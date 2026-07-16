"""Agent 评测执行器：加载 NLU 数据集 → 调用 ToolManager → 收集结果。

与规划器评测不同，Agent 评测调用完整的 ToolManager.process() 流程，
需要 LLM API 参与，会产生 token 消耗和延迟。

评测内容：
  - 工具选择正确性（LLM 是否选择了正确的工具）
  - 参数填充正确性（参数是否完整且符合 schema）
  - 无效输入处理（LLM 面对乱码/空输入的行为）
  - 延迟与 Token 消耗
"""

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.tools import ToolManager, ToolRegistry
from .agent_comparators import compare_agent_response
from .agent_metrics import compute_agent_metrics


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


class TokenCounter:
    """包装 LLM client，记录每次调用的 token 消耗。

    用法:
        counter = TokenCounter(llm_client)
        with counter:
            tool_manager.process(instruction)
        print(counter.get_usage())
    """

    def __init__(self, llm_client):
        self.llm_client = llm_client
        self._original_create = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.cached_tokens = 0

    def __enter__(self):
        self._original_create = self.llm_client.chat.completions.create

        def counting_create(*args, **kwargs):
            response = self._original_create(*args, **kwargs)
            usage = getattr(response, 'usage', None)
            if usage is not None:
                self.total_input_tokens += getattr(usage, 'prompt_tokens', 0)
                self.total_output_tokens += getattr(usage, 'completion_tokens', 0)
                # 缓存命中（部分模型支持）
                details = getattr(usage, 'prompt_tokens_details', None)
                if details:
                    self.cached_tokens += getattr(details, 'cached_tokens', 0)
            return response

        self.llm_client.chat.completions.create = counting_create
        return self

    def __exit__(self, *args):
        if self._original_create:
            self.llm_client.chat.completions.create = self._original_create

    def get_usage(self) -> dict:
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "cached_tokens": self.cached_tokens,
        }


def run_agent_eval(
    dataset_path: str,
    tool_manager: ToolManager,
    verbose: bool = True,
) -> Tuple[List[dict], dict]:
    """运行 Agent/LLM 层面评测。

    每一条用例会调用 ToolManager.process()，完整经过 LLM 调用 + dispatch。
    记录工具选择、参数填充、延迟、Token 消耗。

    Args:
        dataset_path: 数据集 JSON 路径
        tool_manager: 已初始化的 ToolManager 实例
        verbose: 是否输出详细日志

    Returns:
        (results, metrics): 逐条结果 + 汇总指标
    """
    dataset = load_dataset(dataset_path)
    if not dataset:
        return [], {}

    results = []
    total = len(dataset)
    tool_correct_count = 0

    for i, case in enumerate(dataset):
        instruction = case.get("instruction", "")
        case_id = case.get("id", f"case-{i}")
        expected_tool = case.get("expected_tool", "")

        if verbose:
            instr_preview = instruction[:50] + "..." if len(instruction) > 50 else instruction
            print(f"\n  [{i+1}/{total}] {case_id}: \"{instr_preview}\"")

        # 记录 Token
        counter = TokenCounter(tool_manager.llm_client)
        t0 = time.time()

        try:
            with counter:
                response = tool_manager.process(instruction)
            elapsed_ms = (time.time() - t0) * 1000
        except Exception as e:
            response = {"tool_name": "", "success": False, "error": str(e)}
            elapsed_ms = (time.time() - t0) * 1000

        token_usage = counter.get_usage()

        # 对比预期
        comparison = compare_agent_response(response, case)
        tool_correct = response.get("tool_name", "") == expected_tool
        if tool_correct:
            tool_correct_count += 1

        if verbose:
            actual_tool = response.get("tool_name", "(none)")
            status = "✅" if tool_correct else "❌"
            print(f"    {status} tool={actual_tool} (expect={expected_tool}) "
                  f"[{elapsed_ms:.0f}ms, {token_usage['total_tokens']}tokens]")

        results.append({
            "case_id": case_id,
            "instruction": instruction,
            "category": case.get("category", ""),
            "actual_tool": response.get("tool_name", ""),
            "expected_tool": expected_tool,
            "tool_correct": tool_correct,
            "args": response.get("args", {}),
            "error": response.get("error"),
            "comparison": comparison,
            "latency_ms": round(elapsed_ms, 2),
            "tokens": token_usage,
        })

    # 计算汇总指标
    metrics = compute_agent_metrics(results)

    if verbose:
        print(f"\n  ─────────────────────────────────────────")
        print(f"  Agent 评测完成: {total} cases, "
              f"{tool_correct_count} ✅ 工具正确, "
              f"{total - tool_correct_count} ❌ 错误")
        print(f"  工具选择准确率: {metrics.get('tool_accuracy', 0):.2%}")
        print(f"  平均延迟: {metrics.get('latency', {}).get('avg_ms', 0):.0f}ms")
        print(f"  总 Tokens: {metrics.get('token', {}).get('total_tokens', 0)}")

    return results, metrics


def make_tool_manager(
    llm_client: Any,
    model_name: str,
    location_ids: List[str] = None,
    corridor_ids: List[str] = None,
    robot_ids: List[str] = None,
) -> ToolManager:
    """创建一个用于评测的 ToolManager 实例。

    所有工具 handler 使用 stub 实现（不实际执行，只记录被调用）。
    这样 Agent 评测只测试"LLM 是否选择了正确的工具和参数"，
    不依赖后端服务的可用性。

    Args:
        llm_client: OpenAI 兼容的 LLM client
        model_name: 模型名称
        location_ids: 位置ID列表（可选，为空时用占位数据）
        corridor_ids: 通道ID列表（可选）
        robot_ids: 机器人ID列表（可选）

    Returns:
        配置好的 ToolManager 实例
    """
    registry = ToolRegistry()

    manager = ToolManager(
        registry=registry,
        llm_client=llm_client,
        model=model_name,
        temperature=0.1,
        max_tokens=2000,
        workflow_fn=lambda d: None,
        cron_manager=MagicMock(),
        answer_fn=lambda q: f"Answer: {q}",
        robot_move_fn=lambda rid, pos: f"Moved {rid} to {pos}",
        map_modify_fn=lambda a, c: f"{'Closed' if a == 'close' else 'Opened'} {c}",
        cargo_done_fn=lambda rid: f"Done {rid}",
    )

    # 构建默认仓库上下文（固定地图）
    if location_ids is None:
        location_ids = [
            "loading_dock_north", "loading_dock_west",
            "charging_station", "packing_station", "maintenance_zone",
            "shelf_A_pickup", "shelf_B_pickup", "shelf_C_pickup",
            "shelf_D_pickup", "shelf_E_pickup", "shelf_F_pickup",
            "shelf_G_pickup", "shelf_H_pickup", "shelf_I_pickup",
            "shelf_K_pickup", "shelf_L_pickup", "shelf_M_pickup", "shelf_N_pickup",
        ]
    if corridor_ids is None:
        corridor_ids = [
            "north_aisle", "main_cross_aisle", "south_cross_aisle",
            "west_aisle", "east_aisle", "narrow_passage", "bottom_aisle",
        ]
    if robot_ids is None:
        robot_ids = ["R1", "R2", "R3", "R4", "R5"]

    manager.location_ids = location_ids
    manager.corridor_ids = corridor_ids
    manager.robot_ids = robot_ids
    manager.warehouse_context = (
        f"地图 20×20 网格\n"
        f"可用位置: {', '.join(location_ids)}\n"
        f"可用通道: {', '.join(corridor_ids)}\n"
        f"可用机器人: {', '.join(robot_ids)}"
    )

    manager.register_handlers()
    return manager
