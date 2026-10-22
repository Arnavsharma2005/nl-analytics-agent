"""Forecasting a time series a query returned: exponential smoothing, chosen by how well it would have forecast the
series' own recent past.

Three models are fitted: simple exponential smoothing (a level), Holt's damped trend (a level and a trend that
flattens out), and additive Holt-Winters (a level, a trend and a repeating season, given two full seasons of
history). Each is fitted on the series without its last few points and scored on how far off it was on them (the
backtest, as mean absolute percentage error); the best one is refitted on the whole series. The range around the
forecast is an 80% interval from the spread of its one-step errors, widening with the horizon.

Pure Python: a series is at most a few hundred points, and numpy or statsforecast would cost the web tier far more
memory than they save time. Fewer than 12 points is refused: there is too little to tell a trend from noise.
"""

import calendar
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import product
from typing import Literal

MIN_POINTS = 12
Z80 = 1.2816  # two-sided 80% interval
Frequency = Literal["day", "week", "month", "quarter", "year"]
SEASON = {"day": 7, "week": 52, "month": 12, "quarter": 4, "year": 1}
GRID = (0.1, 0.3, 0.5, 0.7, 0.9)


class CannotForecast(ValueError):
    """The series is too short, irregular or not a time series."""


@dataclass(frozen=True)
class Forecast:
    model: str  # "level", "damped trend" or "seasonal"
    frequency: Frequency
    dates: list[date]  # the periods forecast
    values: list[float]
    lower: list[float]
    upper: list[float]
    backtest_mape: float | None  # percent; None when the series has zeros where it was scored
    backtest_points: int


# --- Dates ----------------------------------------------------------------------------------------------------


def frequency(dates: Sequence[date]) -> Frequency:
    """The series' spacing, from the typical gap between consecutive dates."""
    gap = statistics.median((b - a).days for a, b in zip(dates, dates[1:], strict=False))
    for name, low, high in (("day", 1, 1), ("week", 6, 8), ("month", 28, 31), ("quarter", 89, 92),
                            ("year", 365, 366)):
        if low <= gap <= high:
            return name  # type: ignore[return-value]
    raise CannotForecast(f"the dates are not evenly spaced (a typical gap of {gap} days)")


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def next_dates(last: date, freq: Frequency, n: int) -> list[date]:
    step: Callable[[date, int], date] = {
        "day": lambda d, k: d + timedelta(days=k),
        "week": lambda d, k: d + timedelta(weeks=k),
        "month": lambda d, k: _add_months(d, k),
        "quarter": lambda d, k: _add_months(d, 3 * k),
        "year": lambda d, k: _add_months(d, 12 * k),
    }[freq]
    return [step(last, k) for k in range(1, n + 1)]


# --- Models ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fit:
    sse: float
    residuals: list[float]
    predict: Callable[[int], list[float]]  # the next h values


def _ses(y: Sequence[float], alpha: float) -> _Fit:
    level, residuals = y[0], []
    for v in y[1:]:
        residuals.append(v - level)
        level = alpha * v + (1 - alpha) * level
    final = level
    return _Fit(sum(r * r for r in residuals), residuals, lambda h: [final] * h)


def _damped(y: Sequence[float], alpha: float, beta: float, phi: float) -> _Fit:
    level, trend, residuals = y[0], y[1] - y[0], []
    for v in y[1:]:
        guess = level + phi * trend
        residuals.append(v - guess)
        new_level = alpha * v + (1 - alpha) * guess
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    final_level, final_trend = level, trend

    def predict(h: int) -> list[float]:
        out, damp = [], 0.0
        for k in range(1, h + 1):
            damp += phi ** k
            out.append(final_level + damp * final_trend)
        return out

    return _Fit(sum(r * r for r in residuals), residuals, predict)


def _seasonal(y: Sequence[float], m: int, alpha: float, beta: float, gamma: float) -> _Fit:
    # Start from the first two seasons: their means give the trend, and each position's average distance from the
    # trend line through them gives its seasonal effect. The level is where that line stands at the first season's end.
    first, second = statistics.fmean(y[:m]), statistics.fmean(y[m:2 * m])
    trend = (second - first) / m
    centre = (m - 1) / 2
    season = [((y[i] - first - trend * (i - centre)) + (y[i + m] - second - trend * (i - centre))) / 2 for i in range(m)]
    level = first + trend * centre
    residuals = []
    for t in range(m, len(y)):
        s = season[t % m]
        guess = level + trend + s
        residuals.append(y[t] - guess)
        new_level = alpha * (y[t] - s) + (1 - alpha) * (level + trend)
        trend = beta * (new_level - level) + (1 - beta) * trend
        season[t % m] = gamma * (y[t] - new_level) + (1 - gamma) * s
        level = new_level
    n, final_level, final_trend, final_season = len(y), level, trend, list(season)
    return _Fit(sum(r * r for r in residuals), residuals,
                lambda h: [final_level + k * final_trend + final_season[(n + k - 1) % m] for k in range(1, h + 1)])


def _best(fits: Sequence[_Fit]) -> _Fit:
    return min(fits, key=lambda f: f.sse / max(len(f.residuals), 1))


MODELS: dict[str, Callable[[Sequence[float], int], _Fit]] = {
    "level": lambda y, m: _best([_ses(y, a) for a in GRID]),
    "damped trend": lambda y, m: _best([_damped(y, a, b, phi) for a, b, phi in product(GRID, (0.1, 0.3), (0.8, 0.9, 0.98))]),
    "seasonal": lambda y, m: _best([_seasonal(y, m, a, b, g) for a, b, g in product(GRID, (0.1, 0.3), (0.1, 0.3, 0.5))]),
}


def _mape(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    if any(a == 0 for a in actual):
        return None
    return 100 * statistics.fmean(abs(a - p) / abs(a) for a, p in zip(actual, predicted, strict=True))


def _mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    return statistics.fmean(abs(a - p) for a, p in zip(actual, predicted, strict=True))


def forecast(dates: Sequence[date], values: Sequence[float], horizon: int) -> Forecast:
    """Forecast `horizon` periods past the last date."""
    if len(values) < MIN_POINTS:
        raise CannotForecast(f"it needs at least {MIN_POINTS} periods of history, and the query returned "
                             f"{len(values)}")
    freq = frequency(dates)
    m = SEASON[freq]
    held = max(1, min(horizon, len(values) // 4))
    train, test = list(values[:-held]), list(values[-held:])
    candidates = [name for name in MODELS if name != "seasonal" or (m > 1 and len(train) >= 2 * m + 2)]
    scored = {name: MODELS[name](train, m).predict(held) for name in candidates}
    chosen = min(candidates, key=lambda name: _mae(test, scored[name]))

    fit = MODELS[chosen](list(values), m)
    sigma = math.sqrt(statistics.fmean(r * r for r in fit.residuals)) if fit.residuals else 0.0
    point = fit.predict(horizon)
    width = [Z80 * sigma * math.sqrt(k) for k in range(1, horizon + 1)]
    floor = 0.0 if min(values) >= 0 else -math.inf  # a series that never went below zero won't be forecast to
    mape = _mape(test, scored[chosen])
    return Forecast(model=chosen, frequency=freq, dates=next_dates(dates[-1], freq, horizon),
                    values=[max(p, floor) for p in point],
                    lower=[max(p - w, floor) for p, w in zip(point, width, strict=True)],
                    upper=[max(p + w, floor) for p, w in zip(point, width, strict=True)],
                    backtest_mape=None if mape is None else round(mape, 1), backtest_points=held)
