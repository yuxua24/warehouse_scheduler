"""HybridMemoryStore: JSON + SQLite 混合存储的记忆系统。

存储模式:
  - json_only (方式3): 仅写入 JSON 文件，零 LLM 上下文开销
  - hybrid    (方式2): JSON 做权威数据源 + SQLite FTS5 做检索缓存

写入路径 (不阻塞调用方):
  调度结果 → JSON 文件 (source of truth)
           ↘ SQLite 数据库 (FTS5 全文索引, 检索缓存)

读取路径 (影响当前请求延迟):
  用户输入 → SQLite FTS5 检索 top-3
           → 规则摘要压缩至 ~200 tokens
           → 注入 LLM System Prompt
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# ── 默认路径 ────────────────────────────────────────────────────────────────

_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
)
_DEFAULT_JSON_PATH = os.path.join(_DEFAULT_DATA_DIR, "memory.json")
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DATA_DIR, "memory.db")


class HybridMemoryStore:
    """记忆存储：支持 JSON only 和 JSON + SQLite FTS5 两种模式。

    Args:
        json_path: JSON 文件路径（权威数据源）
        db_path: SQLite 数据库路径（检索缓存）
        mode: "json_only" (方式3) 或 "hybrid" (方式2)
        auto_sync: 初始化时是否自动从 JSON 同步到 SQLite
    """

    def __init__(
        self,
        json_path: str = None,
        db_path: str = None,
        mode: str = "json_only",
        auto_sync: bool = True,
    ):
        self.json_path = json_path or _DEFAULT_JSON_PATH
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.mode = mode
        self._ensure_data_dir()

        # SQLite 连接（仅 hybrid 模式初始化）
        self.conn: Optional[sqlite3.Connection] = None
        if self.mode == "hybrid":
            self._init_sqlite()
            if auto_sync:
                self.sync_json_to_sqlite()

    # ── 公开接口 ────────────────────────────────────────────────────────

    def record(
        self,
        instruction: str,
        status: str,
        tasks: list,
        metrics: dict = None,
        failure_reason: str = "",
    ) -> None:
        """记录一次调度结果（写入路径，不阻塞调用方）。

        Args:
            instruction: 用户原始输入
            status: 批次状态 (succeeded / partially_successful / infeasible)
            tasks: 任务列表，每项含 robot_id, goal, success
            metrics: 规划指标字典
            failure_reason: 失败原因
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "instruction": instruction,
            "status": status,
            "tasks": tasks,
            "metrics": metrics or {},
            "failure_reason": failure_reason,
        }

        # 1. 写入 JSON（同步，source of truth）
        self._append_json(entry)

        # 2. 写入 SQLite（同步或异步）
        if self.mode == "hybrid" and self.conn is not None:
            self._write_sqlite(entry)

    def get_history(self, limit: int = 20, offset: int = 0) -> list:
        """获取历史记录（从 JSON 读取）。"""
        data = self._read_json()
        return data[-limit - offset : len(data) - offset] if data else []

    def get_recent(self, n: int = 5) -> list:
        """获取最近 n 条记录。"""
        return self.get_history(limit=n)

    def count(self) -> int:
        """获取总记录数。"""
        return len(self._read_json())

    # ── 读取路径（方式2：智能检索） ─────────────────────────────────────

    def retrieve_for_injection(
        self,
        instruction: str,
        top_k: int = 3,
        max_tokens: int = 300,
    ) -> str:
        """检索相关历史并生成摘要，用于注入 LLM Prompt。

        仅在 hybrid 模式下有效，json_only 模式返回空字符串。

        Args:
            instruction: 当前用户输入
            top_k: 检索 top-k 条
            max_tokens: 摘要最大 token 数（按中文字符估算）

        Returns:
            格式化的摘要字符串，空字符串表示无相关历史
        """
        if self.mode != "hybrid" or self.conn is None:
            return ""

        # 1. FTS5 全文检索
        rows = self._fts_retrieve(instruction, top_k)
        if not rows:
            return ""

        # 2. 规则摘要生成
        lines = []
        for ts, inst, tasks_json, status in rows:
            tasks = json.loads(tasks_json) if isinstance(tasks_json, str) else tasks_json
            tasks_str = ", ".join(
                f"{t.get('robot_id', '?')}→{t.get('goal', '?')}"
                for t in (tasks if isinstance(tasks, list) else [])
            )
            icon = "✅" if status in ("succeeded", "success") else "⚠️"
            lines.append(f"[{ts[:10]}] {icon} {tasks_str}")

        # 3. 附加偏好信息（来自 UserProfile，如果有）
        pref = self._get_preference_summary()
        if pref:
            lines.append(f"[偏好] {pref}")

        summary = "\n".join(lines)

        # 4. 检查 token 预算（中文 2 字符 ≈ 1 token，英文 4 字符 ≈ 1 token）
        token_estimate = self._estimate_tokens(summary)
        if token_estimate > max_tokens:
            # 截断，只保留最近 2 条
            lines = lines[:2]
            if pref:
                lines = lines[:1] + ["[偏好] " + pref]
            summary = "\n".join(lines)

        return summary

    # ── 同步工具 ─────────────────────────────────────────────────────────

    def sync_json_to_sqlite(self) -> int:
        """将 JSON 中所有记录全量同步到 SQLite。

        不删除 SQLite 中已有的记录（避免重复）。
        返回本次新增条数。
        """
        if self.mode != "hybrid" or self.conn is None:
            return 0

        data = self._read_json()
        count = 0
        for entry in data:
            try:
                self._write_sqlite(entry, ignore_duplicate=True)
                count += 1
            except Exception:
                continue
        return count

    def clear(self) -> None:
        """清空所有记忆（JSON + SQLite）。"""
        # 清空 JSON
        self._write_json([])
        # 清空 SQLite
        if self.mode == "hybrid" and self.conn is not None:
            self.conn.execute("DELETE FROM memory")
            self.conn.execute("DELETE FROM memory_fts")
            self.conn.commit()

    # ── 内部实现 ─────────────────────────────────────────────────────────

    def _ensure_data_dir(self) -> None:
        """确保数据目录存在。"""
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)

    def _init_sqlite(self) -> None:
        """初始化 SQLite 数据库和 FTS5 索引。"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

        # 主表
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                instruction TEXT    NOT NULL,
                status      TEXT    NOT NULL,
                tasks_json  TEXT    NOT NULL DEFAULT '[]',
                metrics_json TEXT   DEFAULT '{}',
                failure_reason TEXT DEFAULT ''
            )
        """)

        # 时间索引
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_timestamp
            ON memory(timestamp DESC)
        """)

        # 状态索引
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_status
            ON memory(status)
        """)

        # FTS5 全文搜索虚拟表
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(
                instruction,
                tasks_json,
                content='memory',
                content_rowid='id'
            )
        """)

        # 触发器：保持 FTS 与主表同步
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS after_insert_memory
            AFTER INSERT ON memory
            BEGIN
                INSERT INTO memory_fts(rowid, instruction, tasks_json)
                VALUES (new.id, new.instruction, new.tasks_json);
            END
        """)

        self.conn.commit()

    def _read_json(self) -> list:
        """读取 JSON 文件全部记录。"""
        if not os.path.exists(self.json_path):
            return []
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_json(self, data: list) -> None:
        """原子写入 JSON 文件。"""
        tmp_path = self.json_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.json_path)

    def _append_json(self, entry: dict) -> None:
        """追加一条记录到 JSON 文件。"""
        data = self._read_json()
        data.append(entry)
        self._write_json(data)

    def _write_sqlite(self, entry: dict, ignore_duplicate: bool = False) -> None:
        """写入一条记录到 SQLite（含 FTS 索引）。"""
        if self.conn is None:
            return

        # 计算 fingerprint 用于去重
        fingerprint = f"{entry.get('timestamp', '')}|{entry.get('instruction', '')[:100]}"

        if ignore_duplicate:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM memory WHERE instruction = ? AND timestamp = ?",
                (entry.get("instruction", "")[:100], entry.get("timestamp", "")),
            )
            if cursor.fetchone()[0] > 0:
                return

        tasks_json = json.dumps(entry.get("tasks", []), ensure_ascii=False)
        metrics_json = json.dumps(entry.get("metrics", {}), ensure_ascii=False)

        self.conn.execute(
            """INSERT INTO memory (timestamp, instruction, status, tasks_json, metrics_json, failure_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry.get("timestamp", ""),
                entry.get("instruction", ""),
                entry.get("status", ""),
                tasks_json,
                metrics_json,
                entry.get("failure_reason", ""),
            ),
        )
        self.conn.commit()

    def _fts_retrieve(self, instruction: str, top_k: int = 3) -> list:
        """FTS5 全文检索，返回匹配的历史记录。

        Returns:
            [(timestamp, instruction, tasks_json, status), ...]
        """
        query = self._build_fts_query(instruction)
        if not query:
            return []

        try:
            cursor = self.conn.execute("""
                SELECT m.timestamp, m.instruction, m.tasks_json, m.status
                FROM memory_fts f
                JOIN memory m ON f.rowid = m.id
                WHERE memory_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, [query, top_k])
            return cursor.fetchall()
        except sqlite3.OperationalError:
            # FTS5 查询语法错误时回退
            return []

    def _build_fts_query(self, instruction: str) -> str:
        """将用户输入转换为 FTS5 查询语句。

        提取实体关键词并用 OR 连接。
        """
        # 已知仓储调度领域的实体词
        known_entities = {
            "R1", "R2", "R3", "R4", "R5",
            "装卸区", "充电区", "货架A", "货架B", "货架C",
            "打包站", "维护区", "分拣区", "暂存区",
            "北侧通道", "南侧通道", "东侧通道", "西侧通道",
            "左上角", "右上角", "左下角", "右下角", "中间",
        }

        # 提取指令中出现的已知实体
        found = [kw for kw in known_entities if kw in instruction]

        # 如果没有已知实体，用指令中的非停用词
        if not found:
            # 使用双引号包含的精确短语搜索
            tokens = instruction.strip().split()
            # 过滤过短的词
            tokens = [t for t in tokens if len(t) >= 2]
            if tokens:
                return " OR ".join(f'"{t}"' for t in tokens[:5])
            return ""

        return " OR ".join(f'"{kw}"' for kw in found)

    def _get_preference_summary(self) -> str:
        """从历史记录中提取偏好摘要（规则引擎，无 LLM 调用）。"""
        data = self._read_json()
        if not data:
            return ""

        # 统计 robot -> goal 频率
        robot_goals: Dict[str, Dict[str, int]] = {}
        status_counts = {"succeeded": 0, "partially_successful": 0, "infeasible": 0}

        for entry in data:
            s = entry.get("status", "")
            if s in status_counts:
                status_counts[s] += 1

            for task in entry.get("tasks", []):
                rid = task.get("robot_id", "")
                goal = task.get("goal", "")
                success = task.get("success", False)
                if rid and goal and success:
                    if rid not in robot_goals:
                        robot_goals[rid] = {}
                    robot_goals[rid][goal] = robot_goals[rid].get(goal, 0) + 1

        # 推断优先级顺序（按出现的任务数降序）
        robot_freq = {
            rid: sum(goals.values())
            for rid, goals in robot_goals.items()
        }
        sorted_robots = sorted(robot_freq, key=robot_freq.get, reverse=True)

        parts = []
        if sorted_robots:
            parts.append("优先级: " + " > ".join(sorted_robots))

        # 每个机器人的常用目标
        for rid in sorted_robots[:3]:  # 最多显示 3 个
            goals = robot_goals[rid]
            sorted_goals = sorted(goals, key=goals.get, reverse=True)
            top_goal = sorted_goals[0] if sorted_goals else ""
            if top_goal:
                parts.append(f"{rid}常去{top_goal}")

        return " · ".join(parts)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算文本的 token 数量。

        中文约 2 字符/token，英文约 4 字符/token，按混合场景 3 字符/token 估算。
        """
        if not text:
            return 0
        return len(text) // 2  # 保守估算（中文为主）

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        if self.conn is not None:
            self.conn.close()
            self.conn = None
