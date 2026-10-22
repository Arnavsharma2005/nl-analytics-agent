import pytest

from app.agents.chart import ChartSpec
from app.analyst import Analyst
from app.llm import AllProvidersFailed
from app.replay import CassetteMiss, RecordingLLM, ReplayLLM

from .conftest import FakeLLM
from .test_graph import REVENUE_BY_REGION


def comparable(answer):
    return answer.model_dump(exclude={"timings", "llm_calls"})


def test_replay_reproduces_a_recorded_run(db, tmp_path):
    fake = FakeLLM(sql=["SELECT nope FROM orders", REVENUE_BY_REGION], chart=ChartSpec(type="bar", x="region", y=["revenue"], title="t"))
    recorder = RecordingLLM(fake)
    recorded = Analyst(recorder, db).ask("Revenue by region")
    recorder.save(tmp_path / "c.json")

    replayed = Analyst(ReplayLLM(tmp_path / "c.json"), db).ask("Revenue by region")
    assert recorded.attempts == 2 and recorded.chart.type == "bar"
    assert comparable(replayed) == comparable(recorded)


def test_a_new_prompt_is_a_miss(db, tmp_path):
    recorder = RecordingLLM(FakeLLM(sql=[REVENUE_BY_REGION]))
    Analyst(recorder, db).ask("Revenue by region")
    recorder.save(tmp_path / "c.json")
    with pytest.raises(CassetteMiss):
        Analyst(ReplayLLM(tmp_path / "c.json"), db).ask("Revenue by month")


def test_provider_failures_are_replayed(db, tmp_path):
    class NoSummary(FakeLLM):
        def text(self, system, user):
            raise AllProvidersFailed([{"provider": "x", "error": "rate limited"}])

    recorder = RecordingLLM(NoSummary(sql=[REVENUE_BY_REGION]))
    Analyst(recorder, db).ask("Revenue by region")
    recorder.save(tmp_path / "c.json")
    out = Analyst(ReplayLLM(tmp_path / "c.json"), db).ask("Revenue by region")
    assert out.summary is None and len(out.rows) == 6
