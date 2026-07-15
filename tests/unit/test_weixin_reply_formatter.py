"""Unit tests for reply_formatter (WeChat Markdown message formatting)."""

import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _project_root)

import pytest
from app.domain.planning_state import (
    PlanningState,
    BatchStatus,
    SingleTaskResult,
    PlanningMetrics,
    ReplanDecision,
)
from app.domain.task_models import RobotTask
from app.domain.path_models import TimedPosition
from app.channels.weixin.reply_formatter import (
    format_schedule_result,
    format_error,
    HELP_TEXT,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_task(robot_id: str, success: bool, goal: str, path_len: int = 10):
    """构建一个 SingleTaskResult。"""
    task = RobotTask(robot_id=robot_id, start=(0, 0), goal_location_id=goal, priority=1)
    task.selected_goal = (5, 5)

    path = [
        TimedPosition(x=i, y=0, time=i)
        for i in range(path_len)
    ]

    return SingleTaskResult(
        robot_id=robot_id,
        task=task,
        success=success,
        path=path,
        failure_reason=None if success else "起点被阻塞",
        replanned=not success,
    )


def _make_metrics(success_rate: float, time_ms: float, conflicts: int, retries: int):
    return PlanningMetrics(
        total_task_count=2,
        planned_task_count=int(success_rate * 2),
        planning_failed_task_count=2 - int(success_rate * 2),
        planning_success_rate=success_rate,
        total_planning_time_ms=time_ms,
        initial_conflict_count=conflicts,
        final_conflict_count=0,
        retry_count=retries,
    )


# ── Tests ───────────────────────────────────────────────────────────────────


class TestHelpText:
    """帮助文本测试。"""

    def test_help_not_empty(self):
        assert len(HELP_TEXT) > 50

    def test_help_contains_keywords(self):
        assert "调度" in HELP_TEXT
        assert "R1" in HELP_TEXT


class TestFormatError:
    """错误消息格式化测试。"""

    def test_simple_error(self):
        result = format_error("something went wrong")
        assert "❌" in result
        assert "something went wrong" in result


class TestFormatScheduleResult:
    """调度结果格式化测试。"""

    def test_success_format(self):
        """全部成功的调度结果。"""
        tasks = [
            _make_task("R1", True, "装卸区", 15),
            _make_task("R2", True, "货架B", 12),
        ]
        metrics = _make_metrics(1.0, 245.0, 0, 0)
        state = PlanningState(
            request_id="test-123",
            original_instruction="R1前往装卸区",
            status=BatchStatus.SUCCEEDED,
            task_results=tasks,
            metrics=metrics,
        )

        result = format_schedule_result(state)

        assert "✅" in result
        assert "调度成功" in result
        assert "R1" in result
        assert "R2" in result
        assert "装卸区" in result
        assert "货架B" in result
        assert "15" in result
        assert "12" in result
        assert "100%" in result
        assert "245" in result

    def test_partially_successful_format(self):
        """部分成功的调度结果。"""
        tasks = [
            _make_task("R1", True, "装卸区", 10),
            _make_task("R2", False, "货架B", 0),
        ]
        metrics = _make_metrics(0.5, 100.0, 1, 1)
        state = PlanningState(
            request_id="test-456",
            original_instruction="...",
            status=BatchStatus.PARTIALLY_SUCCEEDED,
            task_results=tasks,
            metrics=metrics,
        )

        result = format_schedule_result(state)

        assert "⚠️" in result
        assert "部分成功" in result
        assert "R1" in result
        assert "R2" in result
        assert "起点被阻塞" in result
        assert "50%" in result

    def test_infeasible_format(self):
        """全部失败的调度结果。"""
        tasks = [
            _make_task("R1", False, "装卸区", 0),
        ]
        metrics = _make_metrics(0.0, 50.0, 0, 0)
        state = PlanningState(
            request_id="test-789",
            original_instruction="...",
            status=BatchStatus.INFEASIBLE,
            task_results=tasks,
            metrics=metrics,
            errors=["地图加载失败"],
        )

        result = format_schedule_result(state)

        assert "❌" in result
        assert "调度失败" in result
        assert "R1" in result
        assert "起点被阻塞" in result
        assert "地图加载失败" in result

    def test_no_metrics(self):
        """无指标的调度结果（不应崩溃）。"""
        tasks = [_make_task("R1", True, "充电区", 5)]
        state = PlanningState(
            request_id="test-000",
            original_instruction="...",
            status=BatchStatus.SUCCEEDED,
            task_results=tasks,
            metrics=None,
        )

        result = format_schedule_result(state)
        assert "R1" in result
        assert "充电区" in result
