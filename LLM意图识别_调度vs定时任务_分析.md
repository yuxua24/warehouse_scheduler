# LLM 意图识别：调度指令 vs 定时任务指令

> 分析如何让 LLM 自动识别用户意图，将自然语言请求路由到正确的处理器。
> 先分析，不修改代码。

---

## 一、问题现状

### 1.1 当前流程

```
微信消息
    │
    ├─ 开头是 "定时"？ → _handle_cron_command() ✅ 正确处理
    │
    └─ 否则 → Workflow.run() → LLM 解析 → 调度引擎
                                    │
                                    └─ "输出当前定时任务" → 调度失败
                                       LLM 无法从中提取机器人任务
```

### 1.2 具体问题

| 用户输入 | 当前行为 | 期望行为 |
|---------|---------|---------|
| `定时 列表` | 列出定时任务 ✅ | 不变 |
| `输出当前定时任务` | 调度失败 ❌ | 列出定时任务 |
| `有哪些定时任务` | 调度失败 ❌ | 列出定时任务 |
| `删除每晚充电` | 调度失败 ❌ | 删除定时任务 |
| `R1去装卸区` | 正常调度 ✅ | 不变 |
| `每天晚上十点让所有机器人充电` | 调度失败（不知道是定时还是调度）❌ | **LLM 判断**：创建定时 |

---

## 二、解决方案：LLM 意图识别 + 路由

### 2.1 核心思路

让 LLM（DeepSeek Function Calling）在接收到自然语言后，先进行一次**意图分类**，返回结构化意图，然后由 message_handler 根据意图路由到正确的处理器。

```
微信消息
    │
    ▼
┌─────────────────────┐
│  LLM 意图识别        │
│  Function Calling    │
│                      │
│  输出:               │
│  {                   │
│    "intent":         │
│      "schedule" |    │
│      "cron_list" |   │
│      "cron_create" | │
│      "cron_delete" | │
│      "cron_toggle"   │
│    "params": {...}   │
│  }                   │
└─────────┬───────────┘
          │
    ┌─────┴──────────────────────┐
    │                            │
    ▼                            ▼
intent="schedule"          intent="cron_*"
    │                            │
    ▼                            ▼
Workflow.run()              CronManager.xxx()
```

### 2.2 为什么在 message_handler 层做而不是 Workflow 层？

- `Workflow.run()` 的设计目标是：接收一个明确的调度指令，返回 PlanningState
- 它不适合处理"列出定时任务"这种非调度的请求
- 意图路由应该在**消息处理层**（message_handler）完成，这是消息通道的职责

### 2.3 LLM Function Calling Schema

```json
{
  "name": "classify_intent",
  "description": "分析用户输入的意图，判断是调度机器人还是管理定时任务",
  "parameters": {
    "type": "object",
    "properties": {
      "intent": {
        "type": "string",
        "enum": ["schedule", "cron_create", "cron_list", "cron_delete", "cron_toggle", "cron_enable", "cron_disable", "unknown"],
        "description": "用户意图类型"
      },
      "schedule_instruction": {
        "type": "string",
        "description": "如果是 schedule 意图，这里是纯调度指令文本（去掉时间描述部分）"
      },
      "cron_name": {
        "type": "string",
        "description": "定时任务名称（cron_create 时必填）"
      },
      "cron_expr": {
        "type": "string",
        "description": "cron 表达式（cron_create 时必填）"
      },
      "cron_instruction": {
        "type": "string",
        "description": "定时执行的调度指令（cron_create 时必填）"
      },
      "target_job_name": {
        "type": "string",
        "description": "要操作的任务名称（cron_delete/cron_toggle 时使用）"
      }
    },
    "required": ["intent"]
  }
}
```

### 2.4 意图分类示例

| 用户输入 | LLM 返回 intent | LLM 返回 params |
|---------|----------------|----------------|
| `R1前往装卸区` | `schedule` | `schedule_instruction: "R1前往装卸区"` |
| `R1去充电，R2去货架B` | `schedule` | `schedule_instruction: "R1去充电，R2去货架B"` |
| `输出当前定时任务` | `cron_list` | — |
| `查看定时任务` | `cron_list` | — |
| `有哪些定时任务` | `cron_list` | — |
| `每天晚上十点所有机器人回充电区` | `cron_create` | `cron_name: "每晚充电"`, `cron_expr: "0 22 * * *"`, `cron_instruction: "所有机器人返回充电区"` |
| `创建定时：每天8点 R1去装卸区` | `cron_create` | `cron_name: "早班调度"`, `cron_expr: "0 8 * * *"`, `cron_instruction: "R1去装卸区"` |
| `删除每晚充电这个定时` | `cron_delete` | `target_job_name: "每晚充电"` |
| `禁用早班开工` | `cron_disable` | `target_job_name: "早班开工"` |
| 发送图片 | `unknown` | — |

---

## 三、需要修改的文件

### 3.1 `message_handler.py` — 主改动

**改动点**：在 `_handle_impl` 中，将原来的「先检查 `定时` 前缀 → 否则 Workflow.run()」改为「先 LLM 意图分类 → 根据 intent 路由」

```python
async def _handle_impl(self, msg):
    text = content.strip()
    
    # === 新逻辑：LLM 意图分类 ===
    intent = await self._classify_intent(text)
    
    if intent == "schedule":
        # → Workflow.run()
        state = self.workflow_fn(text)
        reply = format_schedule_result(state, self.location_names)
    elif intent == "cron_list":
        # → 列出定时任务
        reply = self._format_cron_list()
    elif intent == "cron_create":
        # → 创建定时任务
        reply = self._create_cron_via_llm(text)
    elif intent == "cron_delete":
        # → 删除定时任务
        reply = self._delete_cron_via_llm(text)
    elif intent == "cron_toggle":
        # → 切换定时任务
        reply = self._toggle_cron_via_llm(text)
    else:
        # → 尝试调度（保留兜底）
        reply = ...
```

### 3.2 `app/agents/task_parser_agent.py` — 新增意图分类方法

**改动点**：新增 `classify_intent(self, text: str) -> dict` 方法，调用 DeepSeek Function Calling 做意图分类。

```python
def classify_intent(self, text: str) -> dict:
    """对用户输入做意图分类。
    
    Returns:
        {"intent": "schedule", "schedule_instruction": "..."}
        {"intent": "cron_list"}
        {"intent": "cron_create", "cron_name": "...", ...}
    """
    # 复用已有的 OpenAI client + Function Calling 管道
    tools = [INTENT_CLASSIFY_TOOL]
    response = self.client.chat.completions.create(
        model=self.model,
        messages=[{"role": "user", "content": text}],
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "classify_intent"}},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.tool_calls[0].function.arguments)
```

### 3.3 `message_handler.py` — 去掉旧的"定时"前缀硬匹配

原有的 `text.startswith("定时")` 的硬匹配逻辑**可以保留作为快速路径**（不需要调 LLM），但不再是唯一入口。LLM 意图分类是主路径。

```python
# 快速路径（零成本，不调 LLM）
if text.startswith("定时"):
    reply = await self._handle_cron_command(text, user_id)
    await self._send_reply(user_id, reply)
    return

# 主路径：LLM 意图分类
intent = await self._classify_intent(text)
...
```

### 3.4 `message_handler.py` — 新增辅助方法

```python
async def _classify_intent(self, text: str) -> dict:
    """调用 LLM 做意图分类。"""
    ...

def _format_cron_list(self) -> str:
    """格式化定时任务列表为 Markdown。"""
    ...

async def _create_cron_via_llm(self, text: str) -> str:
    """通过 LLM 创建定时任务。"""
    ...
```

---

## 四、改动量评估

| 文件 | 改动 | 行数 |
|------|------|------|
| `app/agents/task_parser_agent.py` | 新增 `classify_intent()` 方法 | +50 |
| `app/channels/weixin/message_handler.py` | 改为 LLM 路由，去掉硬匹配 | ~80 行修改 |
| `app/api/server.py` | 将 classify_intent 能力注入 handler | +5 |

**总计**：约 130 行新增/修改。

---

## 五、LLM 调用成本分析

| 场景 | LLM 调用次数 | 说明 |
|------|-------------|------|
| 用户发调度指令 | **1 次**（意图分类）→ 然后 Workflow 内的 LLM 解析（已有） | 多 1 次 LLM 调用 |
| 用户发定时指令 | **1 次**（意图分类 + 参数提取） | 不调 Workflow，总调用不变 |
| 用户发"定时 列表" | **0 次**（快速路径命中） | 无额外成本 |

每个请求最多增加 1 次 LLM 调用（约 0.1 秒，$0.0001），可以接受。

### 降级方案（如果不想多调 LLM）

可以先用**简单关键词匹配**做第一层过滤，命中了就直接路由，没命中再调 LLM：

```python
# 零成本快速路由
if re.search(r"定时|cron|计划|定期|每天|每晚|每周", text):
    # 可能是定时相关，调 LLM 分类
    intent = await self._classify_intent(text)
else:
    # 大概率是调度指令，直接走调度
    intent = "schedule"
```

---

## 六、测试用例设计

| 输入 | 期望 intent | 期望行为 |
|------|------------|---------|
| `R1前往装卸区` | `schedule` | 正常调度 |
| `输出当前定时任务` | `cron_list` | 返回任务列表 |
| `有哪些定时任务` | `cron_list` | 返回任务列表 |
| `每天晚上十点所有机器人回充电区` | `cron_create` | 创建定时任务 |
| `帮我删掉每晚充电` | `cron_delete` | 删除任务 |
| `关闭早班开工` | `cron_disable` | 禁用任务 |
| `开启早班开工` | `cron_enable` | 启用任务 |
| 发一张图片 | `unknown` | 忽略 |

---

## 七、是否保留 Web UI 端不变

Web UI 的 `SchedulePanel` 调用的是 `/api/schedule`，走的是完整的 Workflow（含 LLM 解析）。如果用户在 Web UI 输入「输出当前定时任务」，会调度失败。

**方案**：Web UI 的 SchedulePanel **不需要改动**。用户在 Web UI 输入调度指令，在 ⏰ 定时 Tab 管理定时任务，职责分离清晰。只有微信端需要 LLM 意图路由（因为微信只有一个输入框）。
