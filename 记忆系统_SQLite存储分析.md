# 记忆系统 SQLite 存储可行性分析

> 基于"融入 Hermes 能力改造方案"中 P0-3（跨会话记忆）的需求，
> 分析是否适合用 SQLite 替代 JSON 文件存储记忆数据。

---

## 一、项目存储现状

| 已有内容 | 当前存储方式 | 说明 |
|---------|------------|------|
| 微信对话记录 | JSON 文件（`memory/chat_*.json`） | 每个会话一个文件 |
| 定时任务持久化 | JSON 文件（`scheduler/job_store.py`） | 文件存储 |
| 地图与运行时配置 | JSON 文件（`configs/`） | 固定配置文件 |

**关键事实：Python 标准库自带 `sqlite3` 模块，不需要在 `requirements.txt` 中新增任何依赖。** 零安装成本。

---

## 二、SQLite vs JSON 全维度对比

| 维度 | JSON 文件 | SQLite |
|------|---------|--------|
| **依赖** | 无 | Python 内置，零额外依赖 |
| **并发安全** | ❌ 多线程写入互相覆盖 | ✅ WAL 模式支持并发读 + 写 |
| **检索能力** | ❌ 全量加载后 O(n) 过滤 | ✅ SQL 查询 + FTS5 全文搜索（O(log n)） |
| **数据一致性** | ❌ 写入中断可能损坏整个文件 | ✅ 事务保证原子性（ACID） |
| **数据量增长** | 10MB+ 后加载变慢 | 100MB+ 依然高效 |
| **备份/迁移** | ✅ cp 复制即可 | ✅ 单一 .db 文件，同样可复制 |
| **结构灵活性** | ✅ 无 schema，随时加字段 | ⚠️ 需提前定义 schema，改结构需 migration |
| **调试便捷性** | ✅ 任何文本编辑器可看 | ⚠️ 需 `sqlite3` 命令行或 GUI 工具 |
| **写入性能（单条）** | ~1ms | ~5ms（含事务开销） |
| **查询性能（千条级别）** | 全量加载后手动过滤 | 有索引时毫秒级返回 |

---

## 三、这个项目用 SQLite 的具体收益

### 收益 1：FTS5 全文搜索（方式 2 的关键依赖）

方式 2（智能检索注入）的核心是**从历史中高效检索相关记录**。

**JSON 方式**：每次查询都需要读入全部历史，对每条做关键词匹配，O(n) 扫描。

```python
# JSON 检索 —— 每次请求 O(n)
def search_json(histories, query_terms):
    results = []
    for h in histories:          # 全量遍历
        if all(t in h["instruction"] for t in query_terms):
            results.append(h)
    results.sort(key=...)        # 手动排序
    return results[:3]
```

**SQLite FTS5 方式**：数据库级全文索引，O(log n) 返回。

```python
# SQLite 检索 —— 数据库级索引 O(log n)
cursor.execute("""
    SELECT instruction, tasks_json, timestamp, status, rank
    FROM memory_fts
    WHERE instruction MATCH '"R1" OR "装卸区" OR "R2"'
    ORDER BY rank
    LIMIT 3
""")
```

当历史记录超过 100 条后，JSON 的 O(n) 扫描开始产生可感知的延迟（几十毫秒）。
超过 1000 条后，差异上升到几百毫秒。

### 收益 2：避免并发写入冲突

项目已接入微信通道，未来可能接 Telegram、企业微信，多通道同时调度会导致 JSON 文件覆盖写入。

**JSON 的并发问题：**

```python
# 线程 A：读取 → 修改 → 写入
data = json.load(f)           # 读取
data.append(new_entry_A)      # 修改
json.dump(data, f)            # 写入 A

# 线程 B：同时读取 → 修改 → 写入（覆盖了 A！）
data = json.load(f)           # 读到的是旧数据！
data.append(new_entry_B)
json.dump(data, f)            # 写入 B → A 的记录丢失
```

**SQLite 的事务保证：**

```python
# 两个线程同时写入，SQLite 自动串行化
conn.execute("BEGIN IMMEDIATE")          # 加写锁
conn.execute("INSERT INTO memory ...", (entry,))
conn.execute("COMMIT")                   # 原子提交
```

### 收益 3：增量查询，避免全量加载到内存

JSON：`json.load()` 把整个文件读到内存，哪怕你只需要最近 3 条。

```python
data = json.load(f)           # 10000 条全部读到内存（~5MB）
last_3 = data[-3:]            # 只要 3 条，但已经浪费了 5MB
```

SQLite：只读取需要的行。

```python
cursor.execute("""
    SELECT instruction, timestamp, status
    FROM memory
    ORDER BY timestamp DESC
    LIMIT 3                    -- 只读 3 行到内存
""")
```

### 收益 4：结构化统计查询

```python
# JSON 方式：全量加载后手动过滤
data = json.load(f)
count = sum(
    1 for h in data
    if "R1" in h["instruction"] and "装卸区" in h["instruction"]
)

# SQLite 方式：数据库算好再返回
cursor.execute("""
    SELECT COUNT(*) FROM memory
    WHERE instruction LIKE '%R1%' AND instruction LIKE '%装卸区%'
""")
```

---

## 四、引入 SQLite 的代价

### 代价 1：需要写 SQL（维护成本）

```python
# JSON：直观，无需额外知识
def record(self, instruction, state):
    data = self._read_json()
    data.append({"instruction": instruction, "status": state.status.value})
    self._write_json(data)

# SQLite：需要 SQL 知识
def record(self, instruction, state):
    self.conn.execute(
        "INSERT INTO memory (instruction, status, tasks_json, metrics_json) "
        "VALUES (?, ?, ?, ?)",
        (instruction, state.status.value, tasks_json, metrics_json)
    )
    self.conn.commit()
```

SQL 本身并不复杂（INSERT / SELECT / UPDATE / DELETE 四个基础操作），
但团队每个人都需要理解并会维护。

### 代价 2：Schema 变更需要 Migration

项目处于快速迭代期，数据模型可能频繁变动。

```python
# JSON：加字段零成本
entry = {"instruction": "...", "new_field": value}  # 直接加
data.append(entry)

# SQLite：需执行 ALTER TABLE
conn.execute("ALTER TABLE memory ADD COLUMN new_field TEXT DEFAULT ''")
# 并且需要处理数据库中已有记录的 NULL 值
```

### 代价 3：查看数据需要工具

```bash
# JSON：直接用 cat 或编辑器
cat data/memory.json | python -m json.tool | grep instruction

# SQLite：需要 sqlite3 命令行
sqlite3 data/memory.db "SELECT instruction, status FROM memory LIMIT 5;"
```

---

## 五、推荐方案：混合存储

> **不建议"纯 JSON"也不建议"纯 SQLite"，而是 JSON + SQLite 混合。**

### 架构图

```text
写入路径（不阻塞当前请求）：
  调度结果 → JSON 文件（主存储，source of truth）
           ↘ SQLite 数据库（检索缓存，异步同步）

读取路径（影响当前请求延迟）：
  用户输入 → SQLite（FTS5 全文检索，快）
           → 生成摘要（~200 tokens）
           → 注入 LLM Prompt
```

### 为什么不是"纯 SQLite"？

1. **0 -> 1 阶段数据量很小**（每天几十条），JSON 完全够用
2. **调试便利性**：JSON 文件 cat 即看，SQLite 需要额外工具
3. **已有 JSON 数据**：微信聊天记录已经是 JSON，无需迁移
4. **Schema 自由**：快速迭代期不受约束
5. **如果是方式 3（仅记录），JSON 的检索劣势不相关**

### 为什么不是"纯 JSON"？

当以下条件**任一**满足时，JSON 不再适用：

| 触发条件 | 阈值 | 后果 |
|---------|------|------|
| 历史记录 > 10,000 条 | JSON 全量加载几 MB 以上 | 每次请求 ~100ms 的 IO 延迟 |
| 并发写入冲突出现 | 两路消息同时到达 | 数据覆盖丢失 |
| 需要方式 2 智能检索 | 用户开始说"再来一次" | 无 FTS5，O(n) 扫描慢 |
| 恢复数据一致性 | 写入中断 JSON 损坏 | 全部记录丢失 |

### 核心实现

```python
class HybridMemoryStore:
    """
    混合存储方案
    - JSON：权威数据源，保证不丢数据
    - SQLite：检索缓存，保证查询效率
    - 两者之间通过写入同步保持一致性
    """

    def __init__(self, json_path="data/memory.json", db_path="data/memory.db"):
        self.json_path = json_path
        self.db_path = db_path
        self._init_sqlite()          # 建表 + FTS5 索引
        self._ensure_json_exists()   # 确保 JSON 文件存在

    # ─── 写入路径 ───────────────────────────────────────

    def record(self, instruction: str, state) -> None:
        """记录一次调度（不阻塞调用方）"""

        # 1. 写 JSON（同步，source of truth）
        entry = {
            "timestamp": datetime.now().isoformat(),
            "instruction": instruction,
            "status": state.status.value,
            "tasks": [self._task_to_dict(t) for t in state.task_batch.tasks],
            "metrics": state.metrics.dict() if state.metrics else {},
        }
        self._append_json(entry)

        # 2. 写 SQLite（同步或异步）
        #    初期可以同步写（~5ms），性能有问题时改为异步队列
        self._write_sqlite(entry)

    # ─── 读取路径 ───────────────────────────────────────

    def retrieve_for_injection(self, instruction: str, top_k: int = 3) -> str:
        """检索 + 生成摘要 → 返回注入文本（< 300 tokens）"""
        rows = self.conn.execute("""
            SELECT instruction, tasks_json, timestamp, status
            FROM memory_fts
            WHERE instruction MATCH ?
            ORDER BY rank
            LIMIT ?
        """, [self._fts_query(instruction), top_k])

        lines = []
        for ts, inst, tasks_json, status in rows:
            tasks = json.loads(tasks_json)
            tasks_str = ", ".join(
                f"{t['robot_id']}→{t.get('goal', '?')}"
                for t in tasks
            )
            icon = "✅" if status == "success" else "⚠️"
            lines.append(f"[{ts[:10]}] {icon} {tasks_str}")

        if lines:
            lines.append(
                "[偏好] " + self._get_preference_summary()
            )

        return "\n".join(lines)

    # ─── 同步工具 ───────────────────────────────────────

    def sync_json_to_sqlite(self) -> int:
        """全量同步（启动时调用）：将 JSON 中所有记录写入 SQLite"""
        data = json.load(open(self.json_path))
        count = 0
        for entry in data:
            self._write_sqlite(entry)
            count += 1
        return count
```

---

## 六、场景决策树

用下面的流程快速判断你应该用哪种存储：

```text
Q1：你只需要方式 3（仅记录，零开销）？
  ├── ✅ → 保持 JSON，零改动，零风险
  └── ❌ → 进入 Q2

Q2：你需要方式 2（智能检索注入）？
  ├── ✅ → 进入 Q3
  └── ❌ → 保持 JSON

Q3：历史记录超过 1000 条或预期很快超过？
  ├── ✅ → SQLite（FTS5 是必选项）
  └── ❌ → 进入 Q4

Q4：有多个消息通道可能并发写入（微信 + Telegram + cron）？
  ├── ✅ → SQLite（WAL 模式处理并发）
  └── ❌ → 初期用 JSON，预留 SQLite 接口
```

---

## 七、SQLite Schema 建议（如需使用）

```sql
-- memory.sql

-- 主表
CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,          -- ISO 8601
    instruction TEXT    NOT NULL,          -- 用户原始输入
    status      TEXT    NOT NULL,          -- success / partially_successful / infeasible
    tasks_json  TEXT    NOT NULL,          -- 结构化任务 JSON
    metrics_json TEXT  DEFAULT '{}',       -- 规划指标
    failure_reason TEXT DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now'))
);

-- 索引：按时间倒序查询
CREATE INDEX IF NOT EXISTS idx_memory_timestamp
    ON memory(timestamp DESC);

-- 索引：按机器人 ID 查询（用于统计模式）
CREATE INDEX IF NOT EXISTS idx_memory_status
    ON memory(status);

-- FTS5 全文搜索虚拟表
-- 用于方式2的智能检索
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
    USING fts5(
        instruction,
        tasks_json,
        content='memory',
        content_rowid='id'
    );

-- 触发器：保持 FTS 与主表同步
CREATE TRIGGER IF NOT EXISTS after_insert_memory
    AFTER INSERT ON memory
BEGIN
    INSERT INTO memory_fts(rowid, instruction, tasks_json)
    VALUES (new.id, new.instruction, new.tasks_json);
END;
```

---

## 八、结论

> **可以添加 SQLite，且 Python 内置支持，零额外依赖。但是否使用取决于你打算走到方式 2（智能检索注入）还是停在方式 3（仅记录）。**

| 你的路线 | 推荐存储 | 理由 |
|---------|---------|------|
| 先走方式 3，快速上线 | **保持 JSON** | 零改动，零风险，零维护成本 |
| 走方式 3，但预留升级路径 | **JSON + 预留 SQLite 接口** | 不改实现，但留下替换点 |
| 直接走方式 2 | **混合存储（JSON + SQLite）** | JSON 保障数据安全，SQLite 保障检索效率 |
| 方式 2 + 高性能要求 | **纯 SQLite（WAL 模式）** | 配置 WAL 可同时处理读写，避免文件碎片 |

**建议路径：**

```
方式 3（JSON，快速上线）
    ↓ 当用户习惯"再来一次"等简化输入时
方式 2（JSON + SQLite 混合）
    ↓ 当数据量大到 IO 成为瓶颈时
纯 SQLite（WAL 模式）
```

**不要为了"可能有用"而提前引入 SQLite 的 schema 维护成本。**
**但 JSON 方案的并发缺陷和检索瓶颈需要在设计方案时提前评估，不要等到数据丢了再补。**
