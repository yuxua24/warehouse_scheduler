# 微信接入设计方案（iLink Bot API — Hermes 同款）

> 参照 Hermes Agent 源码分析（`weixin-analysis.md`），为智能仓储机器人调度系统设计微信消息通道接入方案。
> 先分析，不修改代码。

---

## 一、方案概述

采用 **腾讯 iLink Bot API**（`https://ilinkai.weixin.qq.com`），通过 **HTTP 长轮询（Long Polling）** 实现个人微信的消息收发。

| 对比项 | Hermes 做法 | 本项目做法 |
|--------|------------|-----------|
| 代码组织 | 单文件 `weixin.py`（2379 行） | 模块化拆分（5 个文件） |
| 登录 | `hermes gateway setup` 交互式 | `scripts/weixin_login.py` 脚本 |
| 凭证存储 | `~/.hermes/weixin/accounts/` | `configs/weixin_account.json` |
| 消息处理 | → Agent 对话循环 | → `Workflow.run()` 调度引擎 |
| 回复格式 | Agent 自然语言回复 | Markdown 格式调度结果 |
| 依赖 | `aiohttp` + `cryptography` | 相同 |

---

## 二、核心 API 端点

| 端点 | 方法 | 用途 | 超时 |
|------|------|------|------|
| `/ilink/bot/get_bot_qrcode?bot_type=3` | GET | 获取登录二维码 | 35s |
| `/ilink/bot/get_qrcode_status` | GET | 轮询扫码状态 | 35s |
| `/ilink/bot/getupdates` | POST | 长轮询收消息 | 35s |
| `/ilink/bot/sendmessage` | POST | 发送消息 | 15s |
| `/ilink/bot/sendtyping` | POST | 「正在输入…」状态 | 10s |
| `/ilink/bot/getconfig` | POST | 获取 typing_ticket | 10s |
| `/ilink/bot/getuploadurl` | POST | 媒体上传 URL | 15s |

### 通用请求头

```
Authorization: Bearer <token>
AuthorizationType: ilink_bot_token
X-WECHAT-UIN: <随机 base64 字符串>
iLink-App-Id: bot
iLink-App-ClientVersion: 2.2.0
```

### 收消息请求体（长轮询）

```json
POST /ilink/bot/getupdates
{
  "get_updates_buf": "<上次的 sync_buf>",
  "base_info": {
    "channel_version": "2.2.0"
  }
}
```

### 发消息请求体

```json
POST /ilink/bot/sendmessage
{
  "msg": {
    "from_user_id": "<account_id>",
    "to_user_id": "<chat_id>",
    "client_id": "warehouse-<uuid>",
    "message_type": 1,
    "message_state": 2,
    "item_list": [
      {
        "type": 0,
        "text_item": { "text": "回复内容" }
      }
    ],
    "context_token": "<用户最后一条消息的 context_token>"
  },
  "base_info": { "channel_version": "2.2.0" }
}
```

**⚠️ context_token 是关键**：每条出站消息必须附带上一条入站消息中的 `context_token`，否则发送会失败。这是一个会话级令牌，每个用户独立维护。

---

## 三、认证流程（扫码登录）

### 完整流程图

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ 1. 请求二维码  │ ──► │ 2. 用户扫码    │ ──► │ 3. 确认登录    │
│ GET qrcode   │     │ 微信扫描二维码   │     │ 点击"确认登录"  │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                 │
                    ┌─────────────────────────────┘
                    ▼
            ┌──────────────┐
            │ 4. 获取凭证   │
            │ account_id   │
            │ token        │
            │ base_url     │
            └──────┬───────┘
                   │
                   ▼
            ┌──────────────┐
            │ 5. 保存到磁盘  │
            │ configs/      │
            │ weixin_account│
            │ .json         │
            └──────────────┘
```

### 实现细节（参照 Hermes 源码）

**第 1 步：请求二维码**

```python
GET https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
```
返回：
```json
{
  "qrcode_url": "https://...",  // 二维码图片 URL
  "key": "uuid-xxxx",           // 用于后续状态查询
  "status": "wait"
}
```

**第 2 步：轮询扫码状态**

```python
GET https://ilinkai.weixin.qq.com/ilink/bot/get_qrcode_status?key=<uuid>
```

状态枚举：

| status | 含义 | 操作 |
|--------|------|------|
| `"wait"` | 等待扫码 | 继续轮询（每 2 秒） |
| `"scaned"` | 已扫码，等待确认 | 提示用户在微信中点击确认 |
| `"scaned_but_redirect"` | 重定向 | 切换到新的 base_url |
| `"expired"` | 二维码过期 | 重新获取（最多 3 次） |
| `"confirmed"` | 确认成功 | 提取 account_id、token |

**第 3 步：获取凭证**

`status == "confirmed"` 时，响应中包含：

```json
{
  "status": "confirmed",
  "account_id": "a5ace6fd482e@im.bot",
  "token": "ilink_bot_token_xxxx",
  "base_url": "https://ilinkai.weixin.qq.com"
}
```

**第 4 步：持久化**

```json
// 保存到 configs/weixin_account.json
{
  "account_id": "a5ace6fd482e@im.bot",
  "token": "ilink_bot_token_xxxx",
  "base_url": "https://ilinkai.weixin.qq.com",
  "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
  "created_at": "2026-07-15T10:30:00"
}
```

---

## 四、消息接收（长轮询循环）

### 完整流程

```
┌─────────────────────────────────────────────────┐
│                 _poll_loop()                     │
│                                                  │
│  while _running:                                 │
│    ┌──────────────────────────────────────┐      │
│    │ POST /ilink/bot/getupdates           │      │
│    │ body: {get_updates_buf, base_info}  │      │
│    │ timeout: 35s                         │      │
│    └──────────────┬───────────────────────┘      │
│                   │                              │
│         ┌─────────┴─────────┐                    │
│         │                   │                    │
│    成功 ▼              失败 ▼                    │
│  ┌──────────────┐  ┌──────────────┐             │
│  │ 更新 sync_buf │  │ errcode=-14? │             │
│  │ 持久化到磁盘   │  │ → session 过期│             │
│  │ 分发 messages │  │ → 暂停 10min │             │
│  └──────┬───────┘  │ 其他错误:     │             │
│         │          │ → 重试/backoff│             │
│         ▼          └──────────────┘             │
│  for msg in messages:                           │
│    asyncio.create_task(                         │
│      _process_message(msg)                      │
│    )                                            │
└─────────────────────────────────────────────────┘
```

### sync_buf（同步游标）

- 每条轮询响应中，iLink 返回 `get_updates_buf`（不透明字符串）
- **必须持久化到磁盘**（`configs/weixin_sync_buf.txt`），否则重启后可能收到重复消息或丢失消息
- 下次轮询时携带此值

### 错误处理策略（参照 Hermes）

| 错误类型 | 行为 |
|---------|------|
| 正常超时（35s） | 立即重新轮询 |
| `errcode == -14`（session 过期） | 暂停 10 分钟，需要重新扫码 |
| `errcode == -2`（频率限制） | 3 倍指数退避 + 熔断器（30s 窗口内 1 次触发冷却 30s） |
| 其他临时错误 | 最多连续 3 次 2 秒退避，之后 30 秒冷却 |
| 网络断开 | 自动重连，无限重试 |

### 重连恢复

1. 从磁盘加载 `sync_buf`（`configs/weixin_sync_buf.txt`）
2. 从磁盘加载 `context_tokens`（`configs/weixin_context_tokens.json`）
3. 从磁盘加载 `account_id` + `token`（`configs/weixin_account.json`）
4. 恢复轮询

**这意味着服务器重启后无需重新扫码**。

---

## 五、消息处理

### 5.1 消息去重（双重机制）

iLink API 可能因网络重试推送重复消息。参照 Hermes 实现两级去重：

```
1. message_id 去重        → 内存 LRU Set，300 秒 TTL
2. 内容 MD5 指纹去重       → key = "content:<sender_id>:<md5(text)>"，300 秒 TTL
```

### 5.2 过滤

```
收到消息
  │
  ├─ sender_id == account_id?  → 忽略（自己的消息）
  ├─ message_type != "text"?   → 忽略（本项目仅处理文本指令）
  ├─ DM policy != "open"?      → 检查白名单
  │
  ▼
保存 context_token（用于后续回复）
  │
  ▼
提取 text 内容
  │
  ▼
调用 Workflow.run(text)
  │
  ▼
format_schedule_result(state)
  │
  ▼
发送回复（携带 context_token）
```

### 5.3 访问控制

| 策略 | 行为 |
|------|------|
| `open` | 任何人都可以发送指令（默认） |
| `allowlist` | 仅 `allowed_users` 中的微信 ID 可用 |
| `disabled` | 忽略所有私聊 |

对于仓库场景，建议设置为 `allowlist`，仅允许仓库操作员的微信 ID。

### 5.4 文本批处理（可选）

微信用户可能在短时间内发送多条短消息。参照 Hermes 的做法：

- 3 秒静默窗口内收到的多条文本，合并后一次性处理
- 单条超过 1800 字符时，窗口延长到 5 秒（可能是客户端自动拆分的长消息）
- 合并时用换行符连接

对于调度场景，这可以处理「R1去装卸区」+「R2去货架B」分开发送的情况。

---

## 六、消息发送

### 6.1 核心 API

```
POST /ilink/bot/sendmessage
```

### 6.2 必须携带 context_token

发送消息时，必须附带上**该用户最后一条入站消息**中的 `context_token`。否则 API 返回错误。

因此系统需要为每个私聊用户维护一个 `context_token` 缓存：

```python
# 内存缓存 + 磁盘持久化
context_tokens: dict[str, str] = {}

# 收到消息时更新
def update_context_token(user_id: str, token: str):
    context_tokens[user_id] = token
    save_to_disk(context_tokens)  # 持久化

# 发送消息时读取
def get_context_token(user_id: str) -> str:
    return context_tokens.get(user_id, "")
```

持久化到 `configs/weixin_context_tokens.json`。

### 6.3 消息格式化

微信客户端通过 iLink API 可以**原生渲染 Markdown**，因此回复可以用 Markdown 格式：

```markdown
✅ 调度成功
━━━━━━━━━━━━━━
🤖 **R1** → 装卸区 · 15 步 · makespan=14
🤖 **R2** → 货架B · 12 步 · makespan=11
━━━━━━━━━━━━━━
成功率: **100%** | 耗时: 245ms
冲突: 0 | 重规划: 1 次
```

### 6.4 消息分片

iLink API 对单条消息有长度限制（约 4000 字符）。超过限制时需要分片发送，每片之间延迟 1.5 秒。分片时尽量在段落边界、代码块边界处断开。

### 6.5 「正在输入…」状态（可选）

在 Agent 处理消息时，可以向 iLink API 发送「正在输入…」信号：

```
POST /ilink/bot/sendtyping
```

这会在用户微信端显示「对方正在输入…」，提升体验。处理完成后发送停止信号。

---

## 七、文件与模块设计

### 7.1 新增文件

```
app/channels/
├── __init__.py                # 模块导出
├── base.py                    # Channel 抽象基类
└── weixin/
    ├── __init__.py
    ├── ilink_client.py        # iLink API 客户端（HTTP 请求封装）
    ├── auth.py                # 扫码登录 + token 管理
    ├── poller.py              # 长轮询循环 + 错误处理
    ├── message_handler.py     # 消息去重、过滤、处理
    ├── reply_formatter.py     # 调度结果 → Markdown 格式化
    └── context_store.py       # context_token + sync_buf 持久化

scripts/
│   └── weixin_login.py        # 首次扫码登录脚本

configs/
│   └── weixin_config.example.json
```

### 7.2 各模块职责

#### `ilink_client.py` — API 封装层（~150 行）

封装所有 iLink HTTP 请求：

```python
class ILinkClient:
    def __init__(self, base_url, token, account_id):
        self.base_url = base_url
        self.token = token
        self.account_id = account_id
        self.session = aiohttp.ClientSession()
    
    async def getupdates(self, sync_buf: str) -> dict:
        """长轮询收消息"""
    
    async def sendmessage(self, to_user: str, text: str, context_token: str) -> dict:
        """发送文本消息"""
    
    async def sendtyping(self, to_user: str, typing_ticket: str, action: str):
        """发送「正在输入…」"""
    
    async def getconfig(self) -> dict:
        """获取 typing_ticket"""
    
    def _headers(self) -> dict:
        """构造通用请求头"""
    
    def _make_client_id(self) -> str:
        """生成 client_id（warehouse-<uuid>）"""
```

#### `auth.py` — 扫码登录（~100 行）

```python
class WeixinAuth:
    @staticmethod
    async def get_qrcode() -> dict:
        """获取二维码，返回 {qrcode_url, key}"""
    
    @staticmethod
    async def poll_qrcode_status(key: str) -> dict:
        """轮询扫码状态，返回 {status, ...}"""
    
    @staticmethod
    async def qr_login() -> dict:
        """完整扫码登录流程，返回 {account_id, token, base_url}"""
    
    @staticmethod
    def save_account(account: dict, path: str):
        """保存账户凭证到 JSON 文件"""
    
    @staticmethod
    def load_account(path: str) -> dict:
        """从 JSON 文件加载账户凭证"""
```

#### `poller.py` — 长轮询循环（~120 行）

```python
class MessagePoller:
    def __init__(self, client: ILinkClient, handler, context_store):
        self.client = client
        self.handler = handler      # async callback: on_message(msg)
        self.context_store = context_store
        self._running = False
    
    async def start(self):
        """启动轮询循环"""
        self._running = True
        while self._running:
            try:
                resp = await self.client.getupdates(
                    self.context_store.get_sync_buf()
                )
                self.context_store.save_sync_buf(resp["get_updates_buf"])
                
                for msg in resp.get("updates", []):
                    asyncio.create_task(self.handler(msg))
            
            except asyncio.TimeoutError:
                continue
            except SessionExpired:
                await asyncio.sleep(600)  # 暂停 10 分钟
            except Exception as e:
                await self._handle_error(e)
    
    async def _handle_error(self, error):
        """错误退避策略：2s → 2s → 2s → 30s backoff"""
    
    async def stop(self):
        self._running = False
```

#### `message_handler.py` — 消息处理（~100 行）

```python
class MessageHandler:
    def __init__(self, workflow, client, context_store, config):
        self.workflow = workflow
        self.client = client
        self.context_store = context_store
        self.allowed_users = set(config.get("allowed_users", []))
        self.dm_policy = config.get("dm_policy", "allowlist")
        self.deduplicator = MessageDeduplicator(ttl=300)
    
    async def handle(self, msg: dict):
        """处理一条 iLink 消息"""
        # 1. 去重
        if self.deduplicator.is_duplicate(msg):
            return
        
        # 2. 过滤自己的消息
        if msg["sender_id"] == self.client.account_id:
            return
        
        # 3. 仅处理文本
        if msg.get("message_type") != "text":
            return
        
        # 4. 访问控制
        if self.dm_policy == "allowlist":
            if msg["sender_id"] not in self.allowed_users:
                await self.client.sendmessage(
                    msg["sender_id"], "❌ 无权限",
                    msg.get("context_token", "")
                )
                return
        
        # 5. 保存 context_token
        self.context_store.update_context_token(
            msg["sender_id"], msg.get("context_token", "")
        )
        
        # 6. 调度
        text = msg.get("content", "")
        result_text = await self._process_instruction(text)
        
        # 7. 回复
        ctx_token = self.context_store.get_context_token(msg["sender_id"])
        await self.client.sendmessage(msg["sender_id"], result_text, ctx_token)
    
    async def _process_instruction(self, text: str) -> str:
        """处理指令，返回格式化的回复文本"""
        if text.strip() in ("帮助", "help"):
            return HELP_TEXT
        try:
            state = self.workflow.run(text)
            return format_result(state)
        except Exception as e:
            return f"❌ 调度失败: {e}"
```

#### `context_store.py` — 状态持久化（~60 行）

```python
class ContextStore:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.sync_buf_file = self.data_dir / "weixin_sync_buf.txt"
        self.context_tokens_file = self.data_dir / "weixin_context_tokens.json"
    
    # sync_buf
    def get_sync_buf(self) -> str: ...
    def save_sync_buf(self, buf: str): ...
    
    # context_tokens
    def get_context_token(self, user_id: str) -> str: ...
    def update_context_token(self, user_id: str, token: str): ...
    
    # 持久化
    def _load_tokens(self) -> dict: ...
    def _save_tokens(self, tokens: dict): ...
```

### 7.3 修改的文件

| 文件 | 改动 | 行数 |
|------|------|------|
| `app/api/server.py` | 启动时初始化微信通道（`startup` 事件中 `asyncio.create_task`） | +20 |
| `requirements.txt` | 新增 `aiohttp>=3.9.0`, `cryptography>=41.0.0` | +2 |
| `.gitignore` | 新增 `configs/weixin_account.json`, `configs/weixin_sync_buf.txt`, `configs/weixin_context_tokens.json` | +3 |

### 7.4 配置模板 (`configs/weixin_config.example.json`)

```json
{
  "enabled": false,
  "account_id": "",
  "token": "",
  "base_url": "https://ilinkai.weixin.qq.com",
  "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
  "dm_policy": "allowlist",
  "group_policy": "disabled",
  "allowed_users": [],
  "data_dir": "configs"
}
```

### 7.5 首次扫码登录脚本 (`scripts/weixin_login.py`)

```
运行: python scripts/weixin_login.py

输出:
  🔳 请用微信扫描以下二维码:
  ████████████████████████
  ██                    ██
  ██  (ASCII QR Code)   ██
  ██                    ██
  ████████████████████████
  
  等待扫码... (已等待 5s)
  ✅ 已扫码，请在微信中点击「确认登录」
  等待确认... (已等待 3s)
  ✅ 登录成功！
  
  account_id: a5ace6fd482e@im.bot
  已保存到 configs/weixin_account.json
  
  请编辑 configs/weixin_config.json，设置:
    "enabled": true
    "account_id": "a5ace6fd482e@im.bot"
    "token": "ilink_bot_token_xxxx"
    "allowed_users": ["<你的微信ID>"]
```

---

## 八、server.py 集成方式

```python
# app/api/server.py 改动示意

from app.channels.weixin.ilink_client import ILinkClient
from app.channels.weixin.auth import WeixinAuth
from app.channels.weixin.poller import MessagePoller
from app.channels.weixin.message_handler import MessageHandler
from app.channels.weixin.context_store import ContextStore

# 全局变量
_weixin_task: asyncio.Task = Noneasync def start_weixin():
    """启动微信通道"""
    config_path = CONFIGS_DIR / "weixin_config.json"
    if not config_path.exists():
        logger.info("微信通道未配置，跳过")
        return
    
    config = json.loads(config_path.read_text())
    if not config.get("enabled"):
        return
    
    # 加载账户凭证
    account_path = CONFIGS_DIR / "weixin_account.json"
    account = json.loads(account_path.read_text())
    
    # 初始化组件
    client = ILinkClient(
        base_url=account.get("base_url", "https://ilinkai.weixin.qq.com"),
        token=account["token"],
        account_id=account["account_id"],
    )
    context_store = ContextStore(data_dir=str(CONFIGS_DIR))
    handler = MessageHandler(
        workflow=get_workflow(),
        client=client,
        context_store=context_store,
        config=config,
    )
    poller = MessagePoller(client, handler.handle, context_store)
    
    # 启动轮询（后台任务）
    global _weixin_task
    _weixin_task = asyncio.create_task(poller.start())
    logger.info("🤖 微信通道已启动")

@app.on_event("startup")
async def startup():
    await start_weixin()

@app.on_event("shutdown")
async def shutdown():
    if _weixin_task:
        _weixin_task.cancel()
```

---

## 九、数据流总览

```
微信用户 "R1前往装卸区，R2前往货架B"
         │
         ▼
┌────────────────────────────────────────┐
│        iLink Bot API (腾讯云)           │
│  https://ilinkai.weixin.qq.com          │
│                                         │
│  POST /getupdates ←── long poll (35s) ──│
│  POST /sendmessage ←──── 发送回复 ──────│
└────────────┬───────────────────────────┘
             │ HTTP
             ▼
┌────────────────────────────────────────┐
│          app/channels/weixin/           │
│                                         │
│  poller.py ──→ message_handler.py      │
│                   │                     │
│                   ▼                     │
│              Workflow.run(text)         │
│                   │                     │
│                   ▼                     │
│            format_result(state)         │
│                   │                     │
│                   ▼                     │
│          ilink_client.sendmessage()     │
└────────────────────────────────────────┘
             │
             ▼
┌────────────────────────────────────────┐
│           configs/ (磁盘)               │
│  weixin_account.json      (凭证)        │
│  weixin_config.json       (启用+白名单)  │
│  weixin_sync_buf.txt      (游标)        │
│  weixin_context_tokens.json (会话令牌)   │
└────────────────────────────────────────┘
```

---

## 十、限制与注意事项

### 10.1 群聊不可用

iLink Bot 身份（如 `a5ace6fd482e@im.bot`）是**机器人身份**，不是普通微信用户。因此：
- ✅ 私聊完全可用
- ❌ **不能被拉入普通微信群**
- ❌ 群聊消息不会推送到 Bot

对于仓库调度场景，**私聊就够了**。

### 10.2 不支持消息编辑

微信不支持编辑已发送的消息。这意味着不能像 Telegram Bot 那样「边生成边编辑」实现流式输出。必须等待 `Workflow.run()` 完整执行后再一次性发送回复。

### 10.3 session 过期

`errcode == -14` 表示登录 session 过期，需要**重新扫码**。目前 iLink API 的 session 有效期不确定，可能在数天到数周之间。当发生此错误时，系统需要：

1. 记录 ERROR 日志
2. 暂停轮询 10 分钟
3. 通知管理员（后续可发邮件/企业微信通知）
4. 管理员运行 `python scripts/weixin_login.py` 重新扫码

### 10.4 多实例互斥

同一个 `token` 只能有一个轮询实例。如果启动第二个服务实例使用相同 token，第一个会被踢下线。对于单机部署这不是问题。

### 10.5 iLink API 无公开文档

iLink Bot API 目前**没有独立的公开文档**（不像企业微信 API）。所有资料来源于 Hermes Agent 源码和社区实践。但这不影响使用——API 本身是稳定的，Hermes 生产环境已在运行。

---

## 十一、与 QQ 方案的对比

| | 微信（iLink） | QQ（NapCat） |
|------|------|------|
| 性质 | 腾讯官方 API | 社区逆向协议 |
| 稳定性 | 🟢 官方 | 🟡 依赖社区维护 |
| 需要额外进程 | 否 | 是（NapCatQQ） |
| 扫码频率 | 一次，token 持久化 | 一次，hotReload |
| 群聊 | ❌ | ✅ |
| "正在输入…" | ✅ | ❌ |
| Markdown 渲染 | ✅ | 纯文本 |
| 代码复杂度 | 中等（HTTP 长轮询） | 低（WebSocket） |

---

## 十二、实施清单

| 步骤 | 内容 |
|------|------|
| □ 1 | `pip install aiohttp cryptography` |
| □ 2 | 创建 `app/channels/weixin/` 模块（6 个文件） |
| □ 3 | 创建 `scripts/weixin_login.py` |
| □ 4 | 运行扫码脚本，获取 account_id + token |
| □ 5 | 填写 `configs/weixin_config.json`（启用 + 白名单） |
| □ 6 | 修改 `app/api/server.py`（启动时初始化） |
| □ 7 | 找一个人微信私聊 Bot：「R1前往装卸区」 |
| □ 8 | 验证调度结果正确返回 |
