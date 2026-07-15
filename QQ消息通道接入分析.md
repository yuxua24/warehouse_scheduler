# QQ 消息通道接入分析

> 分析如何通过 QQ 接入智能仓储调度系统，让仓库人员通过 QQ 发送调度指令并接收结果。

---

## 一、结论：QQ 比微信简单得多

**好消息**：QQ 的 Bot 生态是中文互联网开源社区最成熟的消息平台之一，比个人微信接入**容易 10 倍**，比企业微信**也更简单**。

| 对比维度 | 企业微信 | 个人微信 | **QQ** |
|---------|---------|---------|--------|
| 接入难度 | ⭐⭐⭐ 中等 | ⭐⭐ 简单但风险大 | ⭐ 简单 |
| 需要公网服务器 | 是（回调 URL） | 否 | **否** |
| 需要扫码 | 否 | 是 | 扫码一次即可 |
| 封号风险 | 无 | 🔴 有 | 🟢 低（社区庞大） |
| 需要付费 | 否 | 否 | **否** |
| 开源生态 | 无 | 碎片化 | 🟢 **非常成熟** |
| API 稳定性 | 🟢 官方 API | 🔴 逆向协议 | 🟡 OneBot 标准协议 |
| macOS 可用 | 是 | 仅 Win（wxauto） | **是** |

---

## 二、QQ Bot 技术栈：NapCatQQ + 直连 WebSocket

### 2.1 核心架构

```
┌─────────────────────────────────────┐
│  你的 Mac / 服务器                    │
│                                     │
│  ┌───────────┐    ┌──────────────┐  │
│  │ QQ 客户端   │◄──│  NapCatQQ    │  │
│  │ (NTQQ)    │    │  (协议端)     │  │
│  │           │    │  端口: 3001   │  │
│  └───────────┘    └──────┬───────┘  │
│                          │          │
│                    WebSocket         │
│                   (OneBot 协议)       │
│                          │          │
│                  ┌───────▼───────┐  │
│                  │  Python 代码   │  │
│                  │  (你的调度系统) │  │
│                  └───────────────┘  │
└─────────────────────────────────────┘
```

**原理**：
1. NapCatQQ 作为一个"桥接器"运行在本地，它连接到你的 QQ 客户端
2. NapCatQQ 将 QQ 消息转换为标准的 **OneBot 协议**（JSON over WebSocket）
3. 你的 Python 代码连接 NapCatQQ 的 WebSocket，收发消息就像调 API
4. 不需要公网服务器，不需要配置回调 URL，**全部在本机完成**

### 2.2 与微信方案的对比

```
微信:    手机微信 ──(扫码)──► wxauto(Windows) ──(UIAutomation)──► Python
         ❌ 仅 Windows    ❌ 模拟操作，脆弱    ❌ 微信版本更新就挂

QQ:      QQ客户端 ──(协议)──► NapCatQQ ──(WebSocket)──► Python
         ✅ macOS/Win/Linux  ✅ 标准协议，稳定   ✅ 社区庞大，更新快
```

---

## 三、技术方案：两种连接方式

### 方式 A：正向 WebSocket（最简单，推荐）

Python 作为客户端，**主动连接** NapCatQQ：

```python
import json
import asyncio
import websockets

async def main():
    uri = "ws://localhost:3001"  # NapCatQQ 默认端口
    
    async with websockets.connect(uri) as ws:
        # 收到消息
        while True:
            raw = await ws.recv()
            event = json.loads(raw)
            
            if event.get("post_type") == "message":
                user_id = event["sender"]["user_id"]
                text = event["raw_message"]
                
                # 调用调度引擎
                result = workflow.run(text)
                reply = format_result(result)
                
                # 发送回复
                await ws.send(json.dumps({
                    "action": "send_msg",
                    "params": {
                        "user_id": user_id,
                        "message": reply,
                    }
                }))

asyncio.run(main())
```

### 方式 B：反向 WebSocket

NapCatQQ 作为客户端，**主动连接**你的 Python 服务。适合 Python 服务重启频繁的场景。

---

## 四、接入步骤

### 4.1 安装 NapCatQQ

```bash
# 方式 1：从 GitHub Release 下载
# https://github.com/NapNeko/NapCatQQ/releases
# 下载对应系统的版本，解压即可

# 方式 2：用 Docker（推荐，隔离干净）
docker run -d \
  --name napcat \
  -p 3001:3001 \
  -v ./napcat_data:/app/data \
  mlikiowa/napcat-docker
```

### 4.2 配置 NapCatQQ

启动后会生成 WebUI 配置页面（默认 `http://localhost:6099`），在页面上：
1. 扫码登录 QQ（一次即可，支持热重载）
2. 确认 WebSocket 端口（默认 3001）
3. 配置通信方式为「正向 WebSocket」

### 4.3 Python 端代码

**新增依赖**：

```txt
websockets>=12.0
```

**新增文件**（极简，仅需 1 个文件）：

```
app/channels/
├── __init__.py
├── base.py                 # Channel 抽象基类（从之前的分析复用）
└── qq_handler.py           # QQ 通道实现（约 80 行）

configs/
└── qq_config.json          # QQ 通道配置
```

### 4.4 `qq_handler.py` 完整设计

```python
"""QQ 消息通道：通过 NapCatQQ (OneBot 协议) 收发消息"""
import json
import asyncio
import threading
import websockets
from app.orchestration.workflow import Workflow

class QQHandler:
    """QQ 个人消息通道
    
    通过 OneBot 协议（WebSocket）连接 NapCatQQ，
    监听 QQ 私聊消息，将文本作为调度指令处理。
    """
    
    def __init__(self, config: dict, workflow: Workflow):
        self.uri = config.get("ws_uri", "ws://localhost:3001")
        self.allowed_users = set(config.get("allowed_users", []))
        self.workflow = workflow
        self._running = False
    
    def start(self):
        """启动（独立线程运行 asyncio 事件循环）"""
        self._running = True
        threading.Thread(target=self._run_loop, daemon=True).start()
    
    def _run_loop(self):
        asyncio.run(self._listen())
    
    async def _listen(self):
        """连接 NapCatQQ WebSocket 并持续监听"""
        while self._running:
            try:
                async with websockets.connect(self.uri) as ws:
                    print(f"🤖 QQ 通道已连接: {self.uri}")
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self._handle_event(ws, json.loads(raw))
                        except asyncio.TimeoutError:
                            continue  # 正常超时，继续等
            except Exception as e:
                print(f"QQ 连接断开，5 秒后重连: {e}")
                await asyncio.sleep(5)
    
    async def _handle_event(self, ws, event: dict):
        """处理一条 OneBot 事件"""
        if event.get("post_type") != "message":
            return
        
        msg_type = event.get("message_type")
        if msg_type != "private":
            return  # 仅处理私聊（群聊需额外处理）
        
        user_id = str(event["sender"]["user_id"])
        text = event.get("raw_message", "").strip()
        
        # 鉴权
        if self.allowed_users and user_id not in self.allowed_users:
            await self._send(ws, user_id, "❌ 无权限")
            return
        
        # 帮助
        if text in ("帮助", "help", "/help"):
            await self._send(ws, user_id, HELP_TEXT)
            return
        
        # 调度
        try:
            state = self.workflow.run(text)
            reply = format_schedule_result(state)
        except Exception as e:
            reply = f"❌ 调度失败: {e}"
        
        await self._send(ws, user_id, reply)
    
    async def _send(self, ws, user_id: str, message: str):
        """发送私聊消息"""
        await ws.send(json.dumps({
            "action": "send_private_msg",
            "params": {
                "user_id": int(user_id),
                "message": message,
            }
        }))
```

### 4.5 `server.py` 改动

```python
# 新增导入
from app.channels.qq_handler import QQHandler

# 在启动事件中初始化 QQ 通道
@app.on_event("startup")
async def startup():
    qq_config = load_config("qq_config.json")
    if qq_config and qq_config.get("enabled"):
        handler = QQHandler(qq_config, get_workflow())
        handler.start()
```

**改动量**：约 15 行。

---

## 五、与其他方案的终局对比

| | QQ (NapCat) | 企业微信 | 个人微信 (wxauto) |
|------|------|------|------|
| 合规性 | 🟡 灰产（非官方） | ✅ 官方 API | ❌ 违反 ToS |
| 封号风险 | 🟢 低 | 无 | 🔴 有 |
| 需要公网 | **否** | 是 | 否 |
| 扫码频率 | 一次（hotReload） | 不需要 | 每次重启客户端 |
| 运行环境 | macOS/Win/Linux | 任意 | **仅 Windows** |
| 消息类型 | 文本 | 文本/Markdown/卡片 | 文本 |
| 多用户 | ✅ QQ 好友 | ✅ 企业通讯录 | ✅ 微信好友 |
| 群聊支持 | ✅ | ✅ | ✅ |
| 开发体验 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 生态成熟度 | 🟢 非常成熟 | 🟢 官方 | 🔴 碎片化 |
| 维护成本 | 🟢 社区活跃 | 🟢 稳定 | 🔴 需要跟进版本 |

---

## 六、QQ 方案的优缺点总结

### 优点

1. **不挑系统** — macOS、Windows、Linux 都能跑（和你的 Mac 完美契合！）
2. **不需要公网** — 全在本机跑，零网络配置
3. **社区巨大** — NapCatQQ 9.8k Star + NoneBot2 7.6k Star，有问题能搜到答案
4. **生态完整** — OneBot 协议是行业标准，有几十种适配器
5. **代码量极少** — 核心逻辑 80 行 Python，比企业微信方案（10 个文件）少得多
6. **热重载登录** — 配置一次 hotReload，重启后自动登录，不用反复扫码（微信不行）

### 缺点

1. **需要额外进程** — NapCatQQ 要一直跑着（可以 Docker 后台运行）
2. **依赖 QQ 客户端** — QQ 客户端必须保持登录（企业微信不需要）
3. **非官方** — 虽然社区庞大，但说到底不是 QQ 官方 API（QQ 官方只有频道 Robot 的 API）
4. **仅文本** — OneBot 协议主要支持文本消息（和调度场景刚好匹配）

---

## 七、如果你要接入：最小可行方案

**总共只需新增 1 个 Python 文件 + 1 个配置 + 安装 1 个依赖**：

| 步骤 | 操作 |
|------|------|
| 1 | `pip install websockets` |
| 2 | 下载 NapCatQQ，扫码登录 |
| 3 | 新建 `app/channels/qq_handler.py`（约 80 行） |
| 4 | 在 `server.py` 启动时初始化 |
| 5 | 找一个人给你 QQ 发"R1前往装卸区"，看结果 |

**半小时就能跑起来**。

---

## 八、最终推荐排序

对于你的仓库调度系统，消息通道接入的推荐优先级：

| 排名 | 方案 | 理由 |
|------|------|------|
| 🥇 | **QQ (NapCatQQ)** | 最简单、不需公网、Mac 友好、社区成熟 |
| 🥈 | 企业微信自建应用 | 官方合规、适合正式部署，但需要公网 |
| 🥉 | 个人微信 (wxauto) | 仅 Windows、封号风险、不推荐 |
