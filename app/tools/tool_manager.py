"""ToolManager: 统一的 LLM 工具调用入口。

将当前的"LLM 分类 → 代码路由 → LLM 解析"模式，
改为"一次 LLM 调用（LLM 自主选择工具）→ 注册表分发"。

使用方法:
    manager = ToolManager(registry, llm_client, model, workflow_fn, cron_manager, ...)
    result = manager.process("R1去装卸区")
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .registry import ToolRegistry
from .handlers import (
    make_handle_schedule,
    make_handle_create_cron,
    make_handle_list_cron,
    make_handle_delete_cron,
    make_handle_delete_all_cron,
    make_handle_toggle_cron,
    make_handle_answer_question,
    make_handle_show_map,
    make_handle_move_robot,
    make_handle_modify_map,
    make_handle_cargo_done,
    build_system_messages,
    build_tool_definitions,
)


class ToolManager:
    """统一的 LLM 工具调用管理器。

    职责：
    1. 注册所有工具到 registry
    2. 构建带地图上下文的工具定义
    3. 解析自然语言 → 单次 LLM 调用 → dispatch
    4. 返回结构化结果

    Args:
        registry: ToolRegistry 实例
        llm_client: OpenAI 兼容的 LLM 客户端
        model: 模型名称
        workflow_fn: 调度函数 (structured_dict -> PlanningState)
        cron_manager: CronManager 实例（可选）
        answer_fn: 问答函数 (text -> str)（可选）
        robot_move_fn: 移动机器人函数 (robot_id, position -> str)（可选）
        map_modify_fn: 地图修改函数 (action, corridor_id -> str)（可选）
        cargo_done_fn: 卸货确认函数 (robot_id -> str)（可选）
    """

    def __init__(
        self,
        registry: ToolRegistry,
        llm_client: Any,
        model: str = "deepseek-chat",
        workflow_fn: Callable = None,
        cron_manager: Any = None,
        answer_fn: Callable = None,
        robot_move_fn: Callable = None,
        map_modify_fn: Callable = None,
        cargo_done_fn: Callable = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ):
        self.registry = registry
        self.llm_client = llm_client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # 运行时依赖
        self.workflow_fn = workflow_fn
        self.cron_manager = cron_manager
        self.answer_fn = answer_fn
        self.robot_move_fn = robot_move_fn
        self.map_modify_fn = map_modify_fn
        self.cargo_done_fn = cargo_done_fn

        # 地图上下文（外部设置）
        self.location_ids: List[str] = []
        self.corridor_ids: List[str] = []
        self.robot_ids: List[str] = []
        self.warehouse_context: str = ""

        # 记忆上下文（外部设置）
        self.memory_context: str = ""

        # 内部状态
        self._handlers_registered = False

    def register_handlers(self) -> None:
        """注册所有工具 handler。"""
        if self._handlers_registered:
            return

        # schedule_robots — 特殊，需要 workflow_fn
        if self.workflow_fn:
            self.registry.register(
                name="schedule_robots",
                description="调度机器人执行运输任务。用户指令包含机器人ID和目标位置时使用",
                parameters={
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "机器人任务列表",
                            "items": {"type": "object"},
                        },
                        "constraints": {"type": "array", "description": "临时封闭约束"},
                    },
                    "required": ["tasks"],
                },
                handler=make_handle_schedule(self.workflow_fn),
            )

        # cron 工具
        if self.cron_manager:
            self.registry.register(
                name="create_cron_job",
                description="创建定时调度任务。用户指令包含时间描述和调度指令时使用",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "定时任务名称"},
                        "cron_expr": {"type": "string", "description": "cron 表达式，如 0 22 * * *"},
                        "instruction": {"type": "string", "description": "定时执行的调度指令"},
                    },
                    "required": ["name", "cron_expr", "instruction"],
                },
                handler=make_handle_create_cron(self.cron_manager),
            )

            self.registry.register(
                name="list_cron_jobs",
                description="列出所有定时任务。用户询问有哪些定时任务时使用",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                handler=make_handle_list_cron(self.cron_manager),
            )

            self.registry.register(
                name="delete_cron_job",
                description="删除指定的定时任务",
                parameters={
                    "type": "object",
                    "properties": {
                        "job_name": {"type": "string", "description": "要删除的任务名称"},
                    },
                    "required": ["job_name"],
                },
                handler=make_handle_delete_cron(self.cron_manager),
            )

            self.registry.register(
                name="delete_all_cron_jobs",
                description="删除全部定时任务",
                parameters={
                    "type": "object",
                    "properties": {},
                },
                handler=make_handle_delete_all_cron(self.cron_manager),
            )

            self.registry.register(
                name="toggle_cron_job",
                description="启用或禁用某个定时任务",
                parameters={
                    "type": "object",
                    "properties": {
                        "job_name": {"type": "string", "description": "任务名称"},
                        "enabled": {"type": "boolean", "description": "true=启用, false=禁用"},
                    },
                    "required": ["job_name", "enabled"],
                },
                handler=make_handle_toggle_cron(self.cron_manager),
            )

        # 问答工具
        if self.answer_fn:
            self.registry.register(
                name="answer_question",
                description="回答用户关于仓库、机器人位置、地图配置等信息的提问",
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "用户的问题"},
                    },
                    "required": ["question"],
                },
                handler=make_handle_answer_question(self.answer_fn),
            )

        # show_map
        self.registry.register(
            name="show_map",
            description="显示当前仓库地图。用户说'显示地图'、'查看地图'时使用",
            parameters={"type": "object", "properties": {}},
            handler=make_handle_show_map(),
        )

        # move_robot
        if self.robot_move_fn:
            self.registry.register(
                name="move_robot",
                description="移动机器人到指定坐标位置",
                parameters={
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string", "description": "机器人ID"},
                        "position": {
                            "type": "array", "items": {"type": "integer"},
                            "minItems": 2, "maxItems": 2,
                            "description": "目标坐标 [x, y]",
                        },
                    },
                    "required": ["robot_id", "position"],
                },
                handler=make_handle_move_robot(self.robot_move_fn),
            )

        # modify_map
        if self.map_modify_fn:
            self.registry.register(
                name="modify_map",
                description="封闭或开放通道。用户说'关闭北侧通道'、'开放南侧通道'时使用",
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["close", "open"], "description": "close=封闭, open=开放"},
                        "corridor_id": {"type": "string", "description": "通道ID"},
                    },
                    "required": ["action", "corridor_id"],
                },
                handler=make_handle_modify_map(self.map_modify_fn),
            )

        # confirm_cargo_done
        if self.cargo_done_fn:
            self.registry.register(
                name="confirm_cargo_done",
                description="确认卸货完成。将机器人状态标记为空闲",
                parameters={
                    "type": "object",
                    "properties": {
                        "robot_id": {"type": "string", "description": "已卸货的机器人ID（可选，不填则标记所有）"},
                    },
                },
                handler=make_handle_cargo_done(self.cargo_done_fn),
            )

        self._handlers_registered = True

    # ── 主入口 ───────────────────────────────────────────────────────────

    def process(self, text: str) -> dict:
        """处理用户输入：一次 LLM 调用，LLM 自主选择工具。

        Args:
            text: 用户自然语言输入

        Returns:
            {
                "tool_name": str,           # LLM 选择的工具名
                "success": bool,             # 执行是否成功
                "data": str | dict,          # 结果数据
                "error": str | None,         # 错误信息
                "llm_time_ms": float,        # LLM 调用耗时
            }
        """
        self.register_handlers()

        # 1. 构建带动态 scheam 的工具定义
        tools = build_tool_definitions(
            self.registry,
            self.location_ids,
            self.corridor_ids,
            self.robot_ids,
        )

        # 2. 构建 system messages（分段式，最大化 prompt 缓存命中）
        system_messages = build_system_messages(
            warehouse_context=self.warehouse_context,
            memory_context=self.memory_context,
        )

        # 3. 一次 LLM 调用，让 LLM 自主选择工具
        t0 = time.time()
        try:
            response = self.llm_client.chat.completions.create(
                model=self.model,
                messages=system_messages + [
                    {"role": "user", "content": text},
                ],
                tools=tools,
                tool_choice="auto",
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            return {
                "tool_name": "",
                "success": False,
                "error": f"LLM API call failed: {e}",
                "llm_time_ms": (time.time() - t0) * 1000,
            }

        llm_time_ms = (time.time() - t0) * 1000

        # 4. 提取 LLM 选择的 tool_call
        message = response.choices[0].message

        if not message.tool_calls:
            # LLM 没有调用工具 → 直接返回文本
            content = message.content or ""
            return {
                "tool_name": "_text_reply",
                "success": True,
                "data": content,
                "llm_time_ms": llm_time_ms,
            }

        tool_call = message.tool_calls[0]
        tool_name = tool_call.function.name

        # 5. 解析参数
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            return {
                "tool_name": tool_name,
                "success": False,
                "error": f"Failed to parse arguments: {e}",
                "llm_time_ms": llm_time_ms,
            }

        # 6. 分发执行
        result_str = self.registry.dispatch(tool_name, args)

        # 7. 解析结果
        try:
            result = json.loads(result_str)
        except json.JSONDecodeError:
            result = {"success": True, "data": result_str}

        return {
            "tool_name": tool_name,
            "success": result.get("success", False),
            "data": result.get("data", ""),
            "error": result.get("error"),
            "llm_time_ms": llm_time_ms,
            "args": args,
        }

    def build_warehouse_context(
        self,
        width: int,
        height: int,
        locations: list,
        corridors: list,
        robots: list,
    ) -> str:
        """构建仓库上下文字符串，注入 system prompt。

        同时保存 self.location_ids / self.corridor_ids / self.robot_ids
        供 build_tool_definitions 动态构建工具 Schema。
        """
        # 保存 IDs 供工具 Schema 动态构建
        self.location_ids = [loc.location_id for loc in locations]
        self.corridor_ids = [c.corridor_id for c in corridors]
        self.robot_ids = [rid for rid, _ in robots]

        lines = []

        # 地图基本信息
        lines.append(f"地图 {width}×{height} 网格，坐标原点在左上角 (0,0)")

        # 位置信息
        lines.append("\n可用位置:")
        for loc in locations:
            aliases = ", ".join(loc.aliases) if hasattr(loc, 'aliases') and loc.aliases else ""
            if aliases:
                lines.append(f"- {loc.location_id} ({loc.name}) 别名: [{aliases}] 入口: {loc.entry_cells}")
            else:
                lines.append(f"- {loc.location_id} ({loc.name}) 入口: {loc.entry_cells}")

        # 通道信息
        if corridors:
            lines.append("\n可用通道:")
            for c in corridors:
                lines.append(f"- {c.corridor_id} ({c.name})")

        # 机器人信息
        lines.append("\n可用机器人:")
        for rid, pos in robots:
            lines.append(f"- {rid} 当前位置: {list(pos)}")

        self.warehouse_context = "\n".join(lines)
        return self.warehouse_context

    def set_memory_context(self, context: str) -> None:
        """设置跨会话记忆上下文（方式2注入）。"""
        self.memory_context = context
