"""指标计算模块：评测指标的计算逻辑。

提供三类指标的计算函数：
  - compute_planning_metrics: 规划成功率相关
  - compute_path_metrics: 路径质量相关
  - compute_perf_metrics: 性能指标相关
"""

from typing import Dict, List, Optional


def compute_planning_metrics(results: List[dict]) -> dict:
    """计算规划成功率指标。

    Args:
        results: 评测结果列表，每项含 case_id, planning_state, comparison 等

    Returns:
        {
            "total_cases": int,
            "total_tasks": int,
            "planned_tasks": int,
            "planning_success_rate": float,
            "initial_success_rate": float,
            "replan_trigger_rate": float,
            "avg_replan_count": float,
            "partial_execution_rate": float,
        }
    """
    total_cases = len(results)
    total_tasks = 0
    planned_tasks = 0
    initial_success_tasks = 0
    replan_count_total = 0
    partial_cases = 0
    zero_replan_cases = 0

    for r in results:
        state = r.get("planning_state")
        if state is None:
            continue

        # 从 PlanningState 提取指标
        metrics = getattr(state, "metrics", None)
        if metrics:
            total_tasks += metrics.total_task_count
            planned_tasks += metrics.planned_task_count
            replan_count_total += metrics.retry_count

            # 初次规划成功 = 0 次重规划且成功
            if metrics.retry_count == 0 and metrics.planned_task_count == metrics.total_task_count:
                zero_replan_cases += 1
                initial_success_tasks += metrics.total_task_count
            elif metrics.retry_count > 0 and metrics.planned_task_count > 0:
                # 有部分成功也算重规划场景统计
                pass

        # 部分执行场景
        status = getattr(state, "status", None)
        if status and hasattr(status, "value") and status.value == "partially_succeeded":
            partial_cases += 1

    return {
        "total_cases": total_cases,
        "total_tasks": max(total_tasks, 1),
        "planned_tasks": planned_tasks,
        "planning_success_rate": round(planned_tasks / max(total_tasks, 1), 4),
        "initial_success_rate": round(initial_success_tasks / max(total_tasks, 1), 4),
        "replan_trigger_rate": round(
            (total_cases - zero_replan_cases) / max(total_cases, 1), 4
        ),
        "avg_replan_count": round(replan_count_total / max(total_cases, 1), 2),
        "partial_execution_rate": round(partial_cases / max(total_cases, 1), 4),
    }


def compute_path_metrics(results: List[dict]) -> dict:
    """计算路径质量指标。

    Returns:
        {
            "avg_path_length": float,
            "avg_makespan": float,
            "total_expanded_nodes": int,
            "avg_expanded_nodes": float,
            "astar_call_count": int,
        }
    """
    total_path_length = 0
    total_makespan = 0
    total_expanded = 0
    astar_calls = 0
    path_count = 0

    for r in results:
        state = r.get("planning_state")
        if state is None:
            continue

        current_paths = getattr(state, "current_paths", {})
        if not current_paths:
            current_paths = {}

        for robot_id, result in current_paths.items():
            if hasattr(result, "success") and result.success and hasattr(result, "path"):
                path = result.path
                path_len = len(path)
                total_path_length += path_len
                if path:
                    total_makespan += path[-1].time
                path_count += 1

            if hasattr(result, "expanded_nodes"):
                total_expanded += result.expanded_nodes
                astar_calls += 1

        # 也统计 initial_paths 的 expanded_nodes
        initial_paths = getattr(state, "initial_paths", {})
        if initial_paths:
            for robot_id, result in initial_paths.items():
                if hasattr(result, "expanded_nodes"):
                    pass  # 已在 current_paths 中统计

    return {
        "avg_path_length": round(total_path_length / max(path_count, 1), 2),
        "avg_makespan": round(total_makespan / max(path_count, 1), 2),
        "total_expanded_nodes": total_expanded,
        "avg_expanded_nodes": round(total_expanded / max(astar_calls, 1), 2),
        "astar_call_count": astar_calls,
        "paths_found": path_count,
    }


def compute_conflict_metrics(results: List[dict]) -> dict:
    """计算冲突检测与解决指标。

    Returns:
        {
            "total_conflicts_detected": int,
            "cases_with_conflicts": int,
            "cases_resolved": int,
            "conflict_resolution_rate": float,
            "final_conflict_count": int,
        }
    """
    total_conflicts = 0
    cases_with_conflicts = 0
    cases_resolved = 0
    final_conflict_count = 0

    for r in results:
        state = r.get("planning_state")
        if state is None:
            continue

        initial = getattr(state, "initial_conflicts", []) or []
        current = getattr(state, "current_conflicts", []) or []

        if initial:
            cases_with_conflicts += 1
            total_conflicts += len(initial)
            if not current:
                cases_resolved += 1

        final_conflict_count += len(current)

    return {
        "total_conflicts_detected": total_conflicts,
        "cases_with_conflicts": cases_with_conflicts,
        "cases_resolved": cases_resolved,
        "conflict_resolution_rate": round(
            cases_resolved / max(cases_with_conflicts, 1), 4
        ),
        "cases_with_final_conflicts": final_conflict_count,
    }


def compute_perf_metrics(results: List[dict]) -> dict:
    """计算性能指标。

    Returns:
        {
            "avg_total_time_ms": float,
            "max_total_time_ms": float,
            "min_total_time_ms": float,
            "p50_time_ms": float,
            "p95_time_ms": float,
        }
    """
    times = []

    for r in results:
        state = r.get("planning_state")
        if state is None:
            continue
        t = getattr(state, "total_planning_time_ms", 0)
        if t > 0:
            times.append(t)

    if not times:
        return {
            "avg_total_time_ms": 0,
            "max_total_time_ms": 0,
            "min_total_time_ms": 0,
            "p50_time_ms": 0,
            "p95_time_ms": 0,
        }

    sorted_times = sorted(times)
    n = len(sorted_times)

    return {
        "avg_total_time_ms": round(sum(times) / n, 2),
        "max_total_time_ms": round(max(times), 2),
        "min_total_time_ms": round(min(times), 2),
        "p50_time_ms": round(sorted_times[int(n * 0.5)], 2),
        "p95_time_ms": round(sorted_times[int(n * 0.95)], 2),
    }


def compute_all_metrics(results: List[dict]) -> dict:
    """计算全部指标并汇总。"""
    return {
        "planning": compute_planning_metrics(results),
        "path_quality": compute_path_metrics(results),
        "conflict": compute_conflict_metrics(results),
        "performance": compute_perf_metrics(results),
    }
