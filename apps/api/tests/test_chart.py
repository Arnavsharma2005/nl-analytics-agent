from app.agents.chart import ChartSpec, choose_chart, column_kinds, heuristic_chart, validate_chart

from .conftest import FakeLLM

MONTHLY = (["month", "revenue"], [["2025-01-01", 100.0], ["2025-02-01", 120.0], ["2025-03-01", 90.0]])
BY_REGION = (["region", "revenue"], [["North", 10.0], ["South", 30.0], ["East", 5.0]])


def test_column_kinds():
    cols, rows = MONTHLY
    assert column_kinds(cols, rows) == {"month": "temporal", "revenue": "numeric"}
    assert column_kinds(["year", "n"], [[2024, 1], [2025, 2]])["year"] == "categorical"


def test_heuristics():
    assert heuristic_chart("monthly revenue", *MONTHLY).type == "line"
    assert heuristic_chart("revenue by region", *BY_REGION).type == "bar"
    assert heuristic_chart("revenue share by region", *BY_REGION).type == "pie"
    assert heuristic_chart("total revenue", ["revenue"], [[5.0]]).type == "kpi"
    assert heuristic_chart("list customers", ["name", "email"], [["a", "b"]]).type == "table"
    long = heuristic_chart("monthly revenue by region", ["month", "region", "revenue"],
                           [["2025-01-01", "North", 1.0], ["2025-01-01", "South", 2.0]])
    assert (long.type, long.series) == ("line", "region")


def test_validate_rejects_bad_specs():
    cols, rows = BY_REGION
    assert validate_chart(ChartSpec(type="bar", x="region", y=["revenue"], title="t"), cols, rows) is None
    assert validate_chart(ChartSpec(type="bar", x="region", y=["profit"], title="t"), cols, rows)
    assert validate_chart(ChartSpec(type="bar", x="region", y=["region"], title="t"), cols, rows)
    assert validate_chart(ChartSpec(type="kpi", y=["revenue"], title="t"), cols, rows)


def test_invalid_llm_choice_falls_back():
    llm = FakeLLM(chart=ChartSpec(type="pie", x="nope", y=["revenue"], title="t"))
    spec, source = choose_chart(llm, "revenue by region", *BY_REGION, sample=10)
    assert (spec.type, source) == ("bar", "fallback")


def test_valid_llm_choice_is_kept():
    llm = FakeLLM(chart=ChartSpec(type="pie", x="region", y=["revenue"], title="Share"))
    spec, source = choose_chart(llm, "revenue by region", *BY_REGION, sample=10)
    assert (spec.type, source) == ("pie", "llm")


def test_too_many_series_becomes_a_table():
    rows = [["Cat", f"Product {i}", float(i)] for i in range(12)]
    cols = ["category", "product", "revenue"]
    assert heuristic_chart("top products per category", cols, rows).type == "table"
    spec = ChartSpec(type="bar", x="category", y=["revenue"], series="product", title="t")
    assert validate_chart(spec, cols, rows)
