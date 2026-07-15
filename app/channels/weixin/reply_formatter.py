"""将调度结果格式化为微信 Markdown 消息。"""

from typing import Dict, Optional
from app.domain.planning_state import PlanningState, BatchStatus


HELP_TEXT = """🤖 **仓储调度助手**

发送自然语言指令即可调度机器人：

**示例：**
• `R1前往装卸区，R2前往货架B，R3前往充电区`
• `关闭北侧通道，R1从[0,0]前往装卸区`

**可用位置：** 装卸区、充电区、货架A/B/C
**可用通道：** 北侧通道、南侧通道、中间通道

发送 **帮助** 可重新查看此说明"""


def _location_name(location_id: str, names: Dict[str, str]) -> str:
    """将 location_id 转为中文名称，找不到则返回原 id。"""
    return names.get(location_id, location_id)


def format_schedule_result(
    state: PlanningState,
    location_names: Optional[Dict[str, str]] = None,
) -> str:
    """将 PlanningState 格式化为微信 Markdown 消息。

    Args:
        state: 调度结果
        location_names: location_id → 中文名称 映射（如 {"loading_dock_north": "北装卸区"}）
    """
    if location_names is None:
        location_names = {}

    status = state.status

    if status == BatchStatus.SUCCEEDED:
        emoji = "✅"
        title = "调度成功"
    elif status == BatchStatus.PARTIALLY_SUCCEEDED:
        emoji = "⚠️"
        title = "部分成功"
    else:
        emoji = "❌"
        title = "调度失败"

    lines = [f"{emoji} **{title}**"]
    lines.append("━" * 16)

    for tr in state.task_results:
        if tr.success:
            steps = len(tr.path)
            makespan = tr.path[-1].time if tr.path else 0
            goal_raw = tr.task.goal_location_id if tr.task else "?"
            goal_cn = _location_name(goal_raw, location_names)

            # 起点坐标
            start = tr.task.start if tr.task else None
            start_str = f"({start[0]},{start[1]})" if start else "?"

            lines.append(
                f"🤖 **{tr.robot_id}** {start_str} → **{goal_cn}**"
                f" · {steps} 步 ({makespan}t)"
            )
        else:
            reason = tr.failure_reason or "未知错误"
            lines.append(f"❌ **{tr.robot_id}**: {reason}")

    lines.append("━" * 16)

    if state.metrics:
        rate = state.metrics.planning_success_rate
        ms = state.metrics.total_planning_time_ms
        conflicts = state.metrics.final_conflict_count
        retries = state.metrics.retry_count
        a_star = state.metrics.astar_call_count
        nodes = state.metrics.total_expanded_nodes

        lines.append(
            f"📊 成功率 **{rate:.0%}** · 耗时 {ms:.0f}ms · "
            f"A*×{a_star} · 展开 {nodes} 节点"
        )
        if conflicts > 0 or retries > 0:
            lines.append(f"⚡ 初始冲突 {conflicts} 次 · 重规划 {retries} 次")

    if state.errors:
        lines.append("")
        for err in state.errors[:3]:
            lines.append(f"⚠️ {err}")

    if state.warnings:
        for warn in state.warnings[:2]:
            lines.append(f"💡 {warn}")

    return "\n".join(lines)


def format_error(message: str) -> str:
    """格式化为错误消息。"""
    return f"❌ {message}"
