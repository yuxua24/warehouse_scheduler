# 微信接入终极方案：iLink Bot API（Hermes 同款）

> 来源：Hermes Agent 官方中文文档 https://hermes-doc.aigc.green/user-guide/messaging/weixin
> 这是之前分析中完全遗漏的方案——**腾讯官方 API**，不需要逆向、不需要 Windows、不需要公网。

---

## 一、重大发现：腾讯有官方的个人微信 Bot API

之前分析的三条路（itchat、wxauto、Wechaty）都是**民间逆向**方案。Hermes Agent 的微信接入走的是一条完全不同的路：

> **iLink Bot API** — 腾讯官方提供的个人微信机器人接口
> Base URL: `https://ilinkai.weixin.qq.com`

### 这意味着什么？

| | 之前分析的 wxauto | Hermes 在用的 iLink |
|------|------|------|
| 性质 | 民间 UI 自动化 | **腾讯官方 API** |
| 运行环境 | 仅 Windows | **任何 OS**（纯 HTTP） |
| 需要微信客户端 | 是，必须打开 | **不需要** |
| 需要公网 | 否 | **不需要** |
| 封号风险 | 🟡 有 | **🟢 官方 API，不会封** |
| 扫码 | 每次重启客户端 | **一次，token 持久化** |
| 群聊 | ✅ | ❌（iLink 机器人身份的限制） |
| 私聊 | ✅ | ✅ |
| 媒体（图片/视频） | ❌ | ✅ |
| "正在输入…" 状态 | ❌ | ✅ |
| Markdown 渲染 | ❌ | ✅ 原生支持 |

---

## 二、iLink Bot API 架构

```
┌─────────────────────────────────────────────────┐
│              腾讯微信服务器                        │
│         https://ilinkai.weixin.qq.com            │
│                                                  │
│   ① 扫码 → 返回 account_id + token               │
│   ② GET /getupdates (长轮询 35s) → 收到消息       │
│   ③ POST /send → 发送消息                        │
│                                                  │
│   全程 HTTP，无 WebSocket，无 Webhook             │
└──────────────────────┬──────────────────────────┘
                       │  HTTP Long Polling
                       │  （不需要公网！）
┌──────────────────────▼──────────────────────────┐
│              你的 Mac / 服务器                    │
│                                                  │
│   Python 代码 ──► Workflow.run()                  │
│   pip install aiohttp cryptography               │
└──────────────────────────────────────────────────┘
```

**核心原理**：长轮询（Long Polling）— 你的代码主动向腾讯服务器发 HTTP GET 请求，服务器保持连接 35 秒，有消息就返回，没有就超时后再发下一个请求。**信息是拉取（pull）的，不是推送（push）的，所以不需要公网 IP 或回调 URL。**

---

## 三、与 QQ 方案的对比（更新版）

最新排名（加入 iLink 后）：

| | 🥇 iLink 微信 | 🥈 QQ (NapCat) | 🥉 企业微信 |
|------|------|------|------|
| 性质 | ✅ 腾讯官方 API | 🟡 社区逆向协议 | ✅ 企业微信官方 |
| 需要公网 | ❌ | ❌ | ✅ |
| 需要额外进程 | ❌ | ✅ (NapCat) | ❌ |
| 扫码 | 一次，token 持久 | 一次，hotReload | 不需要 |
| 系统要求 | **任何 OS** | 任何 OS | 任何 OS |
| 封号风险 | **无（官方）** | 低 | 无 |
| 私聊 | ✅ | ✅ | ✅ |
| 群聊 | ❌ | ✅ | ✅ |
| Markdown | ✅ | ❌ | ✅（有限） |
| 媒体 | ✅ 图片/视频/文件/语音 | 有限 | 有限 |
| "正在输入…" | ✅ | ❌ | ❌ |
| 代码量 | ~200 行 | ~80 行 | ~500 行 |
| 合规性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

---

## 四、如果要在你的项目中实现

### 4.1 核心依赖

```txt
# requirements.txt 新增
aiohttp>=3.9.0
cryptography>=41.0.0
```

只有两个！`aiohttp` 用于 HTTP 长轮询，`cryptography` 用于媒体文件的 AES 加解密。

### 4.2 新增文件结构

```
app/channels/
├── __init__.py
├── base.py                 # Channel 抽象基类（复用）
└── weixin/
    ├── __init__.py
    ├── ilink_client.py     # iLink API 客户端（核心，约 200 行）
    ├── handler.py          # WeixinHandler（约 80 行）
    └── reply_formatter.py  # 结果格式化（约 30 行）

configs/
└── weixin_config.json      # account_id + token
```

### 4.3 `ilink_client.py` 核心设计

```python
"""iLink Bot API 客户端 — 腾讯官方个人微信机器人接口"""
import json
import asyncio
import aiohttp
from pathlib import Path

BASE_URL = "https://ilinkai.weixin.qq.com"


class ILinkClient:
    """封装 iLink Bot API 的长轮询消息收发"""
    
    def __init__(self, account_id: str, token: str):
        self.account_id = account_id
        self.token = token
        self.session = aiohttp.ClientSession()
        self._buf = self._load_sync_buf()  # 同步游标，断线重连后继续
        self._context_tokens = {}  # 每个用户的上下文令牌
    
    # ── 扫码登录（首次） ──
    async def login(self) -> dict:
        """请求二维码 → 用户扫码 → 返回 account_id + token"""
        async with self.session.get(
            f"{BASE_URL}/getqrcode",
            headers={"Authorization": f"Bearer {self.token}"}
        ) as resp:
            data = await resp.json()
            # data 包含 qrcode_url（终端显示或打印 URL）
            return data
    
    # ── 长轮询（持续运行） ──
    async def poll_loop(self, on_message):
        """长轮询循环：GET /getupdates，超时 35s，收到消息回调 on_message"""
        while True:
            try:
                async with self.session.get(
                    f"{BASE_URL}/getupdates",
                    params={"buf": self._buf, "timeout": 35},
                    headers={"Authorization": f"Bearer {self.token}"}
                ) as resp:
                    data = await resp.json()
                    
                    if data.get("errcode") == -14:
                        raise SessionExpired("登录过期，需重新扫码")
                    
                    self._buf = data.get("get_updates_buf", self._buf)
                    self._save_sync_buf(self._buf)
                    
                    for msg in data.get("updates", []):
                        await on_message(msg)
            
            except asyncio.TimeoutError:
                continue  # 正常超时，立即重试
            except SessionExpired:
                raise
            except Exception as e:
                await asyncio.sleep(2)  # 临时错误，2 秒后重试
    
    # ── 发送消息 ──
    async def send_text(self, user_id: str, text: str):
        """发送文本消息（支持 Markdown）"""
        ctx_token = self._context_tokens.get(user_id, "")
        async with self.session.post(
            f"{BASE_URL}/send",
            json={
                "account_id": self.account_id,
                "to_user": user_id,
                "msg_type": "text",
                "content": text,
                "context_token": ctx_token,
            },
            headers={"Authorization": f"Bearer {self.token}"}
        ) as resp:
            return await resp.json()
    
    # ── 上下文令牌管理 ──
    def update_context_token(self, user_id: str, token: str):
        """每条入站消息更新该用户的上下文令牌（用于回复连续性）"""
        self._context_tokens[user_id] = token
```

### 4.4 `handler.py` — 调度集成

```python
class WeixinHandler:
    """微信消息通道：通过 iLink Bot API 收发"""
    
    def __init__(self, config: dict, workflow: Workflow):
        self.client = ILinkClient(
            account_id=config["account_id"],
            token=config["token"],
        )
        self.workflow = workflow
        self.allowed_users = set(config.get("allowed_users", []))
    
    async def start(self):
        """启动长轮询循环"""
        print("🤖 微信通道已启动（iLink 长轮询）")
        await self.client.poll_loop(self._on_message)
    
    async def _on_message(self, msg: dict):
        """处理收到的消息"""
        user_id = msg["from_user"]
        text = msg.get("content", "")
        
        # 更新上下文令牌
        self.client.update_context_token(user_id, msg.get("context_token", ""))
        
        # 鉴权
        if self.allowed_users and user_id not in self.allowed_users:
            await self.client.send_text(user_id, "❌ 无权限")
            return
        
        # 调度
        try:
            state = self.workflow.run(text)
            reply = format_schedule_result(state)  # Markdown 格式
        except Exception as e:
            reply = f"❌ 调度失败: {e}"
        
        await self.client.send_text(user_id, reply)
```

### 4.5 `server.py` 改动

```python
@app.on_event("startup")
async def startup():
    config = load_config("weixin_config.json")
    if config and config.get("enabled"):
        handler = WeixinHandler(config, get_workflow())
        asyncio.create_task(handler.start())
```

### 4.6 配置 (`weixin_config.json`)

```json
{
  "enabled": true,
  "account_id": "your-account-id",
  "token": "your-bot-token",
  "allowed_users": ["wxid_xxx1", "wxid_xxx2"]
}
```

`account_id` 和 `token` 通过扫码获得（参照 Hermes 的 `hermes gateway setup` 流程）。

### 4.7 首次扫码登录流程

只需要一个辅助脚本：

```python
# scripts/weixin_login.py — 运行一次，扫码获取 account_id + token
import asyncio
import aiohttp
import json

async def main():
    async with aiohttp.ClientSession() as session:
        # 1. 请求二维码
        resp = await session.get("https://ilinkai.weixin.qq.com/getqrcode")
        data = await resp.json()
        print(f"请用微信扫描二维码: {data['qrcode_url']}")
        
        # 2. 等待扫码确认（poll）
        while True:
            resp = await session.get(
                "https://ilinkai.weixin.qq.com/checklogin",
                params={"key": data["key"]}
            )
            result = await resp.json()
            if result.get("status") == "confirmed":
                print(f"登录成功!")
                print(f"account_id: {result['account_id']}")
                print(f"token: {result['token']}")
                # 保存到配置文件
                with open("configs/weixin_config.json", "w") as f:
                    json.dump({
                        "enabled": True,
                        "account_id": result["account_id"],
                        "token": result["token"],
                    }, f, indent=2)
                break
            await asyncio.sleep(2)

asyncio.run(main())
```

---

## 五、限制须知

### ⚠️ 群聊不工作

iLink Bot 的身份是一个**机器人身份**（如 `a5ace6fd482e@im.bot`），不是普通微信用户。因此：

- ✅ **私聊完全可用** — 用户直接给机器人发消息
- ❌ **群聊通常不可用** — iLink 不会传递群消息给机器人身份
- ❌ **@ 提及不工作** — 群里 @ 微信账号不会触发机器人

对于仓储调度场景，**私聊就够了**。每个操作员加机器人为好友，直接发指令。

### ⚠️ iLink API 本身未公开文档

iLink Bot API 目前没有独立的公开文档（不像企业微信 API 有完整文档站）。Hermes Agent 的源码是目前最好的参考实现。但这不影响使用——API 本身是稳定的，Hermes 团队已经踩过坑了。

---

## 六、文件改动清单（最终版）

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `app/channels/weixin/ilink_client.py` | iLink API 客户端（~200 行） |
| **新增** | `app/channels/weixin/handler.py` | 消息处理 + 调度集成（~80 行） |
| **新增** | `app/channels/weixin/reply_formatter.py` | 结果格式化为 Markdown（~30 行） |
| **新增** | `app/channels/weixin/__init__.py` | 模块导出 |
| **新增** | `scripts/weixin_login.py` | 首次扫码获取 account_id + token |
| **新增** | `configs/weixin_config.example.json` | 配置模板（提交 Git） |
| **修改** | `app/api/server.py` | 启动时初始化微信通道（~15 行） |
| **修改** | `requirements.txt` | 新增 `aiohttp`, `cryptography` |
| **修改** | `.gitignore` | 排除 `weixin_config.json` |

**总计**：新增 5 个文件，修改 3 个文件，约 350 行代码。

---

## 七、终极推荐排名（修正版）

| 排名 | 方案 | 一句话 |
|------|------|--------|
| 🥇 | **iLink 微信** | 腾讯官方 API、无需公网、全平台、私聊完美 |
| 🥈 | **QQ (NapCat)** | 社区成熟、全平台、支持群聊，但非官方 |
| 🥉 | 企业微信自建应用 | 最合规，但需要公网服务器 |

**如果只做私聊场景（仓库人员私信机器人调度），iLink 微信就是最优解。**
