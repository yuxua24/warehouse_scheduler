"""评测执行器：加载数据集 → 执行规划 → 收集结果。

支持两种执行模式：
  - 场景评测: 使用 run_structured() 绕过 LLM，仅测试规划内核
  - NL 理解评测: 使用 run() 走完整流程，需要 LLM API
"""

import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

# 确保项目根目录在 sys.path 中
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.orchestration.workflow import Workflow
from app.domain.planning_state import PlanningState
from .comparators import compare_case_result, score_result
from .metrics import compute_all_metrics


def create_workflow(
    map_path: str = None,
    runtime_path: str = None,
    max_timestep: int = 200,
) -> Optional[Workflow]:
    """创建 Workflow 实例（不初始化 LLM 相关组件）。"""
    try:
        wf = Workflow(
            map_path=map_path,
            runtime_path=runtime_path,
            api_config_path=None,  # 无 API key → parser 不会初始化 LLM 调用
            max_timestep=max_timestep,
        )
        if wf.warehouse_map is None:
            print(f"  [ERROR] Map loading failed: {wf.map_errors}")
            return None
        return wf
    except Exception as e:
        print(f"  [ERROR] Workflow creation failed: {e}")
        return None


def load_dataset(path: str) -> List[dict]:
    """加载评测数据集。"""
    if not os.path.exists(path):
        print(f"  [ERROR] Dataset not found: {path}")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  [INFO] Loaded {len(data)} cases from {path}")
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [ERROR] Failed to load dataset: {e}")
        return []


def run_scenario_eval(
    dataset_path: str,
    map_path: str = None,
    runtime_path: str = None,
    max_timestep: int = 200,
    verbose: bool = True,
) -> Tuple[List[dict], dict]:
    """运行场景评测（绕过 LLM，只测试规划内核）。

    每个 case 调用 workflow.run_structured()，不需要 LLM API。

    Args:
        dataset_path: 数据集 JSON 路径
        map_path: 地图文件路径
        runtime_path: 运行时状态文件路径
        max_timestep: 最大时间步
        verbose: 是否输出详细日志

    Returns:
        (results, metrics): 逐条结果 + 汇总指标
    """
    wf = create_workflow(map_path, runtime_path, max_timestep)
    if wf is None:
        return [], {}

    dataset = load_dataset(dataset_path)
    if not dataset:
        return [], {}

    results = []
    total = len(dataset)
    passed = 0
    failed = 0

    for i, case in enumerate(dataset):
        case_id = case.get("id", f"case-{i}")
        case_name = case.get("name", case_id)

        if verbose:
            print(f"\n  [{i+1}/{total}] {case_id}: {case_name}")

        # 构建 structured 输入
        structured = {
            "tasks": case.get("tasks", []),
        }

        # 处理约束（临时封闭）
        blockages = case.get("blockages", [])
        if blockages:
            structured["runtime_constraints"] = []
            for b in blockages:
                if b.get("target_type") == "closed_corridor":
                    structured["runtime_constraints"].append({
                        "constraint_type": "closed_corridor",
                        "target_id": b.get("target_id", ""),
                        "start_time": b.get("start_time", 0),
                        "end_time": b.get("end_time"),
                    })

        # 执行规划
        t0 = time.time()
        try:
            state = wf.run_structured(structured)
            elapsed = (time.time() - t0) * 1000
        except Exception as e:
            state = None
            elapsed = (time.time() - t0) * 1000
            if verbose:
                print(f"    ❌ Exception: {e}")

        # 对比预期
        expected = case.get("expected", {})
        comparison = compare_case_result(state, expected)
        grade = score_result(comparison)

        if grade == "PASS":
            passed += 1
        else:
            failed += 1

        if verbose:
            status_icon = "✅" if grade == "PASS" else "⚠️" if grade == "WARN" else "❌"
            status_str = getattr(getattr(state, 'status', None), 'value', 'N/A') if state else 'ERROR'
            success_info = ""
            if state and hasattr(state, 'metrics') and state.metrics:
                m = state.metrics
                success_info = f" ({m.planned_task_count}/{m.total_task_count} tasks, {m.retry_count} replans)"
            print(f"    {status_icon} [{grade}] {status_str}{success_info} ({elapsed:.0f}ms)")
            if comparison.get("issues"):
                for issue in comparison["issues"][:3]:
                    print(f"      ⚠ {issue}")

        results.append({
            "case_id": case_id,
            "case_name": case_name,
            "category": case.get("category", "unknown"),
            "planning_state": state,
            "comparison": comparison,
            "grade": grade,
            "elapsed_ms": elapsed,
        })

    # 计算汇总指标
    metrics = compute_all_metrics(results)

    if verbose:
        print(f"\n  ─────────────────────────────────────────")
        print(f"  场景评测完成: {total} cases, {passed} ✅, {failed} ❌")
        print(f"  规划成功率: {metrics['planning']['planning_success_rate']:.2%}")

    return results, metrics


def print_case_details(results: List[dict], max_cases: int = 5) -> None:
    """打印逐条 case 详情。"""
    print(f"\n{'='*60}")
    print(f"  逐条结果详情")
    print(f"{'='*60}")
    for r in results[:max_cases]:
        state = r.get("planning_state")
        grade = r.get("grade", "?")
        icon = "✅" if grade == "PASS" else "⚠️" if grade == "WARN" else "❌"
        status_str = "?"
        success_info = ""
        if state:
            status_str = getattr(getattr(state, 'status', None), 'value', '?')
            if hasattr(state, 'metrics') and state.metrics:
                m = state.metrics
                success_info = f" | {m.planned_task_count}/{m.total_task_count} 成功 | 重规划 {m.retry_count} 次"
        print(f"  {icon} {r['case_id']}: {r['case_name']}")
        print(f"     状态: {status_str}{success_info}")
        issues = r.get("comparison", {}).get("issues", [])
        for issue in issues:
            print(f"     ⚠ {issue}")
