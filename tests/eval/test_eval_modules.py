"""Unit tests for evaluation modules (metrics, comparators, reporter, runner)."""

import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from app.domain.planning_state import (
    PlanningState, BatchStatus, PlanningMetrics, SingleTaskResult
)
from app.domain.path_models import PathPlanResult, TimedPosition
from app.domain.task_models import RobotTask

from eval.metrics import (
    compute_planning_metrics,
    compute_path_metrics,
    compute_conflict_metrics,
    compute_perf_metrics,
    compute_all_metrics,
)
from eval.comparators import (
    soft_match_tasks,
    compare_case_result,
    score_result,
    compare_baselines,
    regression_check,
)
from eval.reporter import generate_json_report, generate_markdown_report


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

def make_mock_state(
    status=BatchStatus.SUCCEEDED,
    planned=3,
    total=3,
    retry=0,
    paths=None,
    initial_conflicts=None,
    current_conflicts=None,
    time_ms=10.0,
):
    """创建一个模拟的 PlanningState 用于测试。"""
    metrics = PlanningMetrics(
        total_task_count=total,
        planned_task_count=planned,
        planning_failed_task_count=total - planned,
        planning_success_rate=planned / max(total, 1),
        retry_count=retry,
    )

    current_paths = {}
    if paths is None:
        # 生成默认路径
        for i in range(planned):
            rid = f"R{i+1}"
            path = [TimedPosition(x=i*2, y=0, time=t) for t in range(5)]
            current_paths[rid] = PathPlanResult(
                success=True, path=path, cost=len(path),
                expanded_nodes=50,
            )
    else:
        current_paths = paths

    return PlanningState(
        request_id="test-001",
        original_instruction="R1去A，R2去B",
        status=status,
        metrics=metrics,
        current_paths=current_paths,
        initial_conflicts=initial_conflicts or [],
        current_conflicts=current_conflicts or [],
        total_planning_time_ms=time_ms,
    )


def make_fake_result(state, case_id="sc-test", case_name="test", grade="PASS"):
    return {
        "case_id": case_id,
        "case_name": case_name,
        "category": "normal",
        "planning_state": state,
        "comparison": {"passed": grade == "PASS", "issues": [], "scores": {}},
        "grade": grade,
        "elapsed_ms": 10.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_planning_metrics_all_success(self):
        results = [
            make_fake_result(make_mock_state(planned=3, total=3, retry=0)),
            make_fake_result(make_mock_state(planned=2, total=2, retry=0)),
        ]
        m = compute_planning_metrics(results)
        assert m["total_tasks"] == 5
        assert m["planned_tasks"] == 5
        assert m["planning_success_rate"] == 1.0
        assert m["avg_replan_count"] == 0.0

    def test_planning_metrics_partial_fail(self):
        results = [
            make_fake_result(make_mock_state(planned=2, total=3, retry=1)),
            make_fake_result(make_mock_state(planned=1, total=2, retry=2)),
        ]
        m = compute_planning_metrics(results)
        assert m["total_tasks"] == 5
        assert m["planned_tasks"] == 3
        assert m["planning_success_rate"] == 0.6
        assert m["avg_replan_count"] == 1.5

    def test_planning_metrics_empty(self):
        m = compute_planning_metrics([])
        assert m["total_cases"] == 0

    def test_path_metrics(self):
        # 创建有路径的 mock state
        paths = {
            "R1": PathPlanResult(
                success=True,
                path=[TimedPosition(x=0, y=0, time=t) for t in range(5)],
                cost=5, expanded_nodes=50,
            ),
            "R2": PathPlanResult(
                success=True,
                path=[TimedPosition(x=3, y=4, time=t) for t in range(3)],
                cost=3, expanded_nodes=30,
            ),
        }
        state = make_mock_state(planned=2, total=2, paths=paths)
        results = [make_fake_result(state)]
        m = compute_path_metrics(results)
        assert m["paths_found"] == 2
        assert m["avg_path_length"] == 4.0  # (5+3)/2
        # Path R1: times 0,1,2,3,4 -> makespan=4, expanded=50
        # Path R2: times 0,1,2     -> makespan=2, expanded=30
        assert m["avg_makespan"] == 3.0  # (4+2)/2
        assert m["total_expanded_nodes"] == 80

    def test_conflict_metrics_all_resolved(self):
        state = make_mock_state(
            initial_conflicts=[MagicMock(type="vertex")],
            current_conflicts=[],
        )
        results = [make_fake_result(state)]
        m = compute_conflict_metrics(results)
        assert m["cases_with_conflicts"] == 1
        assert m["cases_resolved"] == 1
        assert m["conflict_resolution_rate"] == 1.0

    def test_perf_metrics(self):
        states = [
            make_mock_state(time_ms=100.0),
            make_mock_state(time_ms=200.0),
            make_mock_state(time_ms=300.0),
        ]
        results = [make_fake_result(s) for s in states]
        m = compute_perf_metrics(results)
        assert m["avg_total_time_ms"] == 200.0
        assert m["max_total_time_ms"] == 300.0
        assert m["min_total_time_ms"] == 100.0

    def test_compute_all(self):
        results = [make_fake_result(make_mock_state())]
        m = compute_all_metrics(results)
        assert "planning" in m
        assert "path_quality" in m
        assert "conflict" in m
        assert "performance" in m


# ═══════════════════════════════════════════════════════════════════════════════
# Comparators 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestComparators:
    def test_soft_match_exact(self):
        actual = [
            {"robot_id": "R1", "goal_location_id": "A", "priority": 1},
            {"robot_id": "R2", "goal_location_id": "B", "priority": 2},
        ]
        expected = [
            {"robot_id": "R1", "goal_location_id": "A", "priority": 1},
            {"robot_id": "R2", "goal_location_id": "B", "priority": 2},
        ]
        score = soft_match_tasks(actual, expected)
        assert score == 1.0

    def test_soft_match_partial(self):
        actual = [
            {"robot_id": "R1", "goal_location_id": "A", "priority": 1},
            {"robot_id": "R3", "goal_location_id": "C", "priority": 2},
        ]
        expected = [
            {"robot_id": "R1", "goal_location_id": "A", "priority": 1},
            {"robot_id": "R2", "goal_location_id": "B", "priority": 2},
        ]
        score = soft_match_tasks(actual, expected)
        # R1 matches fully (robot_id+goal+priority = 1.0), R3 doesn't match
        # score = 1.0 / 2.0 = 0.5
        assert score == 0.5

    def test_soft_match_empty_expected(self):
        assert soft_match_tasks([], []) == 1.0
        assert soft_match_tasks([{"robot_id": "R1"}], []) == 0.0

    def test_compare_case_result_pass(self):
        state = make_mock_state(planned=3, total=3, retry=0)
        result = compare_case_result(state, {"min_success": 2, "max_replans": 3})
        assert result["passed"] is True
        assert len(result["issues"]) == 0

    def test_compare_case_result_fail_low_success(self):
        state = make_mock_state(planned=1, total=3, retry=2)
        result = compare_case_result(state, {"min_success": 2, "max_replans": 3})
        assert result["passed"] is False
        assert any("success" in i for i in result["issues"])

    def test_compare_case_result_none_state(self):
        result = compare_case_result(None, {})
        assert result["passed"] is False

    def test_score_result(self):
        assert score_result({"passed": True, "issues": [], "status": "succeeded"}) == "PASS"
        assert score_result({"passed": False, "issues": ["replan"], "status": "partial"}) == "WARN"
        assert score_result({"passed": False, "issues": ["fail"], "status": "failed"}) == "FAIL"
        assert score_result({"status": "error", "passed": False, "issues": []}) == "ERROR"

    def test_compare_baselines(self):
        latest = {
            "planning": {"planning_success_rate": 0.85, "avg_replan_count": 0.5},
            "path_quality": {"avg_path_length": 20.0},
        }
        baseline = {
            "planning": {"planning_success_rate": 0.90, "avg_replan_count": 0.3},
            "path_quality": {"avg_path_length": 18.0},
        }
        diff = compare_baselines(latest, baseline)
        assert len(diff["regressions"]) >= 1  # success_rate dropped
        assert len(diff["improvements"]) == 0

    def test_regression_check(self):
        latest = {"planning": {"planning_success_rate": 0.70}}
        baseline = {"planning": {"planning_success_rate": 0.80}}
        regressions = regression_check(latest, baseline, threshold=0.05)
        assert len(regressions) == 1
        assert "70.00%" in regressions[0]

    def test_regression_check_no_regression(self):
        latest = {"planning": {"planning_success_rate": 0.85}}
        baseline = {"planning": {"planning_success_rate": 0.80}}
        regressions = regression_check(latest, baseline, threshold=0.05)
        assert len(regressions) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Reporter 测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestReporter:
    def test_generate_json_report(self):
        results = [make_fake_result(make_mock_state())]
        metrics = {"planning": {"planning_success_rate": 1.0, "total_tasks": 3}}
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "report.json")
            json_str = generate_json_report(results, metrics, "test", output)
            data = json.loads(json_str)
            assert data["summary"]["pass"] == 1
            assert data["metrics"]["planning"]["planning_success_rate"] == 1.0

    def test_generate_markdown_report(self):
        results = [
            make_fake_result(make_mock_state(planned=3, total=3), grade="PASS"),
            make_fake_result(make_mock_state(planned=1, total=2), grade="FAIL"),
        ]
        metrics = {
            "planning": {"planning_success_rate": 0.8, "total_tasks": 5,
                         "planned_tasks": 4, "initial_success_rate": 0.6,
                         "avg_replan_count": 1.0, "replan_trigger_rate": 0.5,
                         "partial_execution_rate": 0.0},
            "path_quality": {"avg_path_length": 10.0, "avg_makespan": 8.0,
                             "astar_call_count": 5, "total_expanded_nodes": 200,
                             "avg_expanded_nodes": 40, "paths_found": 4},
            "conflict": {"cases_with_conflicts": 1, "conflict_resolution_rate": 1.0,
                         "cases_with_final_conflicts": 0},
            "performance": {"avg_total_time_ms": 50.0, "max_total_time_ms": 100.0,
                            "min_total_time_ms": 10.0, "p50_time_ms": 50.0,
                            "p95_time_ms": 95.0},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "report.md")
            md = generate_markdown_report(results, metrics, "test", output)
            assert "评测报告" in md
            assert "通过率概览" in md
            assert "规划成功率" in md
            assert "路径质量" in md
            assert "逐条结果" in md
            assert os.path.exists(output)


# ═══════════════════════════════════════════════════════════════════════════════
# Runner 测试
# ═══════════════════════════════════════════════════════════════════════════════

