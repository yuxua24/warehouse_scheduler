# 融入 Hermes Agent 能力的改造方案

> 基于当前项目架构与 Hermes Agent 能力的对比分析，提出可落地的改造建议。
> 分析范围：项目全部源码（Python 后端 + React 前端）、AGENTS.md、README.md

---

## 一、当前项目 vs Hermes Agent 能力对照

| 能力维度 | 当前项目 | Hermes Agent | 差距 |
|---------|---------|-------------|------|
| 用户交互 | Web UI + CLI | Telegram/Discord/Slack/WhatsApp/Signal/CLI | ❌ 无消息通道 |
| 跨会话记忆 | 无（每次请求独立） | FTS5 会话搜索 + LLM 摘要 + MEMORY.md | ❌ 无持久化 |
| 用户画像 | 无 | Honcho dialectic user modeling | ❌ 无个性化 |
| 技能/工具系统 | 4 个硬编码工具 | 40+ 工具，动态注册，Skills Hub | ❌ 无扩展性 |
| 定时任务 | 无 | 内置 cron 调度器 | ❌ 无自动化 |
| 子代理/并行 | 单线程顺序执行 | 可派生子代理并行工作 | ❌ 无并行 |
| 自我改进 | 无 | 技能自改进、周期 nudge | ❌ 无学习 |
| MCP 集成 | 无 | 支持任意 MCP 服务器 | ❌ 无扩展 |
| AGENTS.md | 项目约束文件，1510 行 | 项目上下文 + AGENTS.md 作为构建指令 | 概念相同 |
| 确定性内核 | 80 个单元测试，强验证 | 不侧重确定性验证 | ✅ 项目优势 |

---

## 二、值得引入的能力（按优先级排序）

### 🔴 P0 — 与项目核心场景直接相关

### 🟡 P1 — 显著提升用户体验

### 🟢 P2 — 锦上添花，后续迭代

---

## 三、详细改造方案

---

### P0-1：消息通道接入（Telegram / 企业微信）

#### 现状

用户只能通过 **Web UI** 或 **CLI** 提交调度任务。这意味着：
- 仓库现场人员无法用手机发一条消息就调度
- 无法从已有的企业通讯工具（企业微信、钉钉）触发调度
- 调度结果只能回 Web UI 上看，无法推送到手机

#### 目标

```
仓库管理员在 Telegram 发消息：
"R1去装卸区，R2去充电"

→ Telegram Bot 收到消息
→ 调用 Workflow.run()
→ 结果推回 Telegram：
"✅ 调度成功，R1: 15步，R2: 4步"
```

#### 改动方案

**新增文件**：

```
app/channels/
  ├── __init__.py
  ├── base.py             # Channel 抽象基类
  ├── telegram.py         # Telegram Bot 集成
  └── wecom.py            # 企业微信 Bot 集成（可选）
```

**base.py 接口设计**：

```python
class Channel(ABC):
    @abstractmethod
    async def send_message(self, user_id: str, text: str): ...
    @abstractmethod
    async def handle_message(self, user_id: str, text: str) -> str: ...
```

**telegram.py 核心逻辑**：

```python
from telegram.ext import Application, CommandHandler, MessageHandler

# 收到消息 →
# 1. 解析文本（可能是自然语言指令）
# 2. 调用 workflow.run()
# 3. 格式化结果为可读文本
# 4. 回复用户

async def handle_message(update, context):
    text = update.message.text
    state = workflow.run(text)
    reply = format_schedule_result(state)
    await update.message.reply_text(reply)
```

**需要新增的依赖**：

```txt
python-telegram-bot>=21.0
```

或者企业微信 SDK（如果走企业微信）。

**配置新增** (`configs/channels.json`)：

```json
{
  "telegram": {
    "enabled": true,
    "bot_token": "${TELEGRAM_BOT_TOKEN}",
    "allowed_users": ["user_id_1", "user_id_2"]
  },
  "wecom": {
    "enabled": false
  }
}
```

**影响范围**：

| 文件 | 改动 |
|------|------|
| `app/api/server.py` | 启动时初始化 channels |
| `requirements.txt` | 新增 `python-telegram-bot` |
| `configs/` | 新增 `channels.json` |

**估计工作量**：2-3 天（含 Bot 注册和配置）

---

### P0-2：Cron 定时调度

#### 现状

系统是"请求→规划→返回"的一次性模式。不支持：
- "每天晚上 10 点把所有机器人送回充电区"
- "每 30 分钟检查一次机器人位置并重新规划"
- "早 8 点开启北侧通道，晚 6 点关闭"

#### 目标

用户可以在 Web UI 或配置文件中定义定时任务：

```yaml
# configs/cron_jobs.yaml
jobs:
  - name: "nightly_charge"
    schedule: "0 22 * * *"        # 每天 22:00
    instruction: "所有机器人返回充电区"
    
  - name: "morning_routine"
    schedule: "0 8 * * 1-5"       # 工作日 8:00
    instruction: "R1前往装卸区，R2前往货架B，R3前往充电区"
```

#### 改动方案

**新增文件**：

```
app/scheduler/
  ├── __init__.py
  ├── cron_manager.py      # 定时任务管理器
  └── job_store.py         # 任务持久化（JSON 文件）
```

**技术选型**：使用 `APScheduler`（轻量、进程内、支持 cron 表达式）

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

class CronManager:
    def __init__(self, workflow: Workflow):
        self.scheduler = AsyncIOScheduler()
        self.workflow = workflow
    
    def add_job(self, name: str, cron_expr: str, instruction: str):
        self.scheduler.add_job(
            self._execute, 
            'cron', 
            **parse_cron(cron_expr),
            args=[instruction],
            id=name,
            replace_existing=True,
        )
    
    async def _execute(self, instruction: str):
        state = self.workflow.run(instruction)
        # 结果可通过 channels 推送到消息平台
        await broadcast_result(state)
```

**需要新增的依赖**：

```txt
apscheduler>=3.10.0
```

**影响范围**：

| 文件 | 改动 |
|------|------|
| `app/api/server.py` | 启动时初始化 CronManager |
| `app/orchestration/workflow.py` | 增加异步接口（可选） |
| `frontend/src/components/SchedulePanel.jsx` | 增加 cron 任务管理 UI |
| `frontend/src/api.js` | 增加 cron 相关 API |
| `configs/` | 新增 `cron_jobs.yaml` 或存入 runtime JSON |

**估计工作量**：2-3 天

---

### P0-3：跨会话记忆与用户偏好

#### 现状

- 每次调度请求完全独立
- 用户每次都要完整描述"R1从左上角去装卸区"
- 系统不记得用户上次的偏好或常用模式

#### 目标

- 记住用户常用的任务模式（"把 R1 和 R2 分配到装卸区"）
- 自动补全常用参数（如果用户上次说"R1去装卸区"，这次说"再来一次"）
- 记住临时封闭偏好（"北侧通道经常关闭"）

#### 改动方案

**新增文件**：

```
app/memory/
  ├── __init__.py
  ├── memory_store.py      # JSON 文件持久化记忆
  ├── session_memory.py    # 会话级记忆（最近 N 次请求）
  └── user_profile.py      # 用户偏好模型
```

**记忆存储设计** (`memory_store.py`)：

```python
class MemoryStore:
    """基于 JSON 文件的轻量级记忆存储"""
    
    def __init__(self, path="data/memory.json"):
        self.path = path
        self._load()
    
    def record_schedule(self, request: str, result: PlanningState):
        """记录一次调度请求和结果"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "instruction": request,
            "status": result.status.value,
            "tasks": [{"robot_id": t.robot_id, "success": t.success} 
                      for t in result.task_results],
            "metrics": result.metrics.dict(),
        }
        self.history.append(entry)
        self._save()
    
    def get_recent_patterns(self, n=5):
        """获取最近的调度模式"""
        # 提取高频 robot_id → goal_location_id 组合
        pass
    
    def suggest_completion(self, partial_input: str):
        """基于历史记录补全用户输入"""
        # 模糊匹配最近使用的指令
        pass
```

**影响范围**：

| 文件 | 改动 |
|------|------|
| `app/orchestration/workflow.py` | 调度结束后调用 `memory_store.record_schedule()` |
| `app/agents/task_parser_agent.py` | 可使用记忆做输入补全 |
| `app/api/server.py` | 初始化 memory_store |
| `frontend/src/components/SchedulePanel.jsx` | 展示历史记录 / 快速复用 |
| `frontend/src/api.js` | 增加记忆相关 API |

**估计工作量**：1-2 天

---

### 🟡 P1-1：技能/工具注册系统

#### 现状

目前 4 个工具全部硬编码：
- `astar_planner.py` — 路径规划
- `conflict_detector.py` — 冲突检测
- `path_validator.py` — 路径验证
- `reservation_table.py` — 预留表

新增任何工具都需要修改源代码。

#### 目标

建立一个轻量级的工具注册表，让工具可以：
- 通过装饰器或声明式配置注册
- 暴露自己的输入/输出 Schema
- 被 LLM 动态发现和调用（参考 Hermes 的 toolset 系统）

#### 改动方案

**新增文件**：

```
app/tools/
  ├── __init__.py          # 工具注册表（装饰器 + 自动发现）
  ├── registry.py          # ToolRegistry 类
  ├── base.py              # BaseTool 抽象类
  ├── astar_planner.py     # 改造为继承 BaseTool
  ├── conflict_detector.py # 改造为继承 BaseTool
  ├── path_validator.py    # 改造为继承 BaseTool
  └── reservation_table.py # 改造为继承 BaseTool
```

**核心接口** (`base.py`)：

```python
class BaseTool(ABC):
    name: str                     # 工具唯一标识
    description: str              # LLM 可读的描述
    parameters: dict              # JSON Schema 格式的参数定义
    
    @abstractmethod
    def execute(self, **kwargs) -> dict: ...
    
    def to_openai_tool(self) -> dict:
        """转为 OpenAI Function Calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
```

**注册表** (`registry.py`)：

```python
class ToolRegistry:
    _tools: dict[str, BaseTool] = {}
    
    @classmethod
    def register(cls, tool: BaseTool):
        cls._tools[tool.name] = tool
    
    @classmethod
    def get(cls, name: str) -> BaseTool: ...
    
    @classmethod
    def all_tools(cls) -> list[BaseTool]: ...
    
    @classmethod
    def openai_tools(cls) -> list[dict]:
        """供 LLM 使用的工具列表"""
        return [t.to_openai_tool() for t in cls._tools.values()]
```

**估计工作量**：2-3 天

---

### 🟡 P1-2：子代理并行规划

#### 现状

`graph_nodes.py` 中，多台机器人的 A* 规划是**顺序执行**的：

```python
# 当前：逐个规划
for task in task_batch.tasks:
    result = astar_planner.plan(...)
```

对于 5 台机器人，每台 A* 耗时 5ms，总共就是 25ms——串行浪费了等待时间。

#### 目标

在保留确定性内核的前提下，实现**规划阶段的并行化**：

```python
# 改造后：并行规划
import asyncio

async def initial_plan(state):
    tasks = [astar_planner.plan_async(task) for task in state.task_batch.tasks]
    results = await asyncio.gather(*tasks)
```

#### 改动方案

| 文件 | 改动 |
|------|------|
| `app/tools/astar_planner.py` | 增加 `async def plan_async()` 方法 |
| `app/orchestration/graph_nodes.py` | `initial_plan` 节点改为 async，使用 `asyncio.gather` |
| `app/orchestration/graph_builder.py` | 适配 async 节点 |

**估计工作量**：1 天

---

### 🟢 P2-1：MCP 集成

#### 现状

项目没有与外部系统对接的标准方式。

#### 目标

支持 MCP（Model Context Protocol）服务器接入，让仓库调度系统可以：
- 查询外部库存系统（某货架还有多少空位）
- 对接机器人执行器（实际下发路径）
- 调用外部地图生成服务

#### 改动方案

在 `app/services/mcp_client.py` 中实现 MCP 客户端，参考 Hermes Agent 的 MCP 集成方式。

**估计工作量**：2-3 天

---

### 🟢 P2-2：结果推送与通知

#### 现状

调度完成后，结果只在 Web UI 上展示或 CLI 输出。用户不会主动刷新看结果。

#### 目标

- 调度完成后主动推送到消息通道（Telegram / 企业微信）
- 失败的调度发送告警
- 定时任务执行完成后推送执行报告

**改动方案**：

在 `app/notifications/` 下实现通知管理器，结合 P0-1 的 channels 模块。

**估计工作量**：1 天

---

## 四、总体架构变化

### 改造后的系统架构

```text
┌─────────────────────────────────────────────────────────────────┐
│                        消息通道层                                │
│  Telegram Bot │ 企业微信 │ Discord │ CLI │ Web UI               │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 统一的消息格式
┌───────────────────────────▼─────────────────────────────────────┐
│                        调度入口层                                │
│  Channel Router → Message Parser → Workflow                     │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                      记忆与学习层                                │
│  MemoryStore │ UserProfile │ PatternRecognizer                  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ 注入历史/偏好
┌───────────────────────────▼─────────────────────────────────────┐
│                    LangGraph 规划内核（不变）                     │
│  parse → plan → conflict_check → replan → validate             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                      工具层（可注册）                             │
│  A* │ ConflictDetector │ PathValidator │ ReservationTable      │
│  + 未来：CBS │ 库存查询 │ 机器人执行接口                           │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                     定时与自动化层                                │
│  CronManager │ JobStore │ NotificationManager                   │
└─────────────────────────────────────────────────────────────────┘
```

### 新增目录结构

```
warehouse_scheduler/
├── app/
│   ├── channels/           # [新增] 消息通道
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── telegram.py
│   │   └── wecom.py
│   ├── memory/             # [新增] 记忆系统
│   │   ├── __init__.py
│   │   ├── memory_store.py
│   │   ├── session_memory.py
│   │   └── user_profile.py
│   ├── scheduler/          # [新增] 定时任务
│   │   ├── __init__.py
│   │   ├── cron_manager.py
│   │   └── job_store.py
│   ├── notifications/      # [新增] 通知推送
│   │   ├── __init__.py
│   │   └── notifier.py
│   ├── tools/
│   │   ├── registry.py     # [新增] 工具注册表
│   │   └── base.py         # [新增] 工具基类
│   └── api/
│       └── server.py       # [修改] 集成 channels + cron + memory
├── configs/
│   ├── channels.json       # [新增] 通道配置
│   └── cron_jobs.yaml      # [新增] 定时任务配置
├── data/
│   ├── memory.json         # [新增] 记忆数据文件
│   └── jobs.db             # [新增] 定时任务持久化
└── requirements.txt        # [修改] 新增依赖
```

---

## 五、实施路线图

### 第一阶段（核心能力，2 周）

| 周次 | 内容 | 产出 |
|------|------|------|
| 第 1 周 | P0-1 消息通道 + P0-2 Cron 定时调度 | 可用 Telegram 调度，定时任务自动执行 |
| 第 2 周 | P0-3 跨会话记忆 | 系统记住用户偏好，支持"再来一次" |

### 第二阶段（体验提升，1-2 周）

| 内容 | 产出 |
|------|------|
| P1-1 工具注册系统 | 工具可动态注册，新增工具不改核心代码 |
| P1-2 子代理并行规划 | 多机器人规划加速 |
| 通知推送 | 调度结果主动推送到消息通道 |

### 第三阶段（扩展能力，按需）

| 内容 | 说明 |
|------|------|
| P2-1 MCP 集成 | 对接库存系统、机器人执行器 |
| P2-2 自我改进循环 | 分析历史失败模式，优化重规划策略 |
| Web UI 增强 | 在界面上集成 cron 管理、历史记录 |

---

## 六、AGENTS.md 需要更新的内容

如果决定实施上述改造，需要在 AGENTS.md 中：

1. **新增已确认范围**：消息通道接入属于当前 MVP 扩展，还是下个阶段？
2. **新增业务规则**：
   - 消息通道 Bot 只能由授权用户调用（防止外部人员随意调度仓库）
   - cron 任务不得与手动指令冲突（同时调度时的优先级规则）
   - 记忆数据的安全性（是否包含敏感信息）
3. **新增模块职责**：
   - `channels/` 只负责消息收发，不包含调度逻辑
   - `memory/` 只存储，不修改调度决策
4. **新增非目标**：
   - 消息通道不是实时机器人控制系统（仍需确认）
   - cron 任务不代替真人决策（高风险操作需人工确认）

---

## 七、总结

| 能力 | 优先级 | 工作量 | 对用户的价值 | 风险 |
|------|--------|--------|-------------|------|
| 消息通道接入 | 🔴 P0 | 2-3 天 | ⭐⭐⭐ 手机端调度 | 低（独立模块） |
| Cron 定时调度 | 🔴 P0 | 2-3 天 | ⭐⭐⭐ 自动化 | 中（需处理并发） |
| 跨会话记忆 | 🔴 P0 | 1-2 天 | ⭐⭐ 减少重复输入 | 低（文件存储） |
| 工具注册系统 | 🟡 P1 | 2-3 天 | ⭐⭐ 可扩展性 | 低（接口封装） |
| 子代理并行 | 🟡 P1 | 1 天 | ⭐ 性能提升 | 低 |
| 通知推送 | 🟡 P1 | 1 天 | ⭐⭐ 及时反馈 | 低 |
| MCP 集成 | 🟢 P2 | 2-3 天 | ⭐ 外部对接能力 | 中（协议兼容） |

**建议优先实施 P0 的三项能力**，它们与"仓储调度"场景直接相关，能让用户真正感受到价值提升，且改动独立、风险低。
