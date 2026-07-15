"""频道模块：消息平台接入。

目前实现：
- weixin: 通过 iLink Bot API 接入个人微信
"""

from app.channels.weixin.ilink_client import ILinkClient, ILinkError, SessionExpired
from app.channels.weixin.auth import qr_login, save_account, load_account, QrLoginError
from app.channels.weixin.poller import MessagePoller
from app.channels.weixin.message_handler import MessageHandler
from app.channels.weixin.context_store import ContextStore
from app.channels.weixin.reply_formatter import (
    format_schedule_result,
    format_error,
    HELP_TEXT,
)

__all__ = [
    "ILinkClient",
    "ILinkError",
    "SessionExpired",
    "qr_login",
    "save_account",
    "load_account",
    "QrLoginError",
    "MessagePoller",
    "MessageHandler",
    "ContextStore",
    "format_schedule_result",
    "format_error",
    "HELP_TEXT",
]
