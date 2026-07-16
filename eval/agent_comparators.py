"""Agent 对比逻辑：对比 LLM 的响应是否符合预期。

校验内容：
  - 工具选择是否匹配 expected_tool
  - 参数是否匹配 check_params 中的规则
  - 无效输入是否正确处理
"""

from typing import Any, Dict, List, Optional


def _get_nested(data: dict, path: str, default=None):
    """从嵌套 dict 中按点号路径取值。

    "tasks.0.robot_id" → data["tasks"][0]["robot_id"]
    """
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else default
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


def _check_value(actual: Any, rule: dict) -> bool:
    """检查单个值是否符合规则。

    rule 支持的检查类型：
      - {"expect": value}          : 精确匹配
      - {"expect_in": [values]}    : 值在列表中
      - {"expect_contain": str}    : 字符串包含
      - {"expect_ge": number}      : 数值大于等于
      - {"expect_match": regex}    : 正则匹配（字符串）
    """
    for check_type, expected in rule.items():
        if check_type == "expect":
            if actual != expected:
                return False
        elif check_type == "expect_in":
            if actual not in expected:
                return False
        elif check_type == "expect_contain":
            if not isinstance(actual, str) or expected not in actual:
                return False
        elif check_type == "expect_ge":
            if not isinstance(actual, (int, float)) or actual < expected:
                return False
        elif check_type == "expect_match":
            import re
            if not isinstance(actual, str) or not re.match(expected, actual):
                return False
    return True


def compare_agent_response(response: dict, case: dict) -> dict:
    """对比 Agent 响应与预期。

    Args:
        response: ToolManager.process() 的返回值
        case: 数据集中的一条用例

    Returns:
        {
            "tool_selection": {"correct": bool, "expected": str, "actual": str},
            "params": {
                "all_required_filled": bool,
                "exact_match": bool,
                "checks": {path: {"passed": bool, "actual": any, "expected": str}},
            },
            "edge_behavior": {
                "handled_correctly": bool,
                "failure_expected": bool,
                "partial_args": bool,
            },
            "issues": [str],
        }
    """
    actual_tool = response.get("tool_name", "")
    expected_tool = case.get("expected_tool", "")
    args = response.get("args", {})

    issues = []

    # ── 工具选择 ────────────────────────────────────────────────────────
    tool_correct = actual_tool == expected_tool
    if not tool_correct:
        issues.append(f"工具选择错误: 期望={expected_tool}, 实际={actual_tool}")

    # ── 参数校验 ────────────────────────────────────────────────────────
    check_params = case.get("check_params", {})
    all_required_filled = True
    exact_match = True
    param_checks = {}

    for param_path, rule in check_params.items():
        actual_value = _get_nested(args, param_path)
        passed = _check_value(actual_value, rule)

        param_checks[param_path] = {
            "passed": passed,
            "actual": str(actual_value),
            "expected": str(rule),
        }

        if not passed:
            all_required_filled = False
            exact_match = False
            issues.append(f"参数 {param_path}: 期望={rule}, 实际={actual_value}")

    # ── 边界行为校验 ────────────────────────────────────────────────────
    check_failure = case.get("check_failure", False)
    partial_args = case.get("expected_partial_args", False)
    expected_behavior = case.get("expected_behavior", "")

    edge_ok = True
    if check_failure:
        # 期望调度失败 → 检查 success=False 或 error 不为空
        if response.get("success", True) or response.get("error"):
            edge_ok = True  # 成功判断为"正确处理"（拒绝不可达任务）
        else:
            edge_ok = False
    elif partial_args:
        # 参数不完整时，工具仍然正确选择即可视为正确
        edge_ok = tool_correct
    elif expected_behavior == "reject_or_ask":
        # 空/乱码输入 → 期望 answer_question 或直接文本回复（_text_reply）
        if actual_tool in ("answer_question", "_text_reply"):
            edge_ok = True
        else:
            edge_ok = False
            issues.append(f"边界行为错误: 期望 answer_question/_text_reply, 实际={actual_tool}")
    elif case.get("category") in ("irrelevant", "gibberish"):
        # 无关话题/乱码 → LLM 返回文本回复而不是工具调用也是正确的
        if actual_tool in ("answer_question", "_text_reply"):
            edge_ok = True
        else:
            edge_ok = False
            issues.append(f"无关输入处理错误: 期望非工具调用, 实际={actual_tool}")
    elif case.get("category") == "fuzzy_match":
        # 模糊匹配 → 工具选对了就算正确
        edge_ok = tool_correct

    # ── 汇总 ────────────────────────────────────────────────────────────
    return {
        "tool_selection": {
            "correct": tool_correct,
            "expected": expected_tool,
            "actual": actual_tool,
        },
        "params": {
            "all_required_filled": all_required_filled,
            "exact_match": exact_match,
            "checks": param_checks,
        },
        "edge_behavior": {
            "handled_correctly": edge_ok,
            "failure_expected": check_failure,
            "partial_args": partial_args,
        },
        "issues": issues,
    }
