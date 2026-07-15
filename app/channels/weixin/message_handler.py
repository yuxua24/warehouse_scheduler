"""iLink 消息处理器：去重、过滤、调用调度引擎、回复。

参照 Hermes Agent gateway/platforms/weixin.py 的 _process_message() 设计。
"""

import hashlib
import logging
import time
import traceback
from typing import Any, Callable, Dict, Optional, Set, Tuple

from app.channels.weixin.ilink_client import ILinkClient
from app.channels.weixin.context_store import ContextStore
from app.channels.weixin.reply_formatter import (
    format_schedule_result,
    format_error,
    HELP_TEXT,
)

logger = logging.getLogger(__name__)

# 去重窗口（秒）
DEDUP_TTL_SECONDS = 300
# 文本批处理窗口（秒）
TEXT_BATCH_WINDOW_SECONDS = 3


class MessageDeduplicator:
    """消息去重器：基于 message_id + 内容 MD5 双重去重。

    每个条目在 TTL 秒后自动过期。
    """

    def __init__(self, ttl: float = DEDUP_TTL_SECONDS):
        self._ttl = ttl
        self._entries: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        """检查是否重复。不重复则记录并返回 False。"""
        self._cleanup()

        now = time.time()
        if key in self._entries:
            return True

        self._entries[key] = now
        return False

    def _cleanup(self) -> None:
        """清理过期条目。"""
        now = time.time()
        expired = [k for k, v in self._entries.items() if now - v > self._ttl]
        for k in expired:
            del self._entries[k]

    @staticmethod
    def make_message_key(sender_id: str, msg_id: str, content: str) -> str:
        """生成消息去重 key。"""
        return f"msg:{msg_id}"

    @staticmethod
    def make_content_key(sender_id: str, content: str) -> str:
        """生成内容 MD5 去重 key。"""
        md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"content:{sender_id}:{md5}"


class MessageHandler:
    """处理 iLink 消息：去重 → 过滤 → 调度 → 回复。

    使用方法:
        handler = MessageHandler(workflow_fn, client, context_store, config)
        await handler.handle(msg)
    """

    def __init__(
        self,
        workflow_fn: Callable[[str], Any],
        client: ILinkClient,
        context_store: ContextStore,
        config: Dict[str, Any],
        location_names: Dict[str, str] = None,
    ):
        self.workflow_fn = workflow_fn
        self.client = client
        self.context_store = context_store
        self.dm_policy = config.get("dm_policy", "open")
        self.allowed_users: Set[str] = set(config.get("allowed_users", []))
        self.deduplicator = MessageDeduplicator(ttl=DEDUP_TTL_SECONDS)
        self.location_names = location_names or {}

    async def handle(self, msg: Dict[str, Any]) -> None:
        """处理一条 iLink 消息。

        消息结构（参考 Hermes 解析的 iLink 消息体）：
        {
            "message_id": "...",
            "from_user": "wxid_xxx",
            "chat_type": "dm" | "group",
            "content": "文本内容",
            "context_token": "...",
            "message_type": "text",
        }
        """
        try:
            await self._handle_impl(msg)
        except Exception:
            print(f"[weixin] Handler error: {e}")

    # ── 内部实现 ─────────────────────────────────────────────────────────

    async def _handle_impl(self, msg: Dict[str, Any]) -> None:
        # iLink API 消息字段映射:
        #   from_user_id     → 发送者
        #   to_user_id       → 接收者 (Bot)
        #   message_type     → 1=文本
        #   group_id         → 空=私聊，非空=群聊
        #   item_list[0].text_item.text → 文本内容
        #   context_token    → 上下文令牌

        user_id = msg.get("from_user_id", msg.get("from_user", ""))
        msg_id = str(msg.get("message_id", ""))
        group_id = msg.get("group_id", "")
        context_token = msg.get("context_token", "")

        # 1. 过滤自己的消息
        if user_id == self.client.account_id:
            return

        # 2. 仅处理私聊
        if group_id:
            return  # 群聊消息，忽略

        # 3. 提取文本内容
        content = ""
        item_list = msg.get("item_list", [])
        if item_list:
            text_item = item_list[0].get("text_item", {})
            content = text_item.get("text", "")

        if not content:
            return  # 非文本或无内容

        # 4. 去重
        if content and self.deduplicator.is_duplicate(
            MessageDeduplicator.make_message_key(user_id, msg_id, content)
        ):
            return

        if content and self.deduplicator.is_duplicate(
            MessageDeduplicator.make_content_key(user_id, content)
        ):
            return

        # 5. 保存 context_token
        if context_token:
            self.context_store.update_context_token(user_id, context_token)

        # 6. 访问控制
        if self.dm_policy == "disabled":
            return
        if self.dm_policy == "allowlist":
            if user_id not in self.allowed_users:
                await self._send_reply(user_id, "❌ 无权限，请联系管理员")
                return

        # 7. 处理指令
        text = content.strip()
        if not text:
            await self._send_reply(user_id, "请输入调度指令，发送「帮助」查看说明")
            return

        print(f"[weixin] Processing msg from {user_id}: {text[:80]}")

        if text in ("帮助", "help", "?", "/help"):
            await self._send_reply(user_id, HELP_TEXT)
            return

        # 8. 调用调度引擎
        try:
            print(f"[weixin] Calling workflow...")
            state = self.workflow_fn(text)
            print(f"[weixin] Workflow done, status={state.status.value}")
            reply = format_schedule_result(state, location_names=self.location_names)
            print(f"[weixin] Reply formatted ({len(reply)} chars)")
        except Exception as e:
            print(f"[weixin] Workflow failed: {e}")
            import traceback; traceback.print_exc()
            reply = format_error(f"调度处理异常: {e}")

        # 9. 发送回复
        print(f"[weixin] Sending reply to {user_id}...")
        await self._send_reply(user_id, reply)
        print(f"[weixin] Reply sent")

    async def _send_reply(self, user_id: str, text: str) -> None:
        """发送回复消息。"""
        ctx_token = self.context_store.get_context_token(user_id)
        print(f"[weixin] Sending to {user_id}, ctx_token_len={len(ctx_token)}, text_len={len(text)}")
        try:
            result = await self.client.sendmessage(
                to_user=user_id,
                text=text,
                context_token=ctx_token,
            )
            print(f"[weixin] Send result: {result}")
        except Exception as e:
            print(f"[weixin] Failed to send reply to {user_id}: {e}")
            import traceback; traceback.print_exc()
