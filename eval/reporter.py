"""报告生成模块：将评测结果输出为 JSON 和 Markdown 格式。"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional


def generate_json_report(
    results: List[dict],
    metrics: dict,
    dataset_name: str = "scenario",
    output_path: str = None,
) -> str:
    """生成 JSON 格式的评测报告。

    Args:
        results: 逐条评测结果
        metrics: 汇总指标
        dataset_name: 数据集名称
        output_path: 输出路径（None 则只返回字符串）

    Returns:
        JSON 字符串
    """
    # 提取可序列化的结果摘要
    cases = []
    for r in results:
        state = r.get("planning_state")
        status_str = getattr(getattr(state, 'status', None), 'value', 'N/A') if state else 'ERROR'
        planned = 0
        total_tasks = 0
        replans = 0
        elapsed = 0
        if state and hasattr(state, 'metrics') and state.metrics:
            m = state.metrics
            planned = m.planned_task_count
            total_tasks = m.total_task_count
            replans = m.retry_count
        elapsed = r.get("elapsed_ms", 0)

        cases.append({
            "case_id": r["case_id"],
            "case_name": r.get("case_name", ""),
            "category": r.get("category", ""),
            "grade": r.get("grade", "?"),
            "status": status_str,
            "planned_tasks": planned,
            "total_tasks": total_tasks,
            "retry_count": replans,
            "elapsed_ms": round(elapsed, 1),
            "issues": r.get("comparison", {}).get("issues", []),
        })

    # 计算通过统计
    grades = [c["grade"] for c in cases]
    pass_count = grades.count("PASS")
    warn_count = grades.count("WARN")
    fail_count = grades.count("FAIL")
    error_count = grades.count("ERROR")

    report = {
        "report_meta": {
            "generated_at": datetime.now().isoformat(),
            "dataset": dataset_name,
            "total_cases": len(cases),
        },
        "summary": {
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
            "error": error_count,
            "pass_rate": round(pass_count / max(len(cases), 1), 4),
        },
        "metrics": metrics,
        "cases": cases,
    }

    json_str = json.dumps(report, ensure_ascii=False, indent=2)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"  [INFO] Report saved to {output_path}")

    return json_str


def generate_markdown_report(
    results: List[dict],
    metrics: dict,
    dataset_name: str = "scenario",
    output_path: str = None,
) -> str:
    """生成 Markdown 格式的评测报告。

    Args:
        results: 逐条评测结果
        metrics: 汇总指标
        dataset_name: 数据集名称
        output_path: 输出路径（None 则只返回字符串）

    Returns:
        Markdown 字符串
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构建报告
    lines = []
    lines.append(f"# 评测报告：{dataset_name}")
    lines.append(f"")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"> 总用例数: {len(results)}")
    lines.append(f"")

    # ── 通过率概览 ──
    grades = [r.get("grade", "?") for r in results]
    pass_count = grades.count("PASS")
    warn_count = grades.count("WARN")
    fail_count = grades.count("FAIL")
    error_count = grades.count("ERROR")
    pass_rate = pass_count / max(len(results), 1)

    lines.append(f"## 通过率概览")
    lines.append(f"")
    lines.append(f"| 等级 | 数量 | 占比 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| ✅ PASS | {pass_count} | {pass_rate:.1%} |")
    lines.append(f"| ⚠️ WARN | {warn_count} | {warn_count/max(len(results),1):.1%} |")
    lines.append(f"| ❌ FAIL | {fail_count} | {fail_count/max(len(results),1):.1%} |")
    lines.append(f"| 💥 ERROR | {error_count} | {error_count/max(len(results),1):.1%} |")
    lines.append(f"")

    # ── 核心指标 ──
    lines.append(f"## 核心指标")
    lines.append(f"")

    planning = metrics.get("planning", {})
    lines.append(f"### 规划成功率")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总任务数 | {planning.get('total_tasks', 0)} |")
    lines.append(f"| 规划成功任务数 | {planning.get('planned_tasks', 0)} |")
    lines.append(f"| 路径规划成功率 | **{planning.get('planning_success_rate', 0):.2%}** |")
    lines.append(f"| 初次规划成功率 | {planning.get('initial_success_rate', 0):.2%} |")
    lines.append(f"| 平均重规划次数 | {planning.get('avg_replan_count', 0)} |")
    lines.append(f"| 重规划触发率 | {planning.get('replan_trigger_rate', 0):.2%} |")
    lines.append(f"| 部分执行触发率 | {planning.get('partial_execution_rate', 0):.2%} |")
    lines.append(f"")

    path_q = metrics.get("path_quality", {})
    lines.append(f"### 路径质量")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 平均路径长度 | {path_q.get('avg_path_length', 0)} 步 |")
    lines.append(f"| 平均 Makespan | {path_q.get('avg_makespan', 0)} 时间步 |")
    lines.append(f"| A* 调用次数 | {path_q.get('astar_call_count', 0)} |")
    lines.append(f"| 总展开节点数 | {path_q.get('total_expanded_nodes', 0)} |")
    lines.append(f"| 平均展开节点数 | {path_q.get('avg_expanded_nodes', 0)} |")
    lines.append(f"")

    conflict = metrics.get("conflict", {})
    lines.append(f"### 冲突检测与解决")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 检测到冲突的用例数 | {conflict.get('cases_with_conflicts', 0)} |")
    lines.append(f"| 冲突解决率 | {conflict.get('conflict_resolution_rate', 0):.2%} |")
    lines.append(f"| 存在最终冲突的用例 | {conflict.get('cases_with_final_conflicts', 0)} |")
    lines.append(f"")

    perf = metrics.get("performance", {})
    lines.append(f"### 性能")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 平均耗时 | {perf.get('avg_total_time_ms', 0):.1f} ms |")
    lines.append(f"| 最大耗时 | {perf.get('max_total_time_ms', 0):.1f} ms |")
    lines.append(f"| 最小耗时 | {perf.get('min_total_time_ms', 0):.1f} ms |")
    lines.append(f"| P50 耗时 | {perf.get('p50_time_ms', 0):.1f} ms |")
    lines.append(f"| P95 耗时 | {perf.get('p95_time_ms', 0):.1f} ms |")
    lines.append(f"")

    # ── 逐条结果 ──
    lines.append(f"## 逐条结果")
    lines.append(f"")
    lines.append(f"| ID | 名称 | 分类 | 评分 | 状态 | 成功/总数 | 重规划 | 耗时(ms) |")
    lines.append(f"|----|------|------|------|------|----------|--------|---------|")

    for r in results:
        state = r.get("planning_state")
        status_str = getattr(getattr(state, 'status', None), 'value', 'N/A') if state else 'ERROR'
        grade = r.get("grade", "?")
        icon = "✅" if grade == "PASS" else "⚠️" if grade == "WARN" else "❌"

        planned = 0
        total_tasks = 0
        replans = 0
        if state and hasattr(state, 'metrics') and state.metrics:
            m = state.metrics
            planned = m.planned_task_count
            total_tasks = m.total_task_count
            replans = m.retry_count

        elapsed = r.get("elapsed_ms", 0)

        lines.append(
            f"| {r['case_id']} | {r.get('case_name', '')} "
            f"| {r.get('category', '')} | {icon} {grade} "
            f"| {status_str} | {planned}/{total_tasks} "
            f"| {replans} | {elapsed:.0f} |"
        )

    # 失败详情
    failed = [r for r in results if r.get("grade") in ("FAIL", "ERROR")]
    if failed:
        lines.append(f"")
        lines.append(f"### 失败详情")
        lines.append(f"")
        for r in failed:
            lines.append(f"- **{r['case_id']}**: {r.get('case_name', '')}")
            issues = r.get("comparison", {}).get("issues", [])
            for issue in issues:
                lines.append(f"  - ⚠ {issue}")

    markdown = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"  [INFO] Report saved to {output_path}")

    return markdown
