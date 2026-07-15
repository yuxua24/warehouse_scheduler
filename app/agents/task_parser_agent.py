"""Task parser agent: converts natural language to structured task batch using DeepSeek API (Function Calling)."""

import json
import os

from openai import OpenAI
from app.domain.task_models import RobotTask, TaskBatch
from app.domain.map_models import WarehouseMap
from app.services.location_resolver import LocationResolver
from app.services.robot_registry import RobotRegistry


PARSE_SYSTEM_PROMPT = """你是一个仓储机器人任务解析器。你的任务是将用户自然语言指令转换为结构化的机器人任务和临时封闭约束。

## 规则
1. **robot_id** 必须使用用户指定的ID（如R1, R2, R3），如果用户没指定，按出现顺序分配R1, R2...
2. **start** 坐标：如果用户明确指定了起点坐标（如"左上角"对应[0,0]），请填写。如果没有指定，不要填start字段（系统会用当前位置补全）。
3. **goal_location_id** 必须使用下面列出的location_id，根据用户提到的目标（如"装卸区"、"充电区"、"货架A"）匹配对应的ID。不要编造不存在的ID。
4. **priority** 数字越小优先级越高。如果用户指定了优先级就按用户说的，否则按指令中出现顺序（1, 2, 3...）。
5. **临时封闭**：如果用户提到"关闭某通道"或"封闭某区域"，生成对应的constraint。target_id填下方列出的通道ID。
6. 如果用户的指令中有无法理解的内容，放到parse_warnings。如果有严重错误（如不存在的机器人），放到parse_errors。

## 可用位置
{locations_info}

## 可用通道
{corridors_info}

## 可用机器人
{robots_info}
"""


class TaskParserAgent:
    """Uses DeepSeek LLM to parse natural language into structured tasks."""

    def __init__(
        self,
        warehouse_map: WarehouseMap,
        robot_registry: RobotRegistry,
        api_config_path: str = None,
    ):
        self.map = warehouse_map
        self.registry = robot_registry
        self.location_resolver = LocationResolver(warehouse_map)

        if api_config_path is None:
            api_config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "configs",
                "api_config.json",
            )

        with open(api_config_path, "r", encoding="utf-8") as f:
            self.api_config = json.load(f)

        self.client = OpenAI(
            api_key=self.api_config["deepseek_api_key"],
            base_url=self.api_config.get(
                "deepseek_base_url", "https://api.deepseek.com/v1"
            ),
        )
        self.model = self.api_config.get("model", "deepseek-chat")
        self.temperature = self.api_config.get("temperature", 0.1)
        self.max_tokens = self.api_config.get("max_tokens", 2000)

    def _build_tool_schema(self) -> dict:
        """Build the OpenAI-compatible function/tool schema with current map data."""
        location_ids = [loc.location_id for loc in self.map.locations]
        corridor_ids = [corr.corridor_id for corr in self.map.corridors]

        return {
            "type": "function",
            "function": {
                "name": "parse_warehouse_tasks",
                "description": "将用户自然语言指令解析为结构化机器人任务和临时封闭约束",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "description": "机器人任务列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "robot_id": {
                                        "type": "string",
                                        "description": "机器人ID，如 R1、R2、R3",
                                    },
                                    "start": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 2,
                                        "maxItems": 2,
                                        "description": "起点坐标 [x, y]。仅有当用户明确指定了起点时才填入，否则不填此字段",
                                    },
                                    "goal_location_id": {
                                        "type": "string",
                                        "description": "目标位置ID，必须选择下方列出的可用位置之一",
                                    },
                                    "priority": {
                                        "type": "integer",
                                        "description": "优先级，数字越小优先级越高。用户指定了就按用户的，否则按出现顺序从1开始递增",
                                        "minimum": 1,
                                    },
                                },
                                "required": ["robot_id", "goal_location_id"],
                            },
                        },
                        "constraints": {
                            "type": "array",
                            "description": "临时封闭约束列表（如封闭通道、封闭区域）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "constraint_type": {
                                        "type": "string",
                                        "enum": ["closed_corridor", "closed_cells"],
                                        "description": "closed_corridor = 封闭通道, closed_cells = 封闭指定坐标",
                                    },
                                    "target_id": {
                                        "type": "string",
                                        "description": f"通道ID，仅当type为closed_corridor时使用。可选值: {', '.join(corridor_ids)}",
                                    },
                                    "start_time": {
                                        "type": "integer",
                                        "description": "封闭开始时间（默认0）",
                                    },
                                    "end_time": {
                                        "type": "integer",
                                        "description": "封闭结束时间。如果不指定或永久封闭则不填此字段",
                                    },
                                },
                                "required": ["constraint_type"],
                            },
                        },
                        "parse_warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "解析过程中的警告信息（如模糊指令）",
                        },
                        "parse_errors": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "解析错误（如不存在的机器人、未知位置），有错误时整个批次不可用",
                        },
                    },
                    "required": ["tasks"],
                },
            },
        }

    def parse(self, instruction: str) -> TaskBatch:
        """Parse natural language instruction into a TaskBatch via Function Calling."""
        # Build system prompt with actual map data
        locations_info = self._build_locations_info()
        corridors_info = self._build_corridors_info()
        robots_info = self._build_robots_info()

        system_prompt = PARSE_SYSTEM_PROMPT.replace(
            "{locations_info}", locations_info
        ).replace(
            "{corridors_info}", corridors_info
        ).replace(
            "{robots_info}", robots_info
        )

        # Build the tool schema dynamically
        tool_schema = self._build_tool_schema()

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": instruction},
                ],
                tools=[tool_schema],
                tool_choice={
                    "type": "function",
                    "function": {"name": "parse_warehouse_tasks"},
                },
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            return TaskBatch(
                tasks=[],
                parse_errors=[f"LLM API call failed: {str(e)}"],
            )

        # Extract structured arguments from Function Calling response
        message = response.choices[0].message
        if not message.tool_calls:
            return TaskBatch(
                tasks=[],
                parse_errors=["LLM did not return a structured tool call"],
            )

        tool_call = message.tool_calls[0]
        if tool_call.function.name != "parse_warehouse_tasks":
            return TaskBatch(
                tasks=[],
                parse_errors=[
                    f"LLM returned unexpected tool: {tool_call.function.name}"
                ],
            )

        try:
            arguments = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            return TaskBatch(
                tasks=[],
                parse_errors=[
                    f"Failed to parse tool call arguments as JSON: {e}"
                ],
            )

        # Build TaskBatch from structured arguments
        return self._build_batch(arguments)

    def _build_locations_info(self) -> str:
        lines = []
        for loc in self.map.locations:
            lines.append(
                f"- location_id: {loc.location_id}, name: {loc.name}, "
                f"aliases: {loc.aliases}, entry_cells: {loc.entry_cells}"
            )
        return "\n".join(lines)

    def _build_corridors_info(self) -> str:
        lines = []
        for corr in self.map.corridors:
            lines.append(
                f"- corridor_id: {corr.corridor_id}, name: {corr.name}"
            )
        return "\n".join(lines)

    def _build_robots_info(self) -> str:
        lines = []
        for rid in self.registry.get_robot_ids():
            pos = self.registry.get_position(rid)
            lines.append(f"- robot_id: {rid}, current_position: {list(pos)}")
        return "\n".join(lines)
    def _build_batch(self, parsed: dict) -> TaskBatch:
        """Convert parsed JSON into a validated TaskBatch."""
        batch = TaskBatch()
        errors = parsed.get("parse_errors", [])
        warnings = parsed.get("parse_warnings", [])
        batch.parse_errors = list(errors)
        batch.parse_warnings = list(warnings)

        # Process tasks
        seen_priorities = set()
        for i, t_raw in enumerate(parsed.get("tasks", [])):
            robot_id = t_raw.get("robot_id", f"R{i+1}")

            # Get start position
            start_raw = t_raw.get("start")
            if start_raw is None or start_raw == [None, None]:
                # Use runtime position
                pos = self.registry.get_position(robot_id)
                if pos is None:
                    batch.parse_errors.append(
                        f"Robot {robot_id}: unknown and no start specified"
                    )
                    continue
                start = pos
            else:
                if isinstance(start_raw, list) and len(start_raw) == 2:
                    start = tuple(start_raw)
                else:
                    batch.parse_errors.append(
                        f"Robot {robot_id}: invalid start format"
                    )
                    continue

            # Validate start in bounds
            if not (0 <= start[0] < self.map.width and 0 <= start[1] < self.map.height):
                batch.parse_errors.append(
                    f"Robot {robot_id}: start {list(start)} out of bounds"
                )
                continue

            # Get goal
            goal_id = t_raw.get("goal_location_id", "")
            loc = self.location_resolver.resolve(goal_id)
            if loc is None:
                # Try to match by name
                for l in self.map.locations:
                    if l.name == goal_id or goal_id in l.aliases:
                        loc = l
                        goal_id = l.location_id
                        break
            if loc is None:
                batch.parse_errors.append(
                    f"Robot {robot_id}: unknown location '{goal_id}'"
                )
                continue

            # Get priority
            priority = t_raw.get("priority", i + 1)
            if not isinstance(priority, int) or priority < 1:
                priority = i + 1
            if priority in seen_priorities:
                priority = max(seen_priorities) + 1
            seen_priorities.add(priority)

            task = RobotTask(
                robot_id=robot_id,
                start=start,
                goal_location_id=goal_id,
                candidate_goals=list(loc.entry_cells),
                priority=priority,
            )
            batch.tasks.append(task)

        # Process constraints
        for c_raw in parsed.get("constraints", []):
            batch.runtime_constraints.append(c_raw)

        return batch

    # ── 意图分类 ──────────────────────────────────────────────────────────

    def classify_intent(self, text: str) -> dict:
        """对用户输入做意图分类：调度指令 vs 定时任务管理。

        通过 DeepSeek Function Calling 分析用户意图，
        返回结构化意图对象供 message_handler 路由。

        Returns:
            {
                "intent": "schedule" | "cron_create" | "cron_list"
                        | "cron_delete" | "cron_toggle" | "unknown",
                ... (intent-specific params)
            }
        """
        INTENT_TOOL = {
            "type": "function",
            "function": {
                "name": "classify_intent",
                "description": (
                    "分析用户输入，判断是调度机器人执行任务，"
                    "还是管理定时任务（创建/查看/删除/启用/禁用）。"
                    "注意：\"查看定时任务\"、\"输出当前定时任务\"、"
                    "\"有哪些定时任务\" 等都是 cron_list 意图。"
                    "\"每天晚上十点让机器人充电\" 这种含时间描述的调度"
                    "是 cron_create 意图。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": [
                                "schedule",
                                "cron_create",
                                "cron_list",
                                "cron_delete",
                                "cron_delete_all",
                                "cron_toggle",
                                "unknown",
                            ],
                            "description": (
                                "用户意图：schedule=调度机器人执行任务；"
                                "cron_create=创建定时任务（含时间描述）；"
                                "cron_list=查看/列出定时任务；"
                                "cron_delete=删除单个定时任务；"
                                "cron_delete_all=删除全部定时任务（含确认/取消）；"
                                "cron_toggle=启用/禁用定时任务；"
                                "unknown=无法判断"
                            ),
                        },
                        "schedule_instruction": {
                            "type": "string",
                            "description": "如果是 schedule 意图，提取纯调度指令部分",
                        },
                        "cron_name": {
                            "type": "string",
                            "description": "定时任务名称（cron_create/delete/toggle 时使用）",
                        },
                        "cron_expr": {
                            "type": "string",
                            "description": "cron 表达式，如 0 22 * * *（cron_create 时从时间描述推断）",
                        },
                        "cron_instruction": {
                            "type": "string",
                            "description": "定时执行的纯调度指令（cron_create 时使用）",
                        },
                        "target_job_name": {
                            "type": "string",
                            "description": "要操作的任务名称（cron_delete/cron_toggle 时使用）",
                        },
                    },
                    "required": ["intent"],
                },
            },
        }

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个仓储调度助手的意图识别器。"
                            "分析用户输入，判断意图类型。\n"
                            "关键规则：\n"
                            "1. 含时间描述的调度请求（如\"每晚十点\"、\"每天8点\"）是 cron_create\n"
                            "2. \"输出定时任务\"、\"查看定时任务\"、\"定时任务列表\"等是 cron_list\n"
                            "3. \"删除全部定时任务\"、\"删除所有\"、\"清空定时\"是 cron_delete_all\n"
                            "4. \"确认\"、\"是的\"、\"确定\" 在上一轮是危险操作确认时，填 confirmed=true\n"
                            "5. \"取消\"、\"不\"、\"算了\" 在确认场景填 confirmed=false\n"
                            "6. \"删除XX\"（单个名称）是 cron_delete\n"
                            "7. \"禁用XX\"、\"启用XX\"等是 cron_toggle\n"
                            "8. 纯机器人调度指令（如\"R1去装卸区\"）是 schedule\n"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                tools=[INTENT_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {"name": "classify_intent"},
                },
                temperature=0.1,
                max_tokens=500,
            )
        except Exception as e:
            return {"intent": "schedule", "schedule_instruction": text}

        message = response.choices[0].message
        if not message.tool_calls:
            return {"intent": "schedule", "schedule_instruction": text}

        try:
            return json.loads(message.tool_calls[0].function.arguments)
        except json.JSONDecodeError:
            return {"intent": "schedule", "schedule_instruction": text}
