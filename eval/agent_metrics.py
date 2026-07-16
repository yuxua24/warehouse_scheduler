"""Agent 评测指标计算：工具选择、参数质量、无效输入、延迟、Token。"""

from typing import Dict, List
from collections import Counter, defaultdict


def compute_tool_accuracy(results: List[dict]) -> dict:
    """计算工具选择准确率 + 混淆矩阵。

    Returns:
        {
            "overall_accuracy": float,
            "per_tool": {tool: {total, correct, accuracy}},
            "confusion_matrix": {actual: {expected: count}},
        }
    """
    per_tool = defaultdict(lambda: {"total": 0, "correct": 0})
    confusion = defaultdict(lambda: defaultdict(int))
    total = len(results)
    correct = 0

    for r in results:
        actual = r.get("actual_tool", "")
        expected = r.get("expected_tool", "")
        is_correct = r.get("tool_correct", False)

        per_tool[expected]["total"] += 1
        if is_correct:
            per_tool[expected]["correct"] += 1
            correct += 1

        confusion[actual][expected] += 1

    # 计算每个工具的准确率
    for tool in per_tool:
        t = per_tool[tool]
        t["accuracy"] = round(t["correct"] / max(t["total"], 1), 4)

    return {
        "overall_accuracy": round(correct / max(total, 1), 4),
        "total_cases": total,
        "correct_cases": correct,
        "per_tool": dict(per_tool),
        "confusion_matrix": {a: dict(e) for a, e in confusion.items()},
    }


def compute_param_quality(results: List[dict]) -> dict:
    """计算参数填充质量指标。

    Returns:
        {
            "completeness": float,    # 必需参数全部填写的比例
            "exact_match_rate": float, # 参数与预期完全一致的比例
        }
    """
    total = len(results)
    complete_count = 0
    exact_match_count = 0

    for r in results:
        comparison = r.get("comparison", {})
        param_result = comparison.get("params", {})

        if param_result.get("all_required_filled", False):
            complete_count += 1
        if param_result.get("exact_match", False):
            exact_match_count += 1

    return {
        "completeness": round(complete_count / max(total, 1), 4),
        "exact_match_rate": round(exact_match_count / max(total, 1), 4),
        "complete_count": complete_count,
        "exact_match_count": exact_match_count,
        "total_cases": total,
    }


def compute_edge_behavior(results: List[dict]) -> dict:
    """计算无效输入处理指标。

    Returns:
        {
            "correct_rejection_rate": float,
            "per_category": {cat: {total, correct, rate}},
        }
    """
    by_category = defaultdict(lambda: {"total": 0, "correct": 0})
    total = 0
    correct = 0

    for r in results:
        comparison = r.get("comparison", {})
        edge_result = comparison.get("edge_behavior", {})
        category = r.get("category", "unknown")

        by_category[category]["total"] += 1
        total += 1

        if edge_result.get("handled_correctly", False):
            by_category[category]["correct"] += 1
            correct += 1

    for cat in by_category:
        c = by_category[cat]
        c["rate"] = round(c["correct"] / max(c["total"], 1), 4)

    return {
        "correct_rejection_rate": round(correct / max(total, 1), 4),
        "total_cases": total,
        "correct_cases": correct,
        "per_category": dict(by_category),
    }


def compute_latency_metrics(results: List[dict]) -> dict:
    """计算延迟指标：平均、P50、P95、最大、最小。"""
    latencies = [r.get("latency_ms", 0) for r in results if r.get("latency_ms", 0) > 0]

    if not latencies:
        return {
            "avg_ms": 0, "p50_ms": 0, "p95_ms": 0,
            "max_ms": 0, "min_ms": 0, "sample_count": 0,
        }

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    return {
        "avg_ms": round(sum(latencies) / n, 2),
        "p50_ms": round(sorted_lat[int(n * 0.5)], 2),
        "p95_ms": round(sorted_lat[int(n * 0.95)], 2),
        "max_ms": round(max(latencies), 2),
        "min_ms": round(min(latencies), 2),
        "sample_count": n,
    }


def compute_token_metrics(results: List[dict]) -> dict:
    """计算 Token 消耗指标。"""
    total_input = 0
    total_output = 0
    total_cached = 0
    count = 0

    for r in results:
        tokens = r.get("tokens", {})
        total_input += tokens.get("input_tokens", 0)
        total_output += tokens.get("output_tokens", 0)
        total_cached += tokens.get("cached_tokens", 0)
        count += 1

    total_all = total_input + total_output

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_all,
        "avg_input_tokens": round(total_input / max(count, 1), 2),
        "avg_output_tokens": round(total_output / max(count, 1), 2),
        "avg_total_tokens": round(total_all / max(count, 1), 2),
        "cached_tokens": total_cached,
        "cache_hit_rate": round(total_cached / max(total_input, 1), 4),
        "sample_count": count,
    }


def compute_agent_metrics(results: List[dict]) -> dict:
    """计算全部 Agent 评测指标。"""
    return {
        "tool_accuracy": compute_tool_accuracy(results),
        "param_quality": compute_param_quality(results),
        "edge_behavior": compute_edge_behavior(results),
        "latency": compute_latency_metrics(results),
        "token": compute_token_metrics(results),
    }
