"""Unit tests for tool calling system (ToolRegistry, ToolManager, handlers)."""

import json
import pytest
from unittest.mock import MagicMock, patch

from app.tools.registry import ToolRegistry
from app.tools.handlers import (
    build_system_prompt,
    build_system_messages,
    build_schedule_tool_schema,
    build_tool_definitions,
    make_handle_show_map,
    make_handle_list_cron,
    make_handle_create_cron,
    make_handle_delete_cron,
    make_handle_delete_all_cron,
    make_handle_toggle_cron,
    make_handle_schedule,
    make_handle_move_robot,
    make_handle_modify_map,
    make_handle_cargo_done,
    make_handle_answer_question,
)
from app.tools.tool_manager import ToolManager


# ═══════════════════════════════════════════════════════════════════════════════
# ToolRegistry 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    def test_register_and_get_names(self):
        r = ToolRegistry()
        r.register("test_tool", "A test tool", {"type": "object", "properties": {}}, lambda x: "ok")
        assert "test_tool" in r.get_all_names()
        assert len(r.get_all_names()) == 1

    def test_deregister(self):
        r = ToolRegistry()
        r.register("tool_a", "desc", {"type": "object", "properties": {}}, lambda x: "a")
        r.register("tool_b", "desc", {"type": "object", "properties": {}}, lambda x: "b")
        r.deregister("tool_a")
        assert "tool_a" not in r.get_all_names()
        assert "tool_b" in r.get_all_names()

    def test_get_openai_tools_format(self):
        r = ToolRegistry()
        r.register("my_tool", "My description", {
            "type": "object",
            "properties": {
                "param1": {"type": "string"},
            },
            "required": ["param1"],
        }, lambda x: "ok")
        tools = r.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "my_tool"
        assert tools[0]["function"]["description"] == "My description"
        assert "param1" in tools[0]["function"]["parameters"]["properties"]

    def test_dispatch_success(self):
        r = ToolRegistry()
        r.register("greet", "Greet someone", {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }, lambda args: f"Hello {args.get('name', 'world')}!")
        result = r.dispatch("greet", {"name": "R1"})
        # dispatch returns raw string when handler returns str (not dict)
        assert result == "Hello R1!"

    def test_dispatch_unknown_tool(self):
        r = ToolRegistry()
        result = r.dispatch("nonexistent", {})
        data = json.loads(result)
        assert data["success"] is False
        assert "Unknown" in data["error"]

    def test_dispatch_handler_exception(self):
        r = ToolRegistry()
        r.register("failing", "Fails", {"type": "object", "properties": {}},
                    lambda args: (_ for _ in ()).throw(ValueError("oops")))
        result = r.dispatch("failing", {})
        data = json.loads(result)
        assert data["success"] is False
        assert "oops" in data["error"]

    def test_multiple_tools(self):
        r = ToolRegistry()
        r.register("a", "desc", {"type": "object", "properties": {}}, lambda x: "a")
        r.register("b", "desc", {"type": "object", "properties": {}}, lambda x: "b")
        r.register("c", "desc", {"type": "object", "properties": {}}, lambda x: "c")
        assert len(r.get_all_names()) == 3
        assert len(r.get_openai_tools()) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Handler 工厂测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestHandlerFactories:
    def test_handle_show_map(self):
        handler = make_handle_show_map()
        result = json.loads(handler({}))
        assert result["success"] is True
        assert "🗺️" in result["data"]

    def test_handle_create_cron(self):
        cron_mock = MagicMock()
        cron_mock.add_job.return_value = MagicMock(name="nightly", cron_expr="0 22 * * *")
        handler = make_handle_create_cron(cron_mock)
        result = json.loads(handler({"name": "nightly", "cron_expr": "0 22 * * *", "instruction": "R1去充电区"}))
        assert result["success"] is True
        cron_mock.add_job.assert_called_once()

    def test_handle_create_cron_missing_params(self):
        handler = make_handle_create_cron(MagicMock())
        result = json.loads(handler({"name": "test"}))
        assert result["success"] is False

    def test_handle_list_cron(self):
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = [
            MagicMock(name="job1", cron_expr="0 22 * * *", instruction="R1去充电区", enabled=True),
            MagicMock(name="job2", cron_expr="0 8 * * 1-5", instruction="R1去装卸区", enabled=False),
        ]
        handler = make_handle_list_cron(cron_mock)
        result = json.loads(handler({}))
        assert result["success"] is True
        assert "job1" in result["data"]

    def test_handle_list_cron_empty(self):
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = []
        handler = make_handle_list_cron(cron_mock)
        result = json.loads(handler({}))
        assert result["success"] is True
        assert "暂无" in result["data"]

    def test_handle_delete_cron(self):
        from app.scheduler.job_store import CronJob
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = [
            CronJob(job_id="1", name="test_job", cron_expr="0 22 * * *", instruction="test"),
        ]
        handler = make_handle_delete_cron(cron_mock)
        result = json.loads(handler({"job_name": "test_job"}))
        assert result["success"] is True
        cron_mock.remove_job.assert_called_once_with("1")

    def test_handle_delete_cron_not_found(self):
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = []
        handler = make_handle_delete_cron(cron_mock)
        result = json.loads(handler({"job_name": "nonexistent"}))
        assert result["success"] is False

    def test_handle_delete_all_cron(self):
        from app.scheduler.job_store import CronJob
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = [
            CronJob(job_id="1", name="j1", cron_expr="0 22 * * *", instruction="test"),
            CronJob(job_id="2", name="j2", cron_expr="0 8 * * *", instruction="test2"),
        ]
        handler = make_handle_delete_all_cron(cron_mock)
        result = json.loads(handler({}))
        assert result["success"] is True
        assert "2" in str(cron_mock.remove_job.mock_calls)  # both removed

    def test_handle_toggle_cron_enable(self):
        from app.scheduler.job_store import CronJob
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = [
            CronJob(job_id="1", name="test_job", cron_expr="0 22 * * *", instruction="test"),
        ]
        handler = make_handle_toggle_cron(cron_mock)
        result = json.loads(handler({"job_name": "test_job", "enabled": True}))
        assert result["success"] is True
        cron_mock.toggle_job.assert_called_once_with("1", True)

    def test_handle_toggle_cron_disable(self):
        from app.scheduler.job_store import CronJob
        cron_mock = MagicMock()
        cron_mock.list_jobs.return_value = [
            CronJob(job_id="1", name="test_job", cron_expr="0 22 * * *", instruction="test"),
        ]
        handler = make_handle_toggle_cron(cron_mock)
        result = json.loads(handler({"job_name": "test_job", "enabled": False}))
        assert result["success"] is True
        cron_mock.toggle_job.assert_called_once_with("1", False)

    def test_handle_move_robot(self):
        robot_move_mock = MagicMock(return_value="🤖 R1 已移动到 (5,3)")
        handler = make_handle_move_robot(robot_move_mock)
        result = json.loads(handler({"robot_id": "R1", "position": [5, 3]}))
        assert result["success"] is True
        assert "R1" in result["data"]

    def test_handle_move_robot_missing_params(self):
        handler = make_handle_move_robot(MagicMock())
        result = json.loads(handler({"robot_id": "R1"}))
        assert result["success"] is False

    def test_handle_modify_map(self):
        map_modify_mock = MagicMock(return_value="🔒 已封闭「北通道」")
        handler = make_handle_modify_map(map_modify_mock)
        result = json.loads(handler({"action": "close", "corridor_id": "北通道"}))
        assert result["success"] is True

    def test_handle_cargo_done(self):
        cargo_mock = MagicMock(return_value="✅ R1 卸货完成")
        handler = make_handle_cargo_done(cargo_mock)
        result = json.loads(handler({"robot_id": "R1"}))
        assert result["success"] is True

    def test_handle_answer_question(self):
        answer_mock = MagicMock(return_value="R1 在装卸区")
        handler = make_handle_answer_question(answer_mock)
        result = json.loads(handler({"question": "R1在哪？"}))
        assert result["success"] is True
        assert "装卸区" in result["data"]


# ═══════════════════════════════════════════════════════════════════════════════
# ToolManager 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolManager:

    class FakeFunc:
        """代替 MagicMock，解决 name 属性被 MagicMock 构造函数拦截的问题。"""
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class FakeToolCall:
        def __init__(self, name, arguments):
            self.function = TestToolManager.FakeFunc(name, arguments)

    class FakeMessage:
        def __init__(self, tool_calls, content):
            self.tool_calls = tool_calls
            self.content = content

    class FakeChoice:
        def __init__(self, message):
            self.message = message

    @pytest.fixture
    def manager(self):
        registry = ToolRegistry()
        llm_client = MagicMock()

        llm_client.chat.completions.create.return_value = MagicMock(
            choices=[self.FakeChoice(self.FakeMessage(
                tool_calls=[self.FakeToolCall(name="show_map", arguments="{}")],
                content=None,
            ))],
        )
        manager = ToolManager(
            registry=registry,
            llm_client=llm_client,
            model="test-model",
        )
        # Register show_map manually
        registry.register(
            name="show_map",
            description="Show map",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: json.dumps({"success": True, "data": "🗺️ map"}),
        )
        return manager

    def test_process_show_map(self, manager):
        result = manager.process("显示地图")
        assert result["success"] is True
        assert "🗺️" in str(result["data"])

    def test_process_llm_error(self, manager):
        manager.llm_client.chat.completions.create.side_effect = Exception("API Error")
        result = manager.process("test")
        assert result["success"] is False
        assert "API Error" in result["error"]

    def test_tool_not_found(self, manager):
        manager.llm_client.chat.completions.create.return_value = MagicMock(
            choices=[self.FakeChoice(self.FakeMessage(
                tool_calls=[self.FakeToolCall(name="nonexistent_tool", arguments="{}")],
                content=None,
            ))],
        )
        result = manager.process("do something")
        assert result["tool_name"] == "nonexistent_tool"
        assert result["success"] is False

    def test_register_handlers_with_cron(self):
        registry = ToolRegistry()
        cron_mock = MagicMock()
        llm_client = MagicMock()

        manager = ToolManager(
            registry=registry,
            llm_client=llm_client,
            model="test",
            cron_manager=cron_mock,
        )
        manager.register_handlers()
        names = registry.get_all_names()
        # Should have: show_map + 5 cron tools
        assert "show_map" in names
        assert "create_cron_job" in names
        assert "list_cron_jobs" in names
        assert "delete_cron_job" in names
        assert "delete_all_cron_jobs" in names
        assert "toggle_cron_job" in names

    def test_register_handlers_no_cron(self):
        registry = ToolRegistry()
        llm_client = MagicMock()
        manager = ToolManager(
            registry=registry,
            llm_client=llm_client,
            model="test",
        )
        manager.register_handlers()
        names = registry.get_all_names()
        # Should have: show_map only (cron tools not registered)
        assert "show_map" in names
        assert "create_cron_job" not in names

    def test_build_warehouse_context(self):
        registry = ToolRegistry()
        manager = ToolManager(registry=registry, llm_client=MagicMock(), model="test")
        locations = [
            MagicMock(location_id="装卸区", name="装卸区", aliases=["dock"], entry_cells=[(5,5)]),
            MagicMock(location_id="充电区", name="充电区", aliases=[], entry_cells=[(10,10)]),
        ]
        corridors = [
            MagicMock(corridor_id="北通道", name="北侧通道"),
        ]
        robots = [("R1", (0, 0)), ("R2", (1, 1))]

        ctx = manager.build_warehouse_context(20, 20, locations, corridors, robots)
        assert "20×20" in ctx
        assert "装卸区" in ctx
        assert "R1" in ctx
        assert "北通道" in ctx


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 Schema 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolSchemas:
    def test_build_system_prompt(self):
        prompt = build_system_prompt(warehouse_context="20x20 grid", memory_context="")
        assert "schedule_robots" in prompt  # 工具描述在 prompt 里
        assert "20x20" in prompt
        assert "[用户调度习惯参考]" not in prompt

    def test_build_system_prompt_with_memory(self):
        prompt = build_system_prompt(warehouse_context="map", memory_context="R1→装卸区")
        assert "[用户调度习惯参考]" in prompt
        assert "R1→装卸区" in prompt

    def test_build_system_messages_no_context(self):
        """最大前缀不变：无仓库信息/记忆时，只有一条 system message。"""
        msgs = build_system_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert "schedule_robots" in msgs[0]["content"]

    def test_build_system_messages_with_warehouse(self):
        """有仓库信息时：第一条不变（缓存命中），第二条是仓库信息。"""
        msgs = build_system_messages(warehouse_context="20x20 grid\n- 装卸区")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "system"
        assert "20x20" in msgs[1]["content"]
        # 第一条仍然是 UNIFIED_SYSTEM_PROMPT（不变，可命中缓存）
        assert "schedule_robots" in msgs[0]["content"]

    def test_build_system_messages_with_memory(self):
        """有记忆上下文时：第三条是记忆（只有它变化，前两条缓存命中）。"""
        msgs = build_system_messages(warehouse_context="20x20 grid", memory_context="R1常去装卸区")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"  # UNIFIED_SYSTEM_PROMPT（不变）
        assert msgs[1]["role"] == "system"  # 仓库信息（低频变化）
        assert msgs[2]["role"] == "system"  # 记忆上下文（可变化）
        assert "R1常去装卸区" in msgs[2]["content"]

    def test_build_system_messages_prefix_invariant(self):
        """验证第一条消息始终是 UNIFIED_SYSTEM_PROMPT（最大前缀不变）。"""
        msgs_a = build_system_messages(warehouse_context="map_a", memory_context="mem_a")
        msgs_b = build_system_messages(warehouse_context="map_b", memory_context="mem_b")
        # 两条请求的第一条 system 消息完全相同 → 缓存命中
        assert msgs_a[0]["content"] == msgs_b[0]["content"]
        # 第二条不同（仓库信息不同）
        assert msgs_a[1]["content"] != msgs_b[1]["content"]

    def test_build_schedule_tool_schema(self):
        schema = build_schedule_tool_schema(
            location_ids=["装卸区", "充电区"],
            corridor_ids=["北通道", "南通道"],
            robot_ids=["R1", "R2", "R3"],
        )
        assert schema["name"] == "schedule_robots"
        assert "tasks" in schema["parameters"]["properties"]
        assert "constraints" in schema["parameters"]["properties"]
        assert "robot_id" in schema["parameters"]["properties"]["tasks"]["items"]["properties"]

    def test_build_tool_definitions(self):
        registry = ToolRegistry()
        registry.register("show_map", "Show", {"type": "object", "properties": {}}, lambda x: "ok")

        tools = build_tool_definitions(registry, ["装卸区"], ["北通道"], ["R1"])
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "show_map"

    def test_build_tool_definitions_with_schedule(self):
        registry = ToolRegistry()
        registry.register("show_map", "Show", {"type": "object", "properties": {}}, lambda x: "ok")

        # Register schedule_robots with static schema first
        registry.register(
            "schedule_robots", "Schedule robots",
            {"type": "object", "properties": {"tasks": {"type": "array"}}},
            lambda x: "ok",
        )

        tools = build_tool_definitions(registry, ["装卸区"], ["北通道"], ["R1", "R2"])
        assert len(tools) == 2

        # schedule_robots schema should be replaced with dynamic one
        schedule_tool = next(t for t in tools if t["function"]["name"] == "schedule_robots")
        params = schedule_tool["function"]["parameters"]
        assert "tasks" in params["properties"]
        # Check that enum values contain our robot_ids
        robot_enum = params["properties"]["tasks"]["items"]["properties"]["robot_id"].get("enum")
        assert robot_enum == ["R1", "R2"]
