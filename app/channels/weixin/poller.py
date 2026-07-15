"""iLink Bot API 长轮询消息接收器。

参照 Hermes Agent gateway/platforms/weixin.py 的 _poll_loop() 设计。

长轮询机制：
- POST /ilink/bot/getupdates，超时 35 秒
- 有消息立即返回，无消息则 35 秒后超时返回
- 错误时根据 errcode 采用不同退避策略
"""

import asyncio
import logging
from typing import Any, Callable, Dict

from app.channels.weixin.ilink_client import ILinkClient, ILinkError, SessionExpired
from app.channels.weixin.context_store import ContextStore

logger = logging.getLogger(__name__)

# 错误退避策略
RETRY_DELAY_SECONDS = 2  # 普通错误退避
MAX_CONSECUTIVE_ERRORS = 3  # 连续错误阈值
BACKOFF_COOLDOWN_SECONDS = 30  # 超过阈值后的冷却时间
SESSION_EXPIRED_PAUSE_SECONDS = 600  # Session 过期暂停 10 分钟


class MessagePoller:
    """长轮询消息接收器。

    使用方法:
        poller = MessagePoller(client, handler, context_store)
        await poller.start()   # 启动轮询循环（阻塞）
        poller.stop()          # 停止
    """

    def __init__(
        self,
        client: ILinkClient,
        handler: Callable[[Dict[str, Any]], Any],
        context_store: ContextStore,
    ):
        self.client = client
        self.handler = handler  # async callable: on_message(msg: dict)
        self.context_store = context_store
        self._running = False
        self._task: asyncio.Task = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> asyncio.Task:
        """启动轮询循环（作为后台任务），返回 task 以便外部管理。"""
        if self._running:
            logger.warning("MessagePoller is already running")
            return self._task

        self._running = True
        self._task = asyncio.ensure_future(self._poll_loop())
        logger.info("MessagePoller started (iLink long-poll)")
        return self._task

    def stop(self) -> None:
        """停止轮询循环。"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("MessagePoller stopped")

    # ── 主循环 ──────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """长轮询主循环。"""
        consecutive_errors = 0
        poll_count = 0

        while self._running:
            poll_count += 1
            try:
                await self._ensure_sessions()
                sync_buf = self.context_store.get_sync_buf()

                # 长轮询请求（35 秒超时）
                resp = await self.client.getupdates(sync_buf)

                # 成功 → 重置连续错误计数
                consecutive_errors = 0

                # 更新同步游标（持久化）
                new_buf = resp.get("get_updates_buf", "")
                self.context_store.save_sync_buf(new_buf)

                # 分发消息 (API 字段名: msgs)
                updates = resp.get("msgs", resp.get("updates", []))
                if updates:
                    import json as _json
                    print(f"[weixin] RECEIVED {len(updates)} message(s):")
                    for i, msg in enumerate(updates):
                        print(f"[weixin]   msg[{i}]: {_json.dumps(msg, ensure_ascii=False, default=str)}")
                    for msg in updates:
                        asyncio.ensure_future(self._safe_handle(msg))
                elif poll_count == 1:
                    # 第一次轮询：打印完整响应结构（调试用）
                    import json as _json
                    print(f"[weixin] First poll response keys: {list(resp.keys())}")
                    print(f"[weixin] Full: {_json.dumps(resp, ensure_ascii=False, default=str)[:500]}")
                elif poll_count % 5 == 0:
                    # 每 5 次轮询（约 3 分钟）打印一次心跳
                    print(f"[weixin] Poll #{poll_count} OK, no messages (buf={len(new_buf)}B)")

            except asyncio.TimeoutError:
                # 长轮询正常超时，立即继续
                continue

            except asyncio.CancelledError:
                break

            except SessionExpired:
                print("[weixin] Session expired! Run: python scripts/weixin_login.py")
                await self._sleep_with_check(SESSION_EXPIRED_PAUSE_SECONDS)
                consecutive_errors = 0

            except ILinkError as e:
                consecutive_errors += 1
                print(f"[weixin] API error [errcode={e.errcode}]: {e.errmsg} (x{consecutive_errors})")

                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print(f"[weixin] Too many errors, cooling down {BACKOFF_COOLDOWN_SECONDS}s")
                    await self._sleep_with_check(BACKOFF_COOLDOWN_SECONDS)
                    consecutive_errors = 0
                else:
                    await self._sleep_with_check(RETRY_DELAY_SECONDS)

            except (OSError, ConnectionError) as e:
                consecutive_errors += 1
                print(f"[weixin] Network error: {e}")
                await self._sleep_with_check(RETRY_DELAY_SECONDS)

            except Exception as e:
                consecutive_errors += 1
                print(f"[weixin] Unexpected poll error: {e}")
                await self._sleep_with_check(RETRY_DELAY_SECONDS)

    # ── 内部 ─────────────────────────────────────────────────────────────

    async def _safe_handle(self, msg: Dict[str, Any]) -> None:
        """安全地调用消息处理器（捕获异常）。"""
        try:
            if self.handler is not None:
                result = self.handler(msg)
                # handler 可能是 coroutine
                if asyncio.iscoroutine(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception:
            print(f"[weixin] Error in safe_handle for msg from {msg.get('from_user', '?')}")

    async def _ensure_sessions(self) -> None:
        """确保 HTTP sessions 已创建。"""
        # ILinkClient 内部处理 session 创建
        pass

    async def _sleep_with_check(self, seconds: float) -> None:
        """sleep with cancellation check each second."""
        for _ in range(int(seconds)):
            if not self._running:
                break
            await asyncio.sleep(1)
        # 处理小数秒
        remain = seconds - int(seconds)
        if remain > 0 and self._running:
            await asyncio.sleep(remain)
