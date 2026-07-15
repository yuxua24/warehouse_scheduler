"""Context token 和 sync_buf 持久化存储。

参照 Hermes Agent 的 ContextTokenStore 设计：
- sync_buf: 长轮询同步游标，重启后从正确位置恢复
- context_token: 每个用户的会话令牌，回复消息时必须携带
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional


class ContextStore:
    """管理 iLink Bot API 的同步游标和上下文令牌持久化。

    两个关键数据：
    1. sync_buf — 长轮询游标。每次 getupdates 返回新的 buf，
       必须持久化，否则重启后丢失消息或收到重复消息。
    2. context_tokens — 每个用户最后一条消息的 context_token，
       发送回复时必须携带。
    """

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._sync_buf_file = self.data_dir / "weixin_sync_buf.txt"
        self._context_tokens_file = self.data_dir / "weixin_context_tokens.json"
        self._tokens: Dict[str, str] = {}

        self._load_tokens()

    # ── sync_buf ────────────────────────────────────────────────────────

    def get_sync_buf(self) -> str:
        """获取当前的同步游标，首次返回空字符串。"""
        if self._sync_buf_file.exists():
            return self._sync_buf_file.read_text(encoding="utf-8").strip()
        return ""

    def save_sync_buf(self, buf: str) -> None:
        """保存同步游标。传入空字符串或 None 不会覆盖。"""
        if not buf:
            return
        self._sync_buf_file.write_text(buf, encoding="utf-8")

    # ── context_tokens ──────────────────────────────────────────────────

    def get_context_token(self, user_id: str) -> str:
        """获取指定用户的 context_token，没有则返回空字符串。"""
        return self._tokens.get(user_id, "")

    def update_context_token(self, user_id: str, token: str) -> None:
        """更新用户的 context_token 并持久化到磁盘。"""
        if not token:
            return
        self._tokens[user_id] = token
        self._save_tokens()

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _load_tokens(self) -> None:
        """从磁盘加载 context_tokens。"""
        if self._context_tokens_file.exists():
            try:
                content = self._context_tokens_file.read_text(encoding="utf-8")
                self._tokens = json.loads(content) if content.strip() else {}
            except (json.JSONDecodeError, OSError):
                self._tokens = {}

    def _save_tokens(self, tokens: Optional[Dict[str, str]] = None) -> None:
        """保存 context_tokens 到磁盘。"""
        data = tokens if tokens is not None else self._tokens
        self._context_tokens_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
