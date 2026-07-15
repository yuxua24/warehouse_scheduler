"""Task parser agent: converts natural language to structured task batch using DeepSeek API (Function Calling)."""

import json
import os

from openai import OpenAI
from app.domain.task_models import RobotTask, TaskBatch
from app.domain.map_models import WarehouseMap
from app.services.location_resolver import LocationResolver
from app.services.robot_registry import RobotRegistry


PARSE_SYSTEM_PROMPT = """你是一个仓储机器人任务解析器。将用户自然语言指令转换为结构化的机器人任务。

## 核心规则

### 空间位置映射
地图是 20×20 网格，坐标原点在左上角，x 向右，y 向下：
- "左上角" → (0,0) 附近
- "右上角" → (19,0) 附近
- "左下角" → (0,19) 附近
- "右下角" → (19,19) 附近
- "中间"、"中心" → (10,10) 附近
- 如果用户指定了具体坐标如 [5,3]，直接使用

### 位置匹配
- goal_location_id 必须从下方【可用位置】中选择
- 如果用户的描述不在列表中（如只说"右上角"），根据空间位置映射找到最近的 entry_cells 位置
- 例如"去右上角"→ 找 entry_cells 坐标最接近(19,0)的位置

### 机器人分配
- 如果用户指定了机器人ID（如R1、R2），严格使用
- 如果用户说"所有机器人"或"全部"，为每个可用机器人生成一个任务
- 如果用户没说哪个机器人，根据指令内容推断（如只说"去充电区"则可为每个空闲机器人生成任务）

### 优先级
- 数字越小优先级越高，默认按出现顺序从1开始递增

### 临时封闭
- "关闭/封闭某通道" → 在 constraints 中生成 closed_corridor
- target_id 填下方【可用通道】中的ID

### 卸货确认
- "卸货完成"、"货物已卸"、"XX卸完" → 在 parse_warnings 中标注，系统会另行处理

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
            aliases_str = ", ".join(loc.aliases) if loc.aliases else "无"
            lines.append(
                f"- location_id: {loc.location_id}, name: {loc.name}, "
                f"aliases: [{aliases_str}], entry_cells: {loc.entry_cells}"
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
                    "分析用户输入，判断意图类型。\n"
                    "schedule=调度机器人（R1去XX）；\n"
                    "cron_create=创建定时任务（含时间+指令）；\n"
                    "cron_list=查看定时任务；\ncron_delete=删除定时；\n"
                    "cron_delete_all=删除全部定时；\ncron_toggle=开关定时；\n"
                    "general_qa=提问仓库信息（机器人在哪、充电区在哪等）；\n"
                    "show_map=显示地图；cargo_done=卸货完成；\n"
                    "robot_move=移动机器人；map_modify=封闭/开放通道"
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
                                "general_qa",
                                "show_map",
                                "cargo_done",
                                "robot_move",
                                "map_modify",
                                "unknown",
                            ],
                            "description": (
                                "schedule=调度机器人；cron_create=创建定时任务；"
                                "cron_list=查看定时任务；cron_delete=删除定时任务；"
                                "cron_delete_all=删除全部定时；cron_toggle=开关定时任务；"
                                "general_qa=提问咨询仓库信息（机器人位置、货架位置等）；"
                                "show_map=显示地图；cargo_done=卸货完成确认；"
                                "robot_move=移动机器人位置；map_modify=修改地图封闭/开放通道"
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
                            "你是仓储调度助手的意图识别器。分析用户输入，判断意图。\n"
                            "规则:\n"
                            "1. 含时间+任务的（每晚十点充电）→ cron_create\n"
                            "2. 查看/列出定时 → cron_list\n"
                            "3. 删除全部定时 → cron_delete_all\n"
                            "4. 删除/禁用/启用单个 → cron_delete/cron_toggle\n"
                            "5. 问仓库信息（在哪/位置/有哪些）→ general_qa\n"
                            "6. 显示/查看地图 → show_map\n"
                            "7. 卸货完成 → cargo_done\n"
                            "8. 移动机器人 → robot_move\n"
                            "9. 封闭/开放通道 → map_modify\n"
                            "10. 调度指令（R1去XX）→ schedule\n"
                            "11. 确认/取消 → 结合上轮设为 confirmed=true/false\n"
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
