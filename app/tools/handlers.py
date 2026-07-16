"""工具 handler 实现。

每个 handler 是一个函数 (args: dict) -> str，接收 LLM 填写的参数，
调用现有逻辑，返回结果字符串。
"""

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════════════
# Handler 工厂：创建闭包，捕获运行时依赖
# ═══════════════════════════════════════════════════════════════════════════════


def make_handle_schedule(workflow_fn: Callable) -> Callable:
    """创建 schedule_robots handler。

    接收 LLM 输出的结构化任务数据，直接调用规划引擎（无需二次 LLM 解析）。
    这替换了原来的 classify_intent + parse 两次调用。
    """
    def handle_schedule(args: dict) -> str:
        tasks_raw = args.get("tasks", [])
        constraints = args.get("constraints", [])

        if not tasks_raw:
            return json.dumps(
                {"success": False, "error": "No tasks specified"},
                ensure_ascii=False,
            )

        # 构建 run_structured 所需的 JSON 格式
        structured = {
            "tasks": [],
            "runtime_constraints": [],
        }

        for t in tasks_raw:
            task_entry = {
                "robot_id": t.get("robot_id", ""),
                "goal_location_id": t.get("goal_location_id", ""),
                "priority": t.get("priority", 1),
            }
            # 如果 LLM 提供了起点坐标
            start = t.get("start")
            if start and len(start) == 2:
                task_entry["start"] = start
            structured["tasks"].append(task_entry)

        for c in constraints:
            structured["runtime_constraints"].append({
                "constraint_type": c.get("constraint_type", "closed_corridor"),
                "target_id": c.get("target_id", ""),
                "start_time": c.get("start_time", 0),
                "end_time": c.get("end_time"),
            })

        # 调用规划引擎
        try:
            state = workflow_fn(structured)
            # 构建结果（包含路径数据供前端图片生成）
            task_results_output = []
            for tr in state.task_results:
                goal_name = tr.task.goal_location_id if tr.task else ""
                task_results_output.append({
                    "robot_id": tr.robot_id,
                    "success": tr.success,
                    "goal_name": goal_name,
                    "goal_location_id": tr.task.goal_location_id if tr.task else "",
                    "path": [
                        {"x": p.x, "y": p.y, "time": p.time}
                        for p in tr.path
                    ] if tr.success else [],
                    "makespan": tr.path[-1].time if tr.success and tr.path else 0,
                    "start": list(tr.task.start) if tr.task and tr.task.start else None,
                    "goal": list(tr.task.selected_goal) if tr.task and tr.task.selected_goal else None,
                })

            total = len(state.task_results)
            success = sum(1 for tr in state.task_results if tr.success)
            status_icon = "✅" if success == total else "⚠️" if success > 0 else "❌"

            return json.dumps(
                {
                    "success": True,
                    "data": {
                        "batch_status": state.status.value,
                        "summary": "\n".join(
                            f"{'✅' if tr.success else '❌'} {tr.robot_id}: "
                            f"{tr.task.goal_location_id if tr.task else '?'} "
                            f"({len(tr.path)}步)" if tr.success else f"{'❌'} {tr.robot_id}: failed"
                            for tr in state.task_results
                        ),
                        "success_rate": f"{success}/{total}",
                        "request_id": state.request_id,
                        "tasks": task_results_output,
                    },
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps(
                {"success": False, "error": f"Schedule failed: {e}"},
                ensure_ascii=False,
            )

    return handle_schedule


def make_handle_create_cron(cron_manager: Any) -> Callable:
    """创建 create_cron_job handler。"""
    def handle_create_cron(args: dict) -> str:
        if cron_manager is None:
            return json.dumps({"success": False, "error": "Cron not available"}, ensure_ascii=False)
        name = args.get("name", "定时任务")
        cron_expr = args.get("cron_expr", "")
        instruction = args.get("instruction", "")
        if not cron_expr or not instruction:
            return json.dumps(
                {"success": False, "error": "Need cron_expr and instruction"},
                ensure_ascii=False,
            )
        try:
            job = cron_manager.add_job(name, cron_expr, instruction)
            return json.dumps(
                {"success": True, "data": f"⏰ 已创建「{job.name}」· {cron_expr}"},
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    return handle_create_cron


def make_handle_list_cron(cron_manager: Any) -> Callable:
    """创建 list_cron_jobs handler。"""
    def handle_list_cron(args: dict) -> str:
        if cron_manager is None:
            return json.dumps({"success": False, "error": "Cron not available"}, ensure_ascii=False)
        jobs = cron_manager.list_jobs()
        if not jobs:
            return json.dumps({"success": True, "data": "暂无定时任务"}, ensure_ascii=False)
        lines = ["⏰ 定时任务列表"]
        for j in jobs:
            icon = "🔵" if j.enabled else "⚪"
            lines.append(f"{icon} {j.name} · {j.cron_expr} → {j.instruction[:40]}")
        return json.dumps({"success": True, "data": "\n".join(lines)}, ensure_ascii=False)
    return handle_list_cron


def make_handle_delete_cron(cron_manager: Any) -> Callable:
    """创建 delete_cron_job handler。"""
    def handle_delete_cron(args: dict) -> str:
        if cron_manager is None:
            return json.dumps({"success": False, "error": "Cron not available"}, ensure_ascii=False)
        job_name = args.get("job_name", "")
        if not job_name:
            return json.dumps({"success": False, "error": "Need job_name"}, ensure_ascii=False)
        for j in cron_manager.list_jobs():
            if job_name in j.name:
                cron_manager.remove_job(j.job_id)
                return json.dumps(
                    {"success": True, "data": f"🗑️ 已删除「{j.name}」"},
                    ensure_ascii=False,
                )
        return json.dumps({"success": False, "error": f"未找到「{job_name}」"}, ensure_ascii=False)
    return handle_delete_cron


def make_handle_delete_all_cron(cron_manager: Any) -> Callable:
    """创建 delete_all_cron_jobs handler。"""
    def handle_delete_all_cron(args: dict) -> str:
        if cron_manager is None:
            return json.dumps({"success": False, "error": "Cron not available"}, ensure_ascii=False)
        jobs = cron_manager.list_jobs()
        if not jobs:
            return json.dumps({"success": True, "data": "暂无定时任务"}, ensure_ascii=False)
        count = len(jobs)
        for j in list(jobs):
            cron_manager.remove_job(j.job_id)
        return json.dumps(
            {"success": True, "data": f"🗑️ 已删除全部 {count} 个定时任务"},
            ensure_ascii=False,
        )
    return handle_delete_all_cron


def make_handle_toggle_cron(cron_manager: Any) -> Callable:
    """创建 toggle_cron_job handler。"""
    def handle_toggle_cron(args: dict) -> str:
        if cron_manager is None:
            return json.dumps({"success": False, "error": "Cron not available"}, ensure_ascii=False)
        job_name = args.get("job_name", "")
        enabled = args.get("enabled", True)
        if not job_name:
            return json.dumps({"success": False, "error": "Need job_name"}, ensure_ascii=False)
        for j in cron_manager.list_jobs():
            if job_name in j.name:
                cron_manager.toggle_job(j.job_id, enabled)
                action = "▶️ 已启用" if enabled else "⏸️ 已禁用"
                return json.dumps(
                    {"success": True, "data": f"{action}「{j.name}」"},
                    ensure_ascii=False,
                )
        return json.dumps({"success": False, "error": f"未找到「{job_name}」"}, ensure_ascii=False)
    return handle_toggle_cron


def make_handle_answer_question(answer_fn: Callable) -> Callable:
    """创建 answer_question handler。"""
    async def handle_answer_question_async(args: dict) -> str:
        question = args.get("question", "")
        if not question:
            return json.dumps({"success": False, "error": "No question"}, ensure_ascii=False)
        try:
            result = answer_fn(question)
            if hasattr(result, '__await__'):
                result = await result
            return json.dumps({"success": True, "data": str(result)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    def handle_answer_question_sync(args: dict) -> str:
        question = args.get("question", "")
        if not question:
            return json.dumps({"success": False, "error": "No question"}, ensure_ascii=False)
        try:
            result = answer_fn(question)
            return json.dumps({"success": True, "data": str(result)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # 返回同步版本（异步在调用方处理）
    return handle_answer_question_sync


def make_handle_show_map() -> Callable:
    """创建 show_map handler。"""
    def handle_show_map(args: dict) -> str:
        return json.dumps(
            {"success": True, "data": "🗺️ 当前地图已渲染，请查看左侧画布"},
            ensure_ascii=False,
        )
    return handle_show_map


def make_handle_move_robot(robot_move_fn: Callable) -> Callable:
    """创建 move_robot handler。"""
    def handle_move_robot(args: dict) -> str:
        robot_id = args.get("robot_id", "").strip().upper()
        position = args.get("position", [])
        if not robot_id or len(position) != 2:
            return json.dumps(
                {"success": False, "error": "Need robot_id and position [x, y]"},
                ensure_ascii=False,
            )
        try:
            result = robot_move_fn(robot_id, position)
            return json.dumps({"success": True, "data": str(result)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    return handle_move_robot


def make_handle_modify_map(map_modify_fn: Callable) -> Callable:
    """创建 modify_map handler。"""
    def handle_modify_map(args: dict) -> str:
        action = args.get("action", "")
        corridor_id = args.get("corridor_id", "")
        if not action or not corridor_id:
            return json.dumps(
                {"success": False, "error": "Need action (close/open) and corridor_id"},
                ensure_ascii=False,
            )
        try:
            result = map_modify_fn(action, corridor_id)
            return json.dumps({"success": True, "data": str(result)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    return handle_modify_map


def make_handle_cargo_done(cargo_done_fn: Callable) -> Callable:
    """创建 confirm_cargo_done handler。"""
    def handle_cargo_done(args: dict) -> str:
        robot_id = args.get("robot_id", "")
        try:
            result = cargo_done_fn(robot_id)
            return json.dumps({"success": True, "data": str(result)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    return handle_cargo_done


# ═══════════════════════════════════════════════════════════════════════════════
# 工具 Schema 构建（动态：需要地图上下文）
# ═══════════════════════════════════════════════════════════════════════════════


def build_schedule_tool_schema(
    location_ids: List[str],
    corridor_ids: List[str],
    robot_ids: List[str],
) -> dict:
    """构建 schedule_robots 工具的动态 JSON Schema。

    需要地图中的位置、通道和机器人信息。
    """
    # 构建 robot_id 字段（enum 仅在列表非空时包含）
    robot_id_prop = {
        "type": "string",
        "description": f"机器人ID, 如 {', '.join(robot_ids[:3])}{'等' if len(robot_ids) > 3 else ''}",
    }
    if robot_ids:
        robot_id_prop["enum"] = list(robot_ids)

    # 构建 goal_location_id 字段（enum 仅在列表非空时包含）
    goal_id_prop = {
        "type": "string",
        "description": f"目标位置ID。可用: {', '.join(location_ids) if location_ids else '请参考仓库信息'}",
    }
    if location_ids:
        goal_id_prop["enum"] = list(location_ids)

    return {
        "name": "schedule_robots",
        "description": (
            "调度机器人执行运输任务。当用户指令包含机器人ID和目标位置时使用。"
            "例如'R1去装卸区'、'R2去货架A'、'所有机器人回充电区'。"
            "如果用户说'所有机器人'或'全部'，为每个机器人创建一个任务。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "机器人任务列表。每个任务指定哪个机器人去哪里。"
                                   f"可用机器人: {', '.join(robot_ids) if robot_ids else '无'}。"
                                   f"可用位置: {', '.join(location_ids) if location_ids else '无'}。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "robot_id": robot_id_prop,
                            "goal_location_id": goal_id_prop,
                            "priority": {
                                "type": "integer",
                                "description": "优先级（数字越小优先级越高，默认按顺序从1递增）",
                                "minimum": 1,
                            },
                            "start": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                                "description": "起点坐标 [x, y]。仅当用户明确指定起点时填入",
                            },
                        },
                        "required": ["robot_id", "goal_location_id"],
                    },
                },
                "constraints": {
                    "type": "array",
                    "description": "临时封闭约束（可选）。用户说'关闭北侧通道'之类的指令时填入",
                    "items": {
                        "type": "object",
                        "properties": {
                            "constraint_type": {
                                "type": "string",
                                "enum": ["closed_corridor"],
                                "description": "closed_corridor = 封闭通道",
                            },
                            "target_id": {
                                "type": "string",
                                "description": f"通道ID。可选: {', '.join(corridor_ids) if corridor_ids else '无'}",
                            },
                            "start_time": {"type": "integer", "description": "封闭开始时间（默认0）"},
                            "end_time": {"type": "integer", "description": "封闭结束时间（可选）"},
                        },
                        "required": ["constraint_type", "target_id"],
                    },
                },
            },
            "required": ["tasks"],
        },
    }


def build_tool_definitions(
    registry: "ToolRegistry",
    location_ids: List[str],
    corridor_ids: List[str],
    robot_ids: List[str],
) -> list:
    """构建完整的工具定义列表，包含静态工具和动态工具。

    Args:
        registry: ToolRegistry 实例
        location_ids: 地图中的位置ID列表
        corridor_ids: 地图中的通道ID列表
        robot_ids: 可用机器人ID列表

    Returns:
        OpenAI Function Calling 格式的工具定义列表
    """
    # 先注册静态工具（名称、描述、参数固定）
    # 返回所有已注册工具的定义
    tools = registry.get_openai_tools()

    # 查找并替换 schedule_robots 的动态 schema
    for i, t in enumerate(tools):
        if t["function"]["name"] == "schedule_robots":
            tools[i] = {
                "type": "function",
                "function": build_schedule_tool_schema(
                    location_ids, corridor_ids, robot_ids,
                ),
            }
            break

    return tools


# ═══════════════════════════════════════════════════════════════════════════════
# 统一 SYSTEM_PROMPT（替代 classify_intent + parse 两个 prompt）
# ═══════════════════════════════════════════════════════════════════════════════

UNIFIED_SYSTEM_PROMPT = """你是仓储机器人调度助手。根据用户指令，选择合适的工具并填写参数。

可用的工具列表已提供给你。请判断用户想做什么，选择对应工具并填写结构化参数。

## 工具选择规则
- 用户说调度的话（R1去XX、所有机器人回充电区等）→ 用 schedule_robots
- 用户说定时任务（每晚十点、每天早上等）→ 用 create_cron_job
- 用户说查看定时 → 用 list_cron_jobs
- 用户说删除定时 → 用 delete_cron_job / delete_all_cron_jobs
- 用户说启用/禁用定时 → 用 toggle_cron_job
- 用户问仓库信息（在哪、有什么等）→ 用 answer_question
- 用户说显示地图 → 用 show_map
- 用户说移动机器人 → 用 move_robot
- 用户说封闭/开放通道 → 用 modify_map
- 用户说卸货完成 → 用 confirm_cargo_done

## 注意事项
- schedule_robots 的 tasks 中，goal_location_id 必须从可用的位置列表中选择
- 如果用户说"所有机器人"、"全部"，为每个可用机器人创建一个任务
- 如果用户没说起点，不填 start 字段（系统会自动从运行状态获取）
- 优先级默认按出现顺序从1开始递增，数字越小优先级越高
- 如果用户的指令不明确或信息不足，用 answer_question 询问澄清
"""


def build_system_messages(
    warehouse_context: str = "",
    memory_context: str = "",
) -> list:
    """构建多段 system prompt 消息列表，最大化 prompt 缓存命中。

    将常量部分（UNIFIED_SYSTEM_PROMPT）、低频变化部分（仓库信息）、
    高频变化部分（记忆上下文）拆分为独立的 system 消息。

    LLM 的 prompt 缓存基于 messages 列表前缀的哈希。
    拆分后，前三段中的不变部分可以跨请求复用缓存。

    Returns:
        list[dict]: system message 列表，供 LLM API 使用
    """
    messages = [
        {"role": "system", "content": UNIFIED_SYSTEM_PROMPT},
    ]

    if warehouse_context:
        messages.append({
            "role": "system",
            "content": f"## 仓库信息\n{warehouse_context}",
        })

    if memory_context:
        messages.append({
            "role": "system",
            "content": f"[用户调度习惯参考]\n{memory_context}",
        })

    return messages


# 保留旧的 build_system_prompt 做兼容（新代码建议用 build_system_messages）
def build_system_prompt(
    warehouse_context: str = "",
    memory_context: str = "",
) -> str:
    """构建完整的 system prompt（单字符串版本，兼容旧代码）。"""
    prompt = UNIFIED_SYSTEM_PROMPT

    if warehouse_context:
        prompt += f"\n\n## 仓库信息\n{warehouse_context}"

    if memory_context:
        prompt = f"[用户调度习惯参考]\n{memory_context}\n---\n\n{prompt}"

    return prompt
