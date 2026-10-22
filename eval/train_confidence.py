"""Train the confidence model from eval runs, and report how well it separates right answers from wrong ones.

Usage (from the repo root):
    python eval/train_confidence.py eval/results/2026-10-23-0900-abc1234.json [more runs...]
    python eval/train_confidence.py RUNS... --dry-run    # report only; don't write the model

The runs must record signals (eval/run_eval.py does from v3.0 on). The model learns from the dev questions only and
is scored on the held-out test questions: AUROC (0.5 is a coin toss, 1.0 separates them perfectly), calibration
error, and how often answers labelled High, Medium and Low were right. It is written to
apps/api/models/confidence.json, which the app loads on start. The roadmap's bar before the label is shown to
anyone: report these held-out figures, then check calibration on live answers in shadow mode.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.confidence import MODEL_PATH, Signals, auroc, calibration_error, save_model, train  # noqa: E402

MIN_TRAINING = 30


def answers(paths: list[Path]) -> tuple[list[tuple[Signals, bool]], list[tuple[Signals, bool]], list[str]]:
    """(signals, correct) for each answered data question, split into dev and test, and the runs' commits."""
    dev, test, commits = [], [], []
    for path in paths:
        run = json.loads(path.read_text(encoding="utf-8"))
        commits.append(run["summary"].get("commit", "unknown"))
        for r in run["results"]:
            if not r.get("signals"):
                continue  # not a data answer, it failed outright, or the run predates signals
            (test if r["split"] == "test" else dev).append((Signals.model_validate(r["signals"]), bool(r["correct"])))
    return dev, test, commits


def by_label(model, rows) -> dict[str, str]:
    out = {}
    for label in ("high", "medium", "low"):
        group = [c for s, c in rows if model.assess(s).label == label]
        out[label] = f"{sum(group)}/{len(group)} right" if group else "none"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="report only; don't write the model")
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
    args = parser.parse_args()

    dev, test, commits = answers(args.runs)
    if len(dev) < MIN_TRAINING or len({c for _, c in dev}) < 2:
        print(f"Need at least {MIN_TRAINING} dev answers with both right and wrong ones; have {len(dev)} "
              f"({sum(c for _, c in dev)} right). Run the full set (python eval/run_eval.py) first.", file=sys.stderr)
        return 1

    version = f"{datetime.now(UTC):%Y-%m-%d}-{'+'.join(sorted(set(commits)))}"
    model = train([s for s, _ in dev], [c for _, c in dev], version=version)

    y = [int(c) for _, c in test]
    raw = [model.score(s) for s, _ in test]
    calibrated = [model.assess(s).probability for s, _ in test]
    report = {
        "trained_on": {"runs": [p.name for p in args.runs], "dev_answers": len(dev),
                       "dev_right": sum(c for _, c in dev)},
        "held_out": {"answers": len(test), "right": sum(y), "auroc": auroc(raw, y),
                     "calibration_error": calibration_error(calibrated, y) if test else None,
                     "by_label": by_label(model, test)},
    }
    model = model.model_copy(update={"report": report})

    print(f"Trained on {len(dev)} dev answers ({sum(c for _, c in dev)} right).")
    if test:
        held = report["held_out"]
        print(f"Held out: {len(test)} answers, AUROC {held['auroc']}, calibration error {held['calibration_error']}")
        for label, outcome in held["by_label"].items():
            print(f"  {label:>6}: {outcome}")
    else:
        print("No held-out answers in these runs, so nothing to score it on.")
    if args.dry_run:
        return 0
    save_model(model, args.out)
    print(f"Wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
