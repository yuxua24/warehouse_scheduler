"""结果对比模块：将实际结果与预期结果对比，计算匹配度。"""

from typing import Dict, List, Optional, Tuple


def soft_match_tasks(actual: List[dict], expected: List[dict]) -> float:
    """软匹配：对比两个任务列表的匹配度。

    不要求精确顺序匹配，允许 LLM 输出顺序不同。
    返回 0.0 ~ 1.0 的匹配度分数。

    Args:
        actual: 实际解析出的任务列表
        expected: 预期的任务列表

    Returns:
        匹配度 (0.0 ~ 1.0)
    """
    if not expected:
        return 1.0 if not actual else 0.0
    if not actual:
        return 0.0

    # 构建 expected 的查找表
    expected_map = {t.get("robot_id", ""): t for t in expected}
    matched = 0
    total_score = 0.0

    for act in actual:
        rid = act.get("robot_id", "")
        exp = expected_map.get(rid)
        if exp is None:
            continue

        matched += 1
        score = 0.0

        # 机器人 ID 匹配 (0.3)
        if act.get("robot_id") == exp.get("robot_id"):
            score += 0.3

        # 目标位置匹配 (0.4)
        if act.get("goal_location_id") == exp.get("goal_location_id"):
            score += 0.4
        elif act.get("goal_name") == exp.get("goal_location_id"):
            score += 0.3

        # 优先级近似 (0.3)
        act_prio = act.get("priority", 1)
        exp_prio = exp.get("priority", 1)
        if act_prio == exp_prio:
            score += 0.3
        elif abs(act_prio - exp_prio) <= 1:
            score += 0.15

        total_score += score

    # 计算最终匹配度
    max_possible = len(expected) * 1.0
    return round(total_score / max_possible, 4) if max_possible > 0 else 0.0


def compare_case_result(
    state,
    expected: dict,
) -> dict:
    """对比单个 case 的实际结果与预期。

    Args:
        state: PlanningState 实例
        expected: 预期的结果描述

    Returns:
        {
            "passed": bool,
            "scores": {...},
            "issues": [str],
        }
    """
    issues = []
    scores = {}

    if state is None:
        return {"passed": False, "scores": {}, "issues": ["No planning state returned"]}

    metrics = getattr(state, "metrics", None)
    status = getattr(state, "status", None)
    status_str = status.value if status else "unknown"

    # 1. 检查最小成功数
    min_success = expected.get("min_success", 1)
    planned_count = metrics.planned_task_count if metrics else 0
    scores["success_count"] = planned_count
    if planned_count < min_success:
        issues.append(
            f"Expected ≥{min_success} success, got {planned_count}"
        )

    # 2. 检查最大重规划次数
    max_replans = expected.get("max_replans", 3)
    retry_count = metrics.retry_count if metrics else 0
    scores["retry_count"] = retry_count
    if retry_count > max_replans:
        issues.append(
            f"Expected ≤{max_replans} replans, got {retry_count}"
        )

    # 3. 检查有无冲突（可选配置）
    if expected.get("check_no_conflicts"):
        current_conflicts = getattr(state, "current_conflicts", [])
        if current_conflicts:
            issues.append(f"Final conflicts exist: {len(current_conflicts)}")

    passed = len(issues) == 0
    return {
        "passed": passed,
        "scores": scores,
        "issues": issues,
        "status": status_str,
    }


def score_result(result: dict) -> str:
    """对单条评测结果评分: PASS / WARN / FAIL / ERROR。

    Args:
        result: compare_case_result 的输出

    Returns:
        "PASS" / "WARN" / "FAIL" / "ERROR"
    """
    if result.get("status") == "error":
        return "ERROR"
    if not result.get("passed", False):
        # 检查是否仅有 WARN 级别的问题
        issues = result.get("issues", [])
        if any("replan" in i for i in issues):
            return "WARN"
        return "FAIL"
    return "PASS"


def compare_baselines(
    latest: dict,
    baseline: dict,
) -> dict:
    """对比最新评测与基线的差异。

    Args:
        latest: 最新评测指标
        baseline: 基线评测指标

    Returns:
        {
            "regressions": [str],    # 退化项
            "improvements": [str],   # 改进项
            "unchanged": [str],      # 不变项
        }
    """
    regressions = []
    improvements = []
    unchanged = []

    # 对比核心指标
    for category in ["planning", "path_quality", "conflict", "performance"]:
        latest_cat = latest.get(category, {})
        baseline_cat = baseline.get(category, {})
        all_keys = set(latest_cat.keys()) | set(baseline_cat.keys())

        for key in all_keys:
            if key not in latest_cat or key not in baseline_cat:
                continue

            latest_val = latest_cat[key]
            baseline_val = baseline_cat[key]

            # 跳过非数值
            if not isinstance(latest_val, (int, float)) or not isinstance(baseline_val, (int, float)):
                continue

            diff = latest_val - baseline_val
            # 根据指标类型判断方向
            higher_is_better_keys = {
                "planning_success_rate", "initial_success_rate",
                "conflict_resolution_rate", "paths_found",
            }
            lower_is_better_keys = {
                "avg_replan_count", "replan_trigger_rate", "partial_execution_rate",
                "total_expanded_nodes", "avg_expanded_nodes",
                "avg_total_time_ms", "p50_time_ms", "p95_time_ms",
                "avg_path_length", "avg_makespan",
            }

            if key in higher_is_better_keys:
                if diff > 0.01:
                    improvements.append(f"{key}: {baseline_val} → {latest_val} (+{diff})")
                elif diff < -0.01:
                    regressions.append(f"{key}: {baseline_val} → {latest_val} ({diff})")
                else:
                    unchanged.append(f"{key}: {latest_val}")
            elif key in lower_is_better_keys:
                if diff < -0.01:
                    improvements.append(f"{key}: {baseline_val} → {latest_val} ({diff})")
                elif diff > 0.01:
                    regressions.append(f"{key}: {baseline_val} → {latest_val} (+{diff})")
                else:
                    unchanged.append(f"{key}: {latest_val}")

    return {
        "regressions": regressions,
        "improvements": improvements,
        "unchanged": unchanged,
    }


def regression_check(latest: dict, baseline: dict, threshold: float = 0.05) -> List[str]:
    """检测是否有退化超过阈值。

    Args:
        latest: 最新评测指标
        baseline: 基线评测指标
        threshold: 退化阈值（默认 5%）

    Returns:
        退化项列表（空列表表示无退化）
    """
    critical_regressions = []

    # 关注核心指标：规划成功率
    latest_rate = latest.get("planning", {}).get("planning_success_rate", 0)
    baseline_rate = baseline.get("planning", {}).get("planning_success_rate", 0)

    if baseline_rate > 0 and latest_rate < baseline_rate - threshold:
        critical_regressions.append(
            f"planning_success_rate dropped from {baseline_rate:.2%} to {latest_rate:.2%}"
        )

    return critical_regressions
