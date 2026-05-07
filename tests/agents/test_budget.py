from openjarvis.agents._stubs import AgentResult
from openjarvis.agents.executor import AgentExecutor
from openjarvis.agents.manager import AgentManager
from openjarvis.core.events import EventBus, EventType


def test_budget_exceeded_sets_status(tmp_path):
    """Agent exceeding max_cost gets status budget_exceeded."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    bus = EventBus(record_history=True)
    executor = AgentExecutor(mgr, bus)

    agent = mgr.create_agent("expensive", config={"max_cost": 1.0})
    mgr.start_tick(agent["id"])

    result = AgentResult(content="done", metadata={"cost": 1.50, "tokens_used": 100})
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)

    updated = mgr.get_agent(agent["id"])
    assert updated["status"] == "budget_exceeded"

    budget_events = [
        e for e in bus.history if e.event_type == EventType.AGENT_BUDGET_EXCEEDED
    ]
    assert len(budget_events) == 1
    mgr.close()


def test_budget_not_exceeded_stays_idle(tmp_path):
    """Agent under budget stays idle."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    bus = EventBus(record_history=True)
    executor = AgentExecutor(mgr, bus)

    agent = mgr.create_agent("cheap", config={"max_cost": 10.0})
    mgr.start_tick(agent["id"])

    result = AgentResult(content="done", metadata={"cost": 0.50, "tokens_used": 50})
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)

    updated = mgr.get_agent(agent["id"])
    assert updated["status"] == "idle"
    mgr.close()


def test_budget_unlimited_skips_check(tmp_path):
    """max_cost=0 means unlimited — no budget enforcement."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    bus = EventBus()
    executor = AgentExecutor(mgr, bus)

    agent = mgr.create_agent("unlimited", config={"max_cost": 0})
    mgr.start_tick(agent["id"])

    result = AgentResult(
        content="done",
        metadata={"cost": 999.99, "tokens_used": 1000000},
    )
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)

    updated = mgr.get_agent(agent["id"])
    assert updated["status"] == "idle"
    mgr.close()


def test_token_budget_exceeded(tmp_path):
    """Agent exceeding max_tokens gets budget_exceeded."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    bus = EventBus()
    executor = AgentExecutor(mgr, bus)

    agent = mgr.create_agent("token-heavy", config={"max_tokens": 1000})
    mgr.start_tick(agent["id"])

    result = AgentResult(content="done", metadata={"cost": 0.01, "tokens_used": 1500})
    executor._finalize_tick(agent["id"], result, error=None, duration=1.0)

    updated = mgr.get_agent(agent["id"])
    assert updated["status"] == "budget_exceeded"
    mgr.close()


# ---------------------------------------------------------------------------
# Per-day rolling cap (AG-5)
# ---------------------------------------------------------------------------


import pytest  # noqa: E402


def test_daily_cap_unconfigured_is_unbounded(tmp_path):
    """No daily caps set => check_daily_budget returns ok."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a", config={})
    ok, reason = mgr.check_daily_budget(agent["id"])
    assert ok is True
    assert reason is None
    mgr.close()


def test_daily_cap_zero_is_unbounded(tmp_path):
    """max_cost_per_day=0 means unlimited (matches existing 0=unlimited)."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "a",
        config={"max_cost_per_day": 0, "max_tokens_per_day": 0},
    )
    ok, _ = mgr.check_daily_budget(agent["id"])
    assert ok is True
    mgr.close()


def test_daily_token_cap_blocks_when_exceeded(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a", config={"max_tokens_per_day": 100})
    mgr.update_agent(
        agent["id"],
        input_tokens_increment=60,
        output_tokens_increment=50,
    )
    ok, reason = mgr.check_daily_budget(agent["id"])
    assert ok is False
    assert "token" in reason.lower()
    mgr.close()


def test_daily_cost_cap_blocks_when_exceeded(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a", config={"max_cost_per_day": 0.10})
    mgr.update_agent(agent["id"], total_cost_increment=0.15)
    ok, reason = mgr.check_daily_budget(agent["id"])
    assert ok is False
    assert "cost" in reason.lower()
    mgr.close()


def test_daily_cap_under_limit_passes(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent(
        "a",
        config={"max_cost_per_day": 1.0, "max_tokens_per_day": 1000},
    )
    mgr.update_agent(
        agent["id"],
        total_cost_increment=0.5,
        input_tokens_increment=200,
        output_tokens_increment=300,
    )
    ok, reason = mgr.check_daily_budget(agent["id"])
    assert ok is True
    assert reason is None
    mgr.close()


def test_daily_usage_for_returns_zero_initially(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a")
    u = mgr.daily_usage_for(agent["id"])
    assert u["cost"] == 0.0
    assert u["tokens"] == 0
    mgr.close()


def test_daily_usage_accumulates_across_updates(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a")
    mgr.update_agent(
        agent["id"],
        total_cost_increment=0.10,
        input_tokens_increment=100,
        output_tokens_increment=200,
    )
    mgr.update_agent(
        agent["id"],
        total_cost_increment=0.05,
        input_tokens_increment=50,
        output_tokens_increment=50,
    )
    u = mgr.daily_usage_for(agent["id"])
    assert u["cost"] == pytest.approx(0.15)
    assert u["tokens"] == 400
    mgr.close()


def test_daily_usage_isolates_agents(tmp_path):
    mgr = AgentManager(str(tmp_path / "test.db"))
    a1 = mgr.create_agent("a1")
    a2 = mgr.create_agent("a2")
    mgr.update_agent(a1["id"], total_cost_increment=0.1, input_tokens_increment=100)
    mgr.update_agent(a2["id"], total_cost_increment=0.2, input_tokens_increment=200)
    assert mgr.daily_usage_for(a1["id"])["tokens"] == 100
    assert mgr.daily_usage_for(a2["id"])["tokens"] == 200
    assert mgr.daily_usage_for(a1["id"])["cost"] == pytest.approx(0.1)
    assert mgr.daily_usage_for(a2["id"])["cost"] == pytest.approx(0.2)
    mgr.close()


def test_daily_usage_rolls_over_at_day_change(tmp_path):
    """A stale day entry triggers rollover on next read."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    agent = mgr.create_agent("a")
    # Inject a stale entry for an obviously-past day.
    mgr._daily_usage[agent["id"]] = {
        "day": "1999-01-01",
        "cost": 99.99,
        "tokens": 99999,
    }
    u = mgr.daily_usage_for(agent["id"])
    assert u["cost"] == 0.0
    assert u["tokens"] == 0
    mgr.close()


def test_check_daily_budget_unknown_agent_returns_ok(tmp_path):
    """Unknown agents are not gated — caller checks existence separately."""
    mgr = AgentManager(str(tmp_path / "test.db"))
    ok, reason = mgr.check_daily_budget("nonexistent")
    assert ok is True
    assert reason is None
    mgr.close()
