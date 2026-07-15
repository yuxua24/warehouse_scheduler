# 定时任务（Cron）功能分析

> 为智能仓储机器人调度系统添加定时调度能力，用户可通过 Web UI 和微信设置定时任务。

---

## 一、用户场景

| 场景 | 用户输入 | 系统行为 |
|------|---------|---------|
| 每日充电 | "每天晚上十点把所有机器人送回充电区" | 每天 22:00 执行调度 |
| 早班开工 | "工作日早上8点，R1去装卸区，R2去货架B" | 周一到周五 8:00 |
| 定时封闭 | "每晚6点到早8点关闭南侧通道" | 定时切换通道状态 |
| 周末维护 | "每周六凌晨2点维护，所有机器人回维护区" | 每周六 2:00 |

---

## 二、架构设计

```
┌──────────────────────────────────────────────────┐
│                    用户                            │
│   Web UI  ──┐                          ┌── 微信    │
│             │                          │          │
│     ┌───────▼──────────┐    ┌──────────▼────────┐ │
│     │  cron API        │    │  NL 解析            │ │
│     │  GET/POST/DELETE │    │  "每晚十点充电"      │ │
│     └───────┬──────────┘    └──────────┬────────┘ │
│             │                          │          │
│             └──────────┬───────────────┘          │
│                        ▼                          │
│              ┌──────────────────┐                 │
│              │   CronManager     │                 │
│              │  - add/del/list   │                 │
│              │  - NL → cron expr │                 │
│              └────────┬─────────┘                 │
│                       │                           │
│              ┌────────▼─────────┐                 │
│              │   APScheduler    │                 │
│              │   定时触发        │                 │
│              └────────┬─────────┘                 │
│                       │ 触发                      │
│              ┌────────▼─────────┐                 │
│              │  Workflow.run()  │                 │
│              │  调度引擎         │                 │
│              └────────┬─────────┘                 │
│                       │                           │
│              ┌────────▼─────────┐                 │
│              │  结果通知         │                 │
│              │  Web UI / 微信    │                 │
│              └──────────────────┘                 │
└──────────────────────────────────────────────────┘
```

---

## 三、数据模型

### 3.1 CronJob

```python
@dataclass
class CronJob:
    job_id: str           # UUID
    name: str             # 用户可读名称，如 "每晚充电"
    cron_expr: str        # cron 表达式，如 "0 22 * * *"
    instruction: str      # 调度指令，如 "所有机器人返回充电区"
    enabled: bool         # 是否启用
    created_at: str       # ISO 时间戳
    last_run_at: str      # 上次执行时间
    last_result: str      # 上次执行结果（succeeded/failed/…）
```

### 3.2 存储格式 (`configs/cron_jobs.json`)

```json
{
  "jobs": [
    {
      "job_id": "a1b2c3d4",
      "name": "每晚充电",
      "cron_expr": "0 22 * * *",
      "instruction": "所有机器人返回充电区",
      "enabled": true,
      "created_at": "2026-07-15T22:00:00",
      "last_run_at": null,
      "last_result": null
    }
  ]
}
```

---

## 四、自然语言 → Cron 表达式

### 4.1 解析策略

利用已有的 DeepSeek LLM（`task_parser_agent.py` 同类能力），将自然语言时间描述转为标准 cron 表达式：

| 用户输入 | Cron 表达式 | 说明 |
|---------|------------|------|
| "每天晚上十点" | `0 22 * * *` | 分 时 日 月 周 |
| "工作日早上8点" | `0 8 * * 1-5` | 周一到周五 |
| "每隔30分钟" | `*/30 * * * *` | 每 30 分钟 |
| "每周六凌晨2点" | `0 2 * * 6` | 周六 |
| "每天早8点和晚8点" | 两个任务 | 多个 cron |

### 4.2 LLM Function Calling Schema

```json
{
  "name": "create_cron_job",
  "description": "创建一个定时调度任务",
  "parameters": {
    "name": { "type": "string", "description": "任务名称" },
    "cron_expr": { "type": "string", "description": "cron 表达式" },
    "instruction": { "type": "string", "description": "调度指令" }
  }
}
```

### 4.3 不需要 LLM 的替代方案

LLM 可能不稳定。更可靠的方式：用简单的规则匹配 + 用户手动输入 cron 表达式。

**推荐混合方案**：LLM 辅助生成 cron 表达式（自动填充），用户可在 Web UI 上手动调整。

---

## 五、Web UI 设计

### 5.1 定时任务管理面板

在现有的 `SchedulePanel.jsx` 旁新增一个 Tab 或折叠面板：

```
┌─────────────────────────────────┐
│  ⏰ 定时任务                      │
│                                  │
│  ┌─────────────────────────────┐ │
│  │ 🔵 每晚充电     0 22 * * *  │ │
│  │    所有机器人返回充电区        │ │
│  │    上次: 07-15 22:00 ✅      │ │
│  │    [禁用] [删除]             │ │
│  ├─────────────────────────────┤ │
│  │ 🔵 早班开工     0 8 * * 1-5 │ │
│  │    R1去装卸区，R2去货架B     │ │
│  │    上次: 07-15 08:00 ✅      │ │
│  │    [禁用] [删除]             │ │
│  └─────────────────────────────┘ │
│                                  │
│  [+ 新建定时任务]                 │
└─────────────────────────────────┘
```

### 5.2 新建任务表单

```
┌─────────────────────────────────┐
│  新建定时任务                     │
│                                  │
│  任务名称: [每晚充电          ]   │
│  Cron:     [0 22 * * *       ]   │
│  指令:     [所有机器人返回充电区]  │
│                                  │
│  💡 常用 Cron:                   │
│  每天 22:00 → 0 22 * * *        │
│  工作日 8:00 → 0 8 * * 1-5      │
│  每小时 → 0 * * * *             │
│                                  │
│  [取消]  [创建]                   │
└─────────────────────────────────┘
```

---

## 六、微信端交互

### 6.1 定时任务指令

用户在微信中发送特殊格式的指令来管理定时任务：

| 用户发送 | 系统行为 |
|---------|---------|
| `定时 每晚十点 所有机器人返回充电区` | 创建定时任务 |
| `定时列表` | 列出所有定时任务 |
| `定时删除 每晚充电` | 删除指定任务 |
| `定时禁用 早班开工` | 禁用指定任务 |
| `定时启用 早班开工` | 启用指定任务 |

### 6.2 微信回复示例

```
⏰ 定时任务列表
━━━━━━━━━━━━━━
🔵 每晚充电 · 0 22 * * *
   所有机器人返回充电区
   上次: 07-15 22:00 ✅

🔵 早班开工 · 0 8 * * 1-5
   R1去装卸区，R2去货架B
   上次: 07-15 08:00 ✅
━━━━━━━━━━━━━━
共 2 个任务
```

---

## 七、APScheduler 集成

### 7.1 技术选型

```txt
# requirements.txt 新增
apscheduler>=3.10.0
```

### 7.2 CronManager 设计

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

class CronManager:
    def __init__(self, workflow_getter, jobs_path="configs/cron_jobs.json"):
        self.scheduler = AsyncIOScheduler()
        self.workflow_getter = workflow_getter  # () -> Workflow
        self.jobs_path = jobs_path
        self.jobs: dict[str, CronJob] = {}
    
    def start(self):
        """加载持久化的任务并启动调度器。"""
        self._load_jobs()
        for job in self.jobs.values():
            if job.enabled:
                self._schedule(job)
        self.scheduler.start()
    
    def add_job(self, name, cron_expr, instruction) -> CronJob:
        """添加定时任务。"""
        job = CronJob(
            job_id=uuid.uuid4().hex[:8],
            name=name,
            cron_expr=cron_expr,
            instruction=instruction,
            enabled=True,
        )
        self.jobs[job.job_id] = job
        self._schedule(job)
        self._save_jobs()
        return job
    
    def remove_job(self, job_id):
        """删除定时任务。"""
        self.scheduler.remove_job(job_id)
        del self.jobs[job_id]
        self._save_jobs()
    
    def _schedule(self, job: CronJob):
        """将 CronJob 注册到 APScheduler。"""
        self.scheduler.add_job(
            self._execute,
            trigger=CronTrigger.from_crontab(job.cron_expr),
            id=job.job_id,
            name=job.name,
            args=[job],
            replace_existing=True,
        )
    
    async def _execute(self, job: CronJob):
        """执行定时任务。"""
        wf = self.workflow_getter()
        state = wf.run(job.instruction)
        
        job.last_result = state.status.value
        
        # 推送结果到微信（如果有微信通道）
        if self.weixin_handler:
            text = format_schedule_result(state)
            # ... 推送到微信
        
        self._save_jobs()
```

### 7.3 server.py 集成

```python
# 在 startup_event 中
cron_manager = CronManager(lambda: get_workflow())
cron_manager.start()

# 新增 API 端点
@app.get("/api/cron")
async def list_cron_jobs(): ...

@app.post("/api/cron")
async def create_cron_job(data): ...

@app.delete("/api/cron/{job_id}")
async def delete_cron_job(job_id): ...

@app.put("/api/cron/{job_id}")
async def toggle_cron_job(job_id, enabled: bool): ...
```

---

## 八、并发安全

### 8.1 定时任务与手动调度的冲突

如果 22:00 定时任务触发时，用户也正在 Web UI 上手动调度，两者同时调用 `Workflow.run()` 会有冲突吗？

**回答**：不会。每次 `Workflow.run()` 创建独立的 `PlanningState`，不共享可变状态。Workflow 内部的 LangGraph 是线程安全的。

### 8.2 定时任务与微信长轮询

APScheduler 和微信长轮询都运行在同一个 asyncio 事件循环中，互相不阻塞。

---

## 九、文件改动清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `app/scheduler/__init__.py` | 模块导出 |
| **新增** | `app/scheduler/cron_manager.py` | 定时任务管理器（~150 行） |
| **新增** | `app/scheduler/cron_parser.py` | NL → cron 表达式解析（~80 行） |
| **新增** | `app/scheduler/job_store.py` | 任务 JSON 持久化（~60 行） |
| **新增** | `frontend/src/components/CronPanel.jsx` | Web UI 定时任务面板 |
| **新增** | `configs/cron_jobs.json` | 默认空任务文件 |
| **修改** | `app/api/server.py` | 启动 CronManager + 新增 4 个 API |
| **修改** | `app/api/schemas.py` | 新增 CronJob Pydantic 模型 |
| **修改** | `app/channels/weixin/message_handler.py` | 识别 `定时` 前缀指令 |
| **修改** | `requirements.txt` | 新增 `apscheduler` |
| **修改** | `frontend/src/api.js` | 新增 cron API 调用 |
| **修改** | `frontend/src/App.jsx` | 集成 CronPanel |

**总计**：新增 4 个 Python 文件 + 1 个前端文件，修改 6 个文件。

---

## 十、实施优先级

| 阶段 | 内容 | 估时 |
|------|------|------|
| **P0** | CronManager + JSON 持久化 + APScheduler | 2h |
| **P0** | server.py 集成 + 4 个 API 端点 | 1h |
| **P1** | Web UI 定时任务面板 | 2h |
| **P1** | 微信端 `定时` 指令支持 | 1h |
| **P2** | NL → cron 表达式（LLM 辅助） | 2h |
| **P2** | 定时任务执行结果微信推送 | 1h |

---

## 十一、风险点

| 风险 | 缓解 |
|------|------|
| LLM cron 解析不准 | 允许用户在 Web UI 手动调整 cron 表达式 |
| 定时任务执行时 Workflow 失败 | 记录失败日志，下次触发时重试 |
| 定时任务过多导致资源竞争 | 限制最多 10 个任务（3~5 台机器人规模足够） |
| 服务器重启丢失定时任务 | JSON 文件持久化，启动时恢复 |
| 微信推送定时结果时 token 过期 | 复用已有的 context_token 缓存 |
