"""iLink Bot API 扫码登录认证。

完全参照 Hermes Agent gateway/platforms/weixin.py qr_login() 的实现。

关键差异（与之前版本相比）:
1. 轮询端点用 ?qrcode= 参数，不是 ?key=
2. 响应读 status 字段，不是 ret
3. 确认后字段名: ilink_bot_id, bot_token, baseurl
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import aiohttp


# ── 常量 ─────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
USER_AGENT = "MicroMessenger"
QR_TIMEOUT_SECONDS = 35
LOGIN_TIMEOUT_SECONDS = 480  # 8 分钟

# 扫码状态枚举
STATUS_QRCODE = "qrcode"
STATUS_WAIT = "wait"
STATUS_SCANED = "scaned"
STATUS_SCANED_REDIRECT = "scaned_but_redirect"
STATUS_EXPIRED = "expired"
STATUS_CONFIRMED = "confirmed"


class QrLoginError(Exception):
    """二维码登录错误。"""


async def _api_get(
    session: aiohttp.ClientSession,
    base_url: str,
    endpoint: str,
    timeout: int = QR_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """HTTP GET 并解析 JSON。

    iLink API 返回的 Content-Type 是 application/octet-stream，
    必须用 resp.text() 手动解析 JSON。
    """
    url = f"{base_url}/{endpoint}"
    headers = {"User-Agent": USER_AGENT}

    async with session.get(
        url,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            raise QrLoginError(f"HTTP {resp.status}: {await resp.text()}")

        raw_text = await resp.text()
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise QrLoginError(
                f"JSON parse failed: {e}, body: {raw_text[:200]}"
            )


# ── 登录主流程 ────────────────────────────────────────────────────────────


async def qr_login(
    base_url: str = DEFAULT_BASE_URL,
    bot_type: str = "3",
    timeout_seconds: int = LOGIN_TIMEOUT_SECONDS,
    on_status: Callable = None,
) -> Dict[str, Any]:
    """完整的 iLink 扫码登录流程。

    参照 Hermes Agent weixin.py:1003 qr_login() 逐行实现。

    流程:
    1. GET /ilink/bot/get_bot_qrcode?bot_type=3 → {"qrcode": "...", "qrcode_img_content": "..."}
    2. 轮询 GET /ilink/bot/get_qrcode_status?qrcode=<qrcode>
       - status="wait" → 继续等
       - status="scaned" → 已扫码
       - status="scaned_but_redirect" → 切换 base_url
       - status="expired" → 刷新（最多 3 次）
       - status="confirmed" → {"ilink_bot_id": "...", "bot_token": "...", ...}
    3. 返回凭证

    Returns:
        {"account_id": "...", "token": "...", "base_url": "...", "user_id": "..."}
    """
    async with aiohttp.ClientSession(trust_env=True) as session:
        # ── Step 1: 获取二维码 ──
        try:
            qr_resp = await _api_get(
                session,
                base_url=base_url,
                endpoint=f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
            )
        except Exception as e:
            raise QrLoginError(f"获取二维码失败: {e}")

        qrcode_value = str(qr_resp.get("qrcode") or "")
        qrcode_url = str(qr_resp.get("qrcode_img_content") or "")

        if not qrcode_value:
            raise QrLoginError("QR 响应缺少 qrcode 字段")

        if on_status:
            on_status(STATUS_QRCODE, {
                "qrcode_url": qrcode_url,
                "qrcode_value": qrcode_value,
            })

        # ── Step 2: 轮询扫码状态 ──
        deadline = time.monotonic() + timeout_seconds
        current_base_url = base_url
        refresh_count = 0

        while time.monotonic() < deadline:
            try:
                # 注意: 参数名是 ?qrcode=，不是 ?key=
                status_resp = await _api_get(
                    session,
                    base_url=current_base_url,
                    endpoint=f"ilink/bot/get_qrcode_status?qrcode={qrcode_value}",
                )
            except asyncio.TimeoutError:
                await asyncio.sleep(1)
                continue
            except Exception as e:
                # 临时错误，继续重试
                if on_status:
                    on_status("error", {"message": str(e)})
                await asyncio.sleep(2)
                continue

            # 读取 status 字段（不是 ret）
            status = str(status_resp.get("status") or "wait")

            if on_status:
                on_status(status, status_resp)

            # ── 处理各种状态 ──

            if status == STATUS_WAIT:
                # 静默等待
                pass

            elif status == STATUS_SCANED:
                # 已扫码，等待用户点击确认
                pass

            elif status == STATUS_SCANED_REDIRECT:
                # 需要切换 host
                redirect_host = str(status_resp.get("redirect_host") or "")
                if redirect_host:
                    current_base_url = f"https://{redirect_host}"

            elif status == STATUS_EXPIRED:
                refresh_count += 1
                if refresh_count > 3:
                    raise QrLoginError("二维码多次过期，请重试")
                # 刷新二维码
                try:
                    qr_resp = await _api_get(
                        session,
                        base_url=base_url,
                        endpoint=f"ilink/bot/get_bot_qrcode?bot_type={bot_type}",
                    )
                    qrcode_value = str(qr_resp.get("qrcode") or "")
                    qrcode_url = str(qr_resp.get("qrcode_img_content") or "")
                    if on_status:
                        on_status(STATUS_QRCODE, {
                            "qrcode_url": qrcode_url,
                            "qrcode_value": qrcode_value,
                        })
                except Exception as e:
                    raise QrLoginError(f"刷新二维码失败: {e}")

            elif status == STATUS_CONFIRMED:
                # 确认成功！提取凭证
                account_id = str(status_resp.get("ilink_bot_id") or "")
                token = str(status_resp.get("bot_token") or "")
                result_base_url = str(status_resp.get("baseurl") or current_base_url)
                user_id = str(status_resp.get("ilink_user_id") or "")

                if not account_id or not token:
                    raise QrLoginError("确认成功但凭证不完整（缺少 ilink_bot_id 或 bot_token）")

                return {
                    "account_id": account_id,
                    "token": token,
                    "base_url": result_base_url,
                    "user_id": user_id,
                }

            else:
                # 未知状态，继续等待
                pass

            await asyncio.sleep(1)

        # 超时
        raise QrLoginError(f"登录超时（{timeout_seconds} 秒）")


# ── 账户持久化 ───────────────────────────────────────────────────────────


def save_account(account: Dict[str, Any], filepath: str) -> None:
    """保存账户凭证到 JSON 文件（权限 600）。"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=2)

    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_account(filepath: str) -> Optional[Dict[str, Any]]:
    """从 JSON 文件加载账户凭证。"""
    path = Path(filepath)
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
