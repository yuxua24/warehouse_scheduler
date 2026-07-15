"""iLink Bot API HTTP 客户端。

封装腾讯 iLink Bot API 的所有 HTTP 请求，包括：
- 长轮询收消息 (getupdates)
- 发送消息 (sendmessage)
- 发送「正在输入…」状态 (sendtyping)
- 获取配置 (getconfig)

参照 Hermes Agent gateway/platforms/weixin.py 的 API 层设计。
"""

import base64
import hashlib
import json
import os
import uuid
import secrets
from typing import Any, Dict, List, Optional
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import aiohttp


# ── 常量 ─────────────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
CLIENT_VERSION = "2.2.0"
APP_ID = "bot"
MESSAGE_TYPE_TEXT = 2  # MSG_TYPE_BOT — Hermes 用 2，不是 1


def _random_uin() -> str:
    """生成随机 X-WECHAT-UIN（16 字节 base64）。"""
    return base64.b64encode(os.urandom(16)).decode("ascii")


def _make_client_id(prefix: str = "warehouse") -> str:
    """生成唯一的 client_id。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── 异常 ─────────────────────────────────────────────────────────────────


class ILinkError(Exception):
    """iLink API 通用错误。"""

    def __init__(self, errcode: int, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"iLink error [{errcode}]: {errmsg}")


class SessionExpired(ILinkError):
    """登录 session 过期 (errcode=-14)，需要重新扫码。"""

    def __init__(self):
        super().__init__(-14, "Session expired, re-scan required")


# ── 客户端 ───────────────────────────────────────────────────────────────


class ILinkClient:
    """iLink Bot API HTTP 客户端。

    使用方法:
        client = ILinkClient(token="...", account_id="...")
        # 收消息
        resp = await client.getupdates(sync_buf="")
        # 发消息
        await client.sendmessage(to_user="wxid_xxx", text="Hello", context_token="...")
    """

    def __init__(
        self,
        token: str,
        account_id: str,
        base_url: str = DEFAULT_BASE_URL,
        cdn_base_url: str = DEFAULT_CDN_BASE_URL,
    ):
        self.token = token
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")

        # 轮询 session（长连接，可能需要长时间保持）
        self._poll_session: Optional[aiohttp.ClientSession] = None
        # 发送 session（短连接）
        self._send_session: Optional[aiohttp.ClientSession] = None

    # ── sessions ────────────────────────────────────────────────────────

    async def ensure_sessions(self) -> None:
        """确保 HTTP sessions 已创建。"""
        if self._poll_session is None:
            # 长轮询 session：不设全局超时
            timeout = aiohttp.ClientTimeout(total=None, connect=10)
            self._poll_session = aiohttp.ClientSession(timeout=timeout)

        if self._send_session is None:
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            self._send_session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        """关闭所有 HTTP sessions。"""
        if self._poll_session:
            await self._poll_session.close()
            self._poll_session = None
        if self._send_session:
            await self._send_session.close()
            self._send_session = None

    # ── headers ─────────────────────────────────────────────────────────

    def _common_headers(self) -> Dict[str, str]:
        """构造通用请求头。"""
        return {
            "Authorization": f"Bearer {self.token}",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_uin(),
            "iLink-App-Id": APP_ID,
            "iLink-App-ClientVersion": CLIENT_VERSION,
            "Content-Type": "application/json",
        }

    # ── 收消息（长轮询）─────────────────────────────────────────────────

    async def getupdates(self, sync_buf: str) -> Dict[str, Any]:
        """长轮询获取消息。

        Args:
            sync_buf: 上次响应的 get_updates_buf，首次传空字符串。

        Returns:
            {"get_updates_buf": "...", "updates": [...]}

        Raises:
            ILinkError: API 返回错误码
            SessionExpired: session 过期
        """
        await self.ensure_sessions()

        body = {
            "get_updates_buf": sync_buf,
            "base_info": {"channel_version": CLIENT_VERSION},
        }

        async with self._poll_session.post(
            f"{self.base_url}/ilink/bot/getupdates",
            json=body,
            headers=self._common_headers(),
            timeout=aiohttp.ClientTimeout(total=35, connect=10),
        ) as resp:
            raw = await resp.text()
            data = json.loads(raw)
            self._check_errcode(data)
            return data

    # ── 发消息 ──────────────────────────────────────────────────────────

    async def sendmessage(
        self,
        to_user: str,
        text: str,
        context_token: str = "",
    ) -> Dict[str, Any]:
        """发送文本消息。

        Args:
            to_user: 目标用户 ID (chat_id)
            text: 消息文本（支持 Markdown）
            context_token: 上下文令牌（用户最后一条消息中的）

        Returns:
            API 响应 JSON

        Raises:
            ILinkError: API 返回错误码
        """
        await self.ensure_sessions()

        body = {
            "msg": {
                "from_user_id": "",  # 发送时置空！不是 account_id
                "to_user_id": to_user,
                "client_id": _make_client_id(),
                "message_type": MESSAGE_TYPE_TEXT,
                "message_state": 2,
                "item_list": [
                    {"type": 1, "text_item": {"text": text}}
                ],
                "context_token": context_token,
            },
            "base_info": {"channel_version": CLIENT_VERSION},
        }

        async with self._send_session.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            json=body,
            headers=self._common_headers(),
        ) as resp:
            raw = await resp.text()
            data = json.loads(raw)
            self._check_errcode(data)
            return data

    # ── 辅助 ────────────────────────────────────────────────────────────

    async def sendtyping(
        self, to_user: str, typing_ticket: str, action: str = "start"
    ) -> Dict[str, Any]:
        """发送「正在输入…」状态。

        Args:
            to_user: 目标用户 ID
            typing_ticket: 从 getconfig 获取的票据
            action: "start" 开始输入 / "stop" 停止输入
        """
        await self.ensure_sessions()

        body = {
            "typing_ticket": typing_ticket,
            "to_user": to_user,
            "action": action,
        }

        async with self._send_session.post(
            f"{self.base_url}/ilink/bot/sendtyping",
            json=body,
            headers=self._common_headers(),
        ) as resp:
            raw = await resp.text()
            return json.loads(raw)

    async def getconfig(self) -> Dict[str, Any]:
        """获取配置，包含 typing_ticket。"""
        await self.ensure_sessions()

        async with self._send_session.post(
            f"{self.base_url}/ilink/bot/getconfig",
            json={},
            headers=self._common_headers(),
        ) as resp:
            raw = await resp.text()
            return json.loads(raw)

    # ── 媒体发送（对照 Hermes _send_file） ────────────────────────────

    async def send_image(
        self,
        to_user: str,
        file_path: str,
        context_token: str = "",
    ) -> Dict[str, Any]:
        """发送图片/GIF 到微信。

        完整流程（对照 Hermes _send_file）:
        1. 读取文件，生成 filekey + aes_key
        2. POST getuploadurl (带 rawsize, rawfilemd5, filesize, aeskey)
        3. AES-ECB 加密文件
        4. POST 密文到 CDN
        5. POST sendmessage (带 encrypt_query_param + aes_key)
        """
        plaintext = Path(file_path).read_bytes()

        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        filesize = _aes_padded_size(rawsize)
        aeskey_hex = aes_key.hex()

        # 1. 获取上传 URL
        upload_resp = await self._get_upload_url(
            to_user_id=to_user,
            filekey=filekey,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=filesize,
            aeskey_hex=aeskey_hex,
        )
        print(f"[weixin] getuploadurl response: {json.dumps(upload_resp, ensure_ascii=False)}")

        # 2. 确定上传 URL
        upload_full_url = str(upload_resp.get("upload_full_url") or "")
        upload_param = str(upload_resp.get("upload_param") or "")
        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = _cdn_upload_url(self.cdn_base_url, upload_param, filekey)
        else:
            raise ILinkError(-1, "getuploadurl returned no URL")

        # 3. AES-ECB 加密
        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)

        # 4. 上传密文到 CDN
        encrypted_query_param = await self._upload_ciphertext(upload_url, ciphertext)
        print(f"[weixin] CDN upload OK, encrypt_query_param={encrypted_query_param[:30]}...")

        # 5. 构造 media item 并发送
        aes_key_for_api = base64.b64encode(
            aes_key.hex().encode("ascii")
        ).decode("ascii")

        return await self._send_media_msg(
            to_user=to_user,
            encrypt_query_param=encrypted_query_param,
            aes_key_for_api=aes_key_for_api,
            ciphertext_size=len(ciphertext),
            plaintext_size=rawsize,
            filename=Path(file_path).name,
            rawfilemd5=rawfilemd5,
            context_token=context_token,
        )

    # ── 媒体内部 ────────────────────────────────────────────────────────

    async def _get_upload_url(
        self,
        to_user_id: str,
        filekey: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey_hex: str,
    ) -> Dict[str, Any]:
        """获取 CDN 上传 URL（对照 Hermes _get_upload_url）。"""
        await self.ensure_sessions()

        body = {
            "filekey": filekey,
            "media_type": 1,  # image
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        }

        async with self._send_session.post(
            f"{self.base_url}/ilink/bot/getuploadurl",
            json=body,
            headers=self._common_headers(),
        ) as resp:
            raw = await resp.text()
            return json.loads(raw)

    async def _upload_ciphertext(
        self, upload_url: str, ciphertext: bytes
    ) -> str:
        """上传密文到微信 CDN，返回 encrypt_query_param。

        关键：encrypt_query_param 在响应头 X-Encrypted-Param 中！
        对照 Hermes _upload_ciphertext (L551-576)。
        """
        async with self._send_session.post(
            upload_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        ) as resp:
            if resp.status == 200:
                encrypted_param = resp.headers.get("X-Encrypted-Param")
                if encrypted_param:
                    await resp.read()
                    return encrypted_param
                raw = await resp.text()
                raise ILinkError(-1, f"CDN upload missing X-Encrypted-Param header: {raw[:200]}")
            raw = await resp.text()
            raise ILinkError(-1, f"CDN upload HTTP {resp.status}: {raw[:200]}")

    async def _send_media_msg(
        self,
        to_user: str,
        encrypt_query_param: str,
        aes_key_for_api: str,
        ciphertext_size: int,
        plaintext_size: int,
        filename: str,
        rawfilemd5: str,
        context_token: str = "",
    ) -> Dict[str, Any]:
        """发送图片消息（对照 Hermes _outbound_media_builder image_item 结构）。"""
        await self.ensure_sessions()

        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user,
                "client_id": _make_client_id("whimg"),
                "message_type": 2,
                "message_state": 2,
                "item_list": [{
                    "type": 2,  # ITEM_IMAGE
                    "image_item": {
                        "media": {
                            "encrypt_query_param": encrypt_query_param,
                            "aes_key": aes_key_for_api,
                            "encrypt_type": 1,
                        },
                        "mid_size": ciphertext_size,
                    },
                }],
                "context_token": context_token,
            },
            "base_info": {"channel_version": CLIENT_VERSION},
        }

        async with self._send_session.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            json=body,
            headers=self._common_headers(),
        ) as resp:
            raw = await resp.text()
            return json.loads(raw)

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _check_errcode(self, data: Dict[str, Any]) -> None:
        """检查 API 响应中的错误码。"""
        errcode = data.get("ret", data.get("errcode", 0))
        if errcode is None:
            errcode = 0

        if errcode == 0:
            return
        if errcode == -14:
            raise SessionExpired()
        raise ILinkError(
            errcode=errcode,
            errmsg=data.get("errmsg", data.get("message", "unknown error")),
        )


# ── AES 加密工具（对照 Hermes _aes128_ecb_encrypt + _pkcs7_pad）───────


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """PKCS7 填充。"""
    pad_len = block_size - len(data) % block_size
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密 + PKCS7 填充。"""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes_padded_size(size: int) -> int:
    """计算 PKCS7 填充后的加密尺寸。"""
    return ((size + 1 + 15) // 16) * 16


def _cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    """构造 CDN 上传 URL。"""
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )
