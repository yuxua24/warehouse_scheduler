"""MemoryRetriever: 智能检索 + 摘要生成（方式 2 的核心）。

将用户输入与调度历史进行多路召回（FTS5 全文搜索 + 模式匹配），
然后用规则引擎生成轻量摘要注入 LLM Prompt。

核心原则:
  - 检索路径不调用 LLM（零额外 API 费用）
  - 摘要控制在 ~200 tokens（可控开销）
  - 失败记录加权降级但保留（提供学习价值）
"""

import json
from typing import Optional, List, Dict, Any

from .hybrid_store import HybridMemoryStore


class MemoryRetriever:
    """智能记忆检索器，负责检索 + 摘要生成。

    Args:
        store: HybridMemoryStore 实例
        top_k: 检索 top-k 条历史记录
        max_tokens: 摘要最大 token 数
        include_preference: 是否在摘要中包含用户偏好
    """

    def __init__(
        self,
        store: HybridMemoryStore,
        top_k: int = 3,
        max_tokens: int = 300,
        include_preference: bool = True,
    ):
        self.store = store
        self.top_k = top_k
        self.max_tokens = max_tokens
        self.include_preference = include_preference

    def retrieve(self, instruction: str) -> str:
        """检索 + 生成摘要 → 返回注入文本。

        这是方式 2 的主入口，供 Workflow 调用。
        """
        if self.store.mode != "hybrid":
            return ""

        return self.store.retrieve_for_injection(
            instruction=instruction,
            top_k=self.top_k,
            max_tokens=self.max_tokens,
        )

    def format_injection_block(self, summary: str) -> str:
        """将摘要包装为 LLM Prompt 注入块的格式。"""
        if not summary:
            return ""
        return f"[用户调度习惯参考]\n{summary}\n---"

    def retrieve_formatted(self, instruction: str) -> str:
        """检索 + 格式化 → 直接可用于注入的文本块。"""
        summary = self.retrieve(instruction)
        return self.format_injection_block(summary)
