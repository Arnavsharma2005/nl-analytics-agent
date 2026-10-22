from app.evaluation import results_match


def test_ignores_column_names_order_and_extra_columns():
    gold = [["North", 10.0], ["South", 20.0]]
    pred = [[20.0, "South", 5], [10.0, "North", 3]]
    assert results_match(gold, pred)


def test_tolerates_rounding_and_date_formats():
    assert results_match([["2025-01-01", 12.35]], [["2025-01-01T00:00:00", 12.3456]])


def test_row_count_and_values_must_match():
    assert not results_match([["North", 10.0]], [["North", 10.0], ["South", 1.0]])
    assert not results_match([["North", 10.0]], [["North", 11.0]])


def test_order_matters_when_asked():
    gold = [["a", 3.0], ["b", 2.0]]
    assert results_match(gold, [["b", 2.0], ["a", 3.0]], ordered=False)
    assert not results_match(gold, [["b", 2.0], ["a", 3.0]], ordered=True)


def test_gold_questions_run(db):
    from pathlib import Path

    import yaml

    questions = yaml.safe_load((Path(__file__).resolve().parents[3] / "eval" / "questions.yaml").read_text())
    assert [q["id"] for q in questions] == list(range(1, len(questions) + 1))
    assert {q["split"] for q in questions} == {"dev", "test"} and {q["difficulty"] for q in questions} == {"easy", "medium", "hard"}
    assert len({q["question"].lower() for q in questions}) == len(questions)
    for q in questions:
        assert db.run(q["gold_sql"]).rows, f"gold query {q['id']} returned no rows"
