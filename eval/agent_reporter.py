"""Agent 评测报告生成：Markdown 格式，含混淆矩阵和模型对比。"""

import json
import os
from datetime import datetime
from typing import Dict, List


def generate_agent_report(
    results: List[dict],
    metrics: dict,
    dataset_name: str = "agent",
    model_name: str = "unknown",
    output_path: str = None,
) -> str:
    """生成 Agent 评测报告（Markdown）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    correct = sum(1 for r in results if r.get("tool_correct", False))
    accuracy = correct / max(total, 1)

    lines = []
    lines.append(f"# Agent 评测报告：工具调用")
    lines.append(f"")
    lines.append(f"> 模型: {model_name} | 数据集: {dataset_name} | 总用例: {total}")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"")

    # ── 工具选择准确率 ──
    ta = metrics.get("tool_accuracy", {})
    lines.append(f"## 工具选择准确率")
    lines.append(f"")
    lines.append(f"| 工具名 | 测试数 | 正确数 | 准确率 |")
    lines.append(f"|--------|--------|--------|--------|")
    per_tool = ta.get("per_tool", {})
    for tool_name in sorted(per_tool.keys()):
        t = per_tool[tool_name]
        lines.append(
            f"| {tool_name} | {t['total']} | {t['correct']} "
            f"| {t['accuracy']:.1%} |"
        )
    lines.append(f"| **总计** | **{ta.get('total_cases', 0)}** | **{ta.get('correct_cases', 0)}** | **{ta.get('overall_accuracy', 0):.1%}** |")
    lines.append(f"")

    # ── 混淆矩阵 ──
    lines.append(f"### 混淆矩阵")
    lines.append(f"")
    conf = ta.get("confusion_matrix", {})
    all_tools = sorted(set(
        list(conf.keys()) + [
            k for v in conf.values() for k in v.keys()
        ]
    ))
    header = "| 实际\\期望 | " + " | ".join(all_tools) + " |"
    sep = "|-----------|" + "|".join("---" for _ in all_tools) + "|"
    lines.append(header)
    lines.append(sep)
    for actual_tool in all_tools:
        row = f"| {actual_tool} "
        for expected_tool in all_tools:
            count = conf.get(actual_tool, {}).get(expected_tool, 0)
            row += f"| {count} "
        row += "|"
        lines.append(row)
    lines.append(f"")

    # ── 参数质量 ──
    pq = metrics.get("param_quality", {})
    lines.append(f"## 参数质量")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 参数完整率 | {pq.get('completeness', 0):.1%} |")
    lines.append(f"| 参数精确匹配率 | {pq.get('exact_match_rate', 0):.1%} |")
    lines.append(f"")

    # ── 无效输入处理 ──
    eb = metrics.get("edge_behavior", {})
    lines.append(f"## 无效输入处理")
    lines.append(f"")
    lines.append(f"| 输入类型 | 测试数 | 正确处理 | 正确率 |")
    lines.append(f"|---------|--------|---------|--------|")
    per_cat = eb.get("per_category", {})
    for cat in sorted(per_cat.keys()):
        c = per_cat[cat]
        lines.append(f"| {cat} | {c['total']} | {c['correct']} | {c['rate']:.1%} |")
    lines.append(f"| **总计** | **{eb.get('total_cases', 0)}** | **{eb.get('correct_cases', 0)}** | **{eb.get('correct_rejection_rate', 0):.1%}** |")
    lines.append(f"")

    # ── 延迟 ──
    lat = metrics.get("latency", {})
    lines.append(f"## 延迟 (LLM)")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 平均延迟 | {lat.get('avg_ms', 0):.0f} ms |")
    lines.append(f"| P50 延迟 | {lat.get('p50_ms', 0):.0f} ms |")
    lines.append(f"| P95 延迟 | {lat.get('p95_ms', 0):.0f} ms |")
    lines.append(f"| 最大延迟 | {lat.get('max_ms', 0):.0f} ms |")
    lines.append(f"| 最小延迟 | {lat.get('min_ms', 0):.0f} ms |")
    lines.append(f"| 采样数 | {lat.get('sample_count', 0)} |")
    lines.append(f"")

    # ── Token 消耗 ──
    tok = metrics.get("token", {})
    lines.append(f"## Token 消耗")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 总输入 Tokens | {tok.get('total_input_tokens', 0)} |")
    lines.append(f"| 总输出 Tokens | {tok.get('total_output_tokens', 0)} |")
    lines.append(f"| 总 Tokens | {tok.get('total_tokens', 0)} |")
    lines.append(f"| 平均输入 Tokens | {tok.get('avg_input_tokens', 0):.0f} |")
    lines.append(f"| 平均输出 Tokens | {tok.get('avg_output_tokens', 0):.0f} |")
    lines.append(f"| 缓存命中 Tokens | {tok.get('cached_tokens', 0)} |")
    lines.append(f"| 缓存命中率 | {tok.get('cache_hit_rate', 0):.1%} |")
    lines.append(f"")

    # ── 失败详情 ──
    failed = [r for r in results if not r.get("tool_correct", False)]
    if failed:
        lines.append(f"## 失败详情")
        lines.append(f"")
        for r in failed:
            instr = r.get("instruction", "")[:60]
            lines.append(
                f"- **{r['case_id']}**: \"{instr}\"  "
                f"期望={r['expected_tool']}, 实际={r['actual_tool']}"
            )
            issues = r.get("comparison", {}).get("issues", [])
            for issue in issues[:3]:
                lines.append(f"  - ⚠ {issue}")

    markdown = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"  [INFO] Agent report saved to {output_path}")

    return markdown


def generate_model_comparison(
    all_results: Dict[str, dict],
    output_path: str = None,
) -> str:
    """生成多模型对比报告。

    Args:
        all_results: {model_name: {"metrics": metrics, "results": results}}
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_names = list(all_results.keys())

    lines = []
    lines.append(f"# 多模型对比报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"")
    lines.append(f"## 准确率 vs 延迟 vs Token")
    lines.append(f"")
    lines.append(f"| 模型 | 工具准确率 | 参数完整率 | 边界正确率 | 平均延迟 | 平均 Tokens |")
    lines.append(f"|------|-----------|-----------|-----------|---------|------------|")

    for name in model_names:
        m = all_results[name]["metrics"]
        ta = m.get("tool_accuracy", {})
        pq = m.get("param_quality", {})
        eb = m.get("edge_behavior", {})
        lat = m.get("latency", {})
        tok = m.get("token", {})

        lines.append(
            f"| {name} "
            f"| {ta.get('overall_accuracy', 0):.1%} "
            f"| {pq.get('completeness', 0):.1%} "
            f"| {eb.get('correct_rejection_rate', 0):.1%} "
            f"| {lat.get('avg_ms', 0):.0f}ms "
            f"| {tok.get('avg_total_tokens', 0):.0f} |"
        )

    lines.append(f"")
    lines.append(f"## 逐模型报告")
    lines.append(f"")

    for name in model_names:
        lines.append(f"---")
        lines.append(f"### {name}")
        lines.append(f"")
        m = all_results[name]["metrics"]

        # 工具准确率详情
        ta = m.get("tool_accuracy", {})
        lines.append(f"**工具选择准确率**: {ta.get('overall_accuracy', 0):.1%} "
                     f"({ta.get('correct_cases', 0)}/{ta.get('total_cases', 0)})")

        # 失败用例
        results = all_results[name].get("results", [])
        failed = [r for r in results if not r.get("tool_correct", False)]
        if failed:
            lines.append(f"\n失败用例:")
            for r in failed:
                lines.append(
                    f"- {r['case_id']}: 期望={r['expected_tool']}, 实际={r['actual_tool']}"
                )

        lines.append(f"")

    markdown = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"  [INFO] Model comparison report saved to {output_path}")

    return markdown
