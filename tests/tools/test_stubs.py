"""Tests for tools/_stubs.py — ToolSpec, BaseTool, ToolExecutor."""

from __future__ import annotations

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _EchoTool(BaseTool):
    """Minimal tool that echoes its input."""

    tool_id = "echo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="Echoes input back.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            category="testing",
        )

    def execute(self, **params) -> ToolResult:
        return ToolResult(
            tool_name="echo",
            content=params.get("text", ""),
            success=True,
        )


class _ErrorTool(BaseTool):
    """Tool that always raises."""

    tool_id = "error"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="error", description="Always errors.")

    def execute(self, **params) -> ToolResult:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# ToolSpec tests
# ---------------------------------------------------------------------------


class TestToolSpec:
    def test_defaults(self):
        s = ToolSpec(name="test", description="A test tool.")
        assert s.name == "test"
        assert s.description == "A test tool."
        assert s.parameters == {}
        assert s.category == ""
        assert s.cost_estimate == 0.0
        assert s.requires_confirmation is False

    def test_full_spec(self):
        s = ToolSpec(
            name="calc",
            description="Calculate things.",
            parameters={"type": "object"},
            category="math",
            cost_estimate=0.01,
            latency_estimate=0.5,
            requires_confirmation=True,
            metadata={"version": "1.0"},
        )
        assert s.category == "math"
        assert s.metadata["version"] == "1.0"


# ---------------------------------------------------------------------------
# BaseTool tests
# ---------------------------------------------------------------------------


class TestBaseTool:
    def test_echo_tool_spec(self):
        tool = _EchoTool()
        assert tool.spec.name == "echo"
        assert tool.tool_id == "echo"

    def test_echo_tool_execute(self):
        tool = _EchoTool()
        result = tool.execute(text="hello")
        assert result.content == "hello"
        assert result.success is True

    def test_to_openai_function(self):
        tool = _EchoTool()
        fn = tool.to_openai_function()
        assert fn["type"] == "function"
        assert fn["function"]["name"] == "echo"
        assert fn["function"]["description"] == "Echoes input back."
        assert "properties" in fn["function"]["parameters"]


# ---------------------------------------------------------------------------
# ToolExecutor tests
# ---------------------------------------------------------------------------


class TestToolExecutor:
    def test_execute_success(self):
        executor = ToolExecutor([_EchoTool()])
        call = ToolCall(id="1", name="echo", arguments='{"text":"hi"}')
        result = executor.execute(call)
        assert result.success is True
        assert result.content == "hi"
        assert result.latency_seconds > 0

    def test_execute_unknown_tool(self):
        executor = ToolExecutor([_EchoTool()])
        call = ToolCall(id="1", name="nonexistent", arguments="{}")
        result = executor.execute(call)
        assert result.success is False
        assert "Unknown tool" in result.content

    def test_execute_invalid_json(self):
        executor = ToolExecutor([_EchoTool()])
        call = ToolCall(id="1", name="echo", arguments="not json")
        result = executor.execute(call)
        assert result.success is False
        assert "Invalid arguments JSON" in result.content

    def test_execute_empty_arguments(self):
        executor = ToolExecutor([_EchoTool()])
        call = ToolCall(id="1", name="echo", arguments="")
        result = executor.execute(call)
        assert result.success is True
        assert result.content == ""

    def test_execute_tool_error(self):
        executor = ToolExecutor([_ErrorTool()])
        call = ToolCall(id="1", name="error", arguments="{}")
        result = executor.execute(call)
        assert result.success is False
        assert "boom" in result.content

    def test_available_tools(self):
        executor = ToolExecutor([_EchoTool(), _ErrorTool()])
        specs = executor.available_tools()
        assert len(specs) == 2
        names = {s.name for s in specs}
        assert names == {"echo", "error"}

    def test_get_openai_tools(self):
        executor = ToolExecutor([_EchoTool()])
        tools = executor.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "echo"

    def test_event_bus_integration(self):
        bus = EventBus(record_history=True)
        executor = ToolExecutor([_EchoTool()], bus=bus)
        call = ToolCall(id="1", name="echo", arguments='{"text":"ping"}')
        executor.execute(call)
        events = bus.history
        types = [e.event_type for e in events]
        assert EventType.TOOL_CALL_START in types
        assert EventType.TOOL_CALL_END in types
        # Check start event data
        start = [e for e in events if e.event_type == EventType.TOOL_CALL_START][0]
        assert start.data["tool"] == "echo"
        # Check end event data
        end = [e for e in events if e.event_type == EventType.TOOL_CALL_END][0]
        assert end.data["success"] is True

    def test_event_bus_on_error(self):
        bus = EventBus(record_history=True)
        executor = ToolExecutor([_ErrorTool()], bus=bus)
        call = ToolCall(id="1", name="error", arguments="{}")
        executor.execute(call)
        end = [e for e in bus.history if e.event_type == EventType.TOOL_CALL_END][0]
        assert end.data["success"] is False

    def test_no_bus_works(self):
        executor = ToolExecutor([_EchoTool()])
        call = ToolCall(id="1", name="echo", arguments='{"text":"ok"}')
        result = executor.execute(call)
        assert result.success is True

    def test_empty_executor(self):
        executor = ToolExecutor([])
        assert executor.available_tools() == []
        assert executor.get_openai_tools() == []


# ---------------------------------------------------------------------------
# AG-9 auto-injection of allowed_data_sources
# ---------------------------------------------------------------------------


class _CategoryRecorderTool(BaseTool):
    """Tool that records the params it received under a configurable category."""

    def __init__(self, category: str) -> None:
        self._category = category
        self.received_params: dict = {}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=f"recorder_{self._category}",
            description="Records params it was called with.",
            parameters={"type": "object", "properties": {}},
            category=self._category,
        )

    def execute(self, **params) -> ToolResult:
        self.received_params = dict(params)
        return ToolResult(tool_name=self.spec.name, content="ok", success=True)


class TestToolExecutorAllowedDataSources:
    """AG-9 architectural fix — dispatcher auto-injects the agent's
    data-source allowlist into AG-9-aware tool params, eliminating the
    per-call-site kwarg plumbing that Phases 9/12/13/14 worked around."""

    def test_injects_into_data_category(self):
        tool = _CategoryRecorderTool("data")
        executor = ToolExecutor([tool], allowed_data_sources=["gmail", "slack"])
        executor.execute(ToolCall(id="1", name="recorder_data", arguments="{}"))
        assert tool.received_params.get("allowed_data_sources") == [
            "gmail",
            "slack",
        ]

    def test_injects_into_knowledge_category(self):
        tool = _CategoryRecorderTool("knowledge")
        executor = ToolExecutor([tool], allowed_data_sources=["*"])
        executor.execute(ToolCall(id="1", name="recorder_knowledge", arguments="{}"))
        assert tool.received_params.get("allowed_data_sources") == ["*"]

    def test_does_not_inject_into_other_categories(self):
        tool = _CategoryRecorderTool("utility")
        executor = ToolExecutor([tool], allowed_data_sources=["gmail"])
        executor.execute(ToolCall(id="1", name="recorder_utility", arguments="{}"))
        # The kwarg must NOT be passed to non-AG-9 categories — keeps
        # unrelated tools' param surfaces clean.
        assert "allowed_data_sources" not in tool.received_params

    def test_none_allowlist_means_no_injection(self):
        tool = _CategoryRecorderTool("data")
        # allowed_data_sources defaults to None → legacy path.
        executor = ToolExecutor([tool])
        executor.execute(ToolCall(id="1", name="recorder_data", arguments="{}"))
        assert "allowed_data_sources" not in tool.received_params

    def test_does_not_overwrite_explicit_caller_value(self):
        tool = _CategoryRecorderTool("data")
        executor = ToolExecutor([tool], allowed_data_sources=["dispatcher_default"])
        # Caller passes their own value via tool_call args — must win.
        executor.execute(
            ToolCall(
                id="1",
                name="recorder_data",
                arguments='{"allowed_data_sources":["caller_override"]}',
            )
        )
        assert tool.received_params["allowed_data_sources"] == ["caller_override"]

    def test_empty_allowlist_is_passed_through(self):
        # Empty list = deny-all (Phase 5 semantics). Must reach the tool.
        tool = _CategoryRecorderTool("data")
        executor = ToolExecutor([tool], allowed_data_sources=[])
        executor.execute(ToolCall(id="1", name="recorder_data", arguments="{}"))
        assert tool.received_params.get("allowed_data_sources") == []

    def test_each_call_gets_independent_list_copy(self):
        # The dispatcher copies its list per-call so a tool that mutates
        # the kwarg can't leak state across calls.
        tool = _CategoryRecorderTool("data")
        executor = ToolExecutor([tool], allowed_data_sources=["gmail"])
        executor.execute(ToolCall(id="1", name="recorder_data", arguments="{}"))
        first = tool.received_params["allowed_data_sources"]
        first.append("mutated")  # nosec — we want to test isolation
        executor.execute(ToolCall(id="2", name="recorder_data", arguments="{}"))
        # Second call must still see the original ["gmail"].
        assert tool.received_params["allowed_data_sources"] == ["gmail"]
