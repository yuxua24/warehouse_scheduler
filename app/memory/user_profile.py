"""UserProfile: 用户偏好模式学习（规则引擎，无 LLM 调用）。

从调度历史中提取用户的隐式偏好：
  - 机器人常用目标（Robot → goal 频率统计）
  - 隐式优先级排序（按出现频率 / 历史优先级分配）
  - 时间段模式（特定时间的常用调度方案）

所有分析基于统计规则，不调用 LLM。
"""

import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .hybrid_store import HybridMemoryStore


class UserProfile:
    """用户偏好模型：从历史记录中学习调度模式。

    Args:
        store: HybridMemoryStore 实例
    """

    def __init__(self, store: HybridMemoryStore):
        self.store = store

        # 统计缓存（惰性加载）
        self._robot_goals: Dict[str, Dict[str, int]] = {}  # robot_id -> {goal: count}
        self._hourly_patterns: Dict[str, Dict[str, int]] = {}  # hour -> {instruction_hash: count}
        self._priority_order: List[str] = []
        self._total_schedules: int = 0
        self._loaded: bool = False

    def load(self) -> None:
        """从历史记录加载并计算统计模式。"""
        data = self.store.get_history(limit=1000)
        self._robot_goals.clear()
        self._hourly_patterns.clear()
        self._total_schedules = len(data)

        priority_counter: Dict[str, int] = defaultdict(int)

        for entry in data:
            status = entry.get("status", "")
            if status not in ("succeeded", "partially_successful"):
                continue

            # 解析时间
            ts_str = entry.get("timestamp", "")
            hour = "00"
            try:
                if ts_str:
                    dt = datetime.fromisoformat(ts_str)
                    hour = f"{dt.hour:02d}"
            except (ValueError, TypeError):
                pass

            # 统计机器人 -> 目标频率
            for task in entry.get("tasks", []):
                rid = task.get("robot_id", "")
                goal = task.get("goal", "")
                success = task.get("success", False)

                if rid and goal and success:
                    if rid not in self._robot_goals:
                        self._robot_goals[rid] = {}
                    self._robot_goals[rid][goal] = self._robot_goals[rid].get(goal, 0) + 1

                # 统计优先级倾向（优先级 1 出现次数越多，优先级越高）
                if rid:
                    priority_counter[rid] += 1

        # 推断优先级顺序（按出现频率降序）
        self._priority_order = sorted(
            priority_counter,
            key=priority_counter.get,
            reverse=True,
        )

        self._loaded = True

    # ── 查询接口 ────────────────────────────────────────────────────────

    def get_favorite_goal(self, robot_id: str) -> Optional[str]:
        """获取机器人的最常用目标。"""
        self._ensure_loaded()
        goals = self._robot_goals.get(robot_id, {})
        if not goals:
            return None
        return max(goals, key=goals.get)

    def get_robot_goal_frequency(self, robot_id: str, goal: str) -> int:
        """获取机器人去某个目标的次数。"""
        self._ensure_loaded()
        return self._robot_goals.get(robot_id, {}).get(goal, 0)

    def get_inferred_priority(self) -> List[str]:
        """获取推断的优先级顺序（按使用频率降序）。"""
        self._ensure_loaded()
        return list(self._priority_order)

    def get_summary(self, max_lines: int = 5) -> str:
        """生成用户偏好摘要（规则引擎，适合注入）。"""
        self._ensure_loaded()
        if self._total_schedules == 0:
            return ""

        parts = []

        # 推断优先级
        if self._priority_order:
            parts.append("优先级: " + " > ".join(self._priority_order))

        # 每个机器人的常用目标（取 top-3 机器人）
        for rid in self._priority_order[:3]:
            goals = self._robot_goals.get(rid, {})
            if goals:
                sorted_goals = sorted(goals, key=goals.get, reverse=True)
                top_goal = sorted_goals[0]
                count = goals[top_goal]
                parts.append(f"{rid}常去{top_goal}({count}次)")

        return " · ".join(parts)

    def get_patterns(self) -> dict:
        """获取完整模式数据（供调试/展示）。"""
        self._ensure_loaded()
        return {
            "total_schedules": self._total_schedules,
            "robot_goals": {
                rid: dict(goals)
                for rid, goals in self._robot_goals.items()
            },
            "inferred_priority": list(self._priority_order),
        }

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()
