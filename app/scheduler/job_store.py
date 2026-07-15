"""定时任务 JSON 持久化存储。"""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class CronJob:
    """定时任务数据模型。"""

    job_id: str
    name: str
    cron_expr: str
    instruction: str
    enabled: bool = True
    created_at: str = ""
    last_run_at: str = ""
    last_result: str = ""


class JobStore:
    """JSON 文件持久化的定时任务存储。"""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.jobs: Dict[str, CronJob] = {}
        self._ensure_file()

    def _ensure_file(self) -> None:
        """确保文件存在。"""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        if not self.filepath.exists():
            self.filepath.write_text('{"jobs": []}', encoding="utf-8")

    def load(self) -> Dict[str, CronJob]:
        """从文件加载所有任务。"""
        try:
            data = json.loads(self.filepath.read_text(encoding="utf-8"))
            self.jobs.clear()
            for item in data.get("jobs", []):
                job = CronJob(**item)
                self.jobs[job.job_id] = job
        except (json.JSONDecodeError, OSError):
            self.jobs.clear()
        return self.jobs

    def save(self) -> None:
        """保存所有任务到文件。"""
        data = {
            "jobs": [asdict(j) for j in self.jobs.values()]
        }
        self.filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, job: CronJob) -> None:
        """添加任务并保存。"""
        self.jobs[job.job_id] = job
        self.save()

    def remove(self, job_id: str) -> Optional[CronJob]:
        """删除任务并保存，返回被删除的任务。"""
        removed = self.jobs.pop(job_id, None)
        if removed:
            self.save()
        return removed

    def get(self, job_id: str) -> Optional[CronJob]:
        """获取指定任务。"""
        return self.jobs.get(job_id)

    def list_all(self) -> List[CronJob]:
        """列出所有任务。"""
        return list(self.jobs.values())

    def update(self, job: CronJob) -> None:
        """更新任务并保存。"""
        if job.job_id in self.jobs:
            self.jobs[job.job_id] = job
            self.save()
