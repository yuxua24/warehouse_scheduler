"""定时任务管理器（APScheduler + JobStore）。"""

import uuid
import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.scheduler.job_store import JobStore, CronJob

logger = logging.getLogger(__name__)


class CronManager:
    """定时任务管理器。

    使用方法:
        mgr = CronManager(workflow_fn, jobs_path="configs/cron_jobs.json")
        mgr.start()

        # 添加任务
        job = mgr.add_job("每晚充电", "0 22 * * *", "所有机器人返回充电区")

        # 列表
        jobs = mgr.list_jobs()

        # 删除
        mgr.remove_job("job_id")
    """

    def __init__(
        self,
        workflow_fn: Callable[[str], object],
        jobs_path: str = "configs/cron_jobs.json",
        on_result: Callable = None,
    ):
        self.workflow_fn = workflow_fn  # fn(instruction) -> PlanningState
        self.on_result = on_result      # fn(job: CronJob, state) 结果回调
        self.store = JobStore(jobs_path)
        self.scheduler = AsyncIOScheduler()
        self._started = False

    def start(self) -> None:
        """加载持久化的任务并启动调度器。"""
        if self._started:
            return

        # 加载磁盘上的任务
        self.store.load()

        # 注册所有启用的任务
        for job in self.store.list_all():
            if job.enabled:
                self._schedule(job)

        self.scheduler.start()
        self._started = True
        print(f"[cron] Scheduler started with {len(self.store.jobs)} job(s)")

    def stop(self) -> None:
        """停止调度器。"""
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    # ── CRUD ────────────────────────────────────────────────────────────

    def add_job(self, name: str, cron_expr: str, instruction: str) -> CronJob:
        """添加定时任务。"""
        job = CronJob(
            job_id=uuid.uuid4().hex[:8],
            name=name,
            cron_expr=cron_expr,
            instruction=instruction,
            enabled=True,
            created_at=datetime.now().isoformat(),
        )

        self.store.add(job)
        self._schedule(job)
        print(f"[cron] Added job: {name} ({cron_expr})")
        return job

    def remove_job(self, job_id: str) -> bool:
        """删除定时任务，返回是否成功。"""
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

        removed = self.store.remove(job_id)
        if removed:
            print(f"[cron] Removed job: {removed.name}")
            return True
        return False

    def toggle_job(self, job_id: str, enabled: bool) -> Optional[CronJob]:
        """启用/禁用定时任务。"""
        job = self.store.get(job_id)
        if not job:
            return None

        job.enabled = enabled
        self.store.update(job)

        if enabled:
            self._schedule(job)
        else:
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass

        print(f"[cron] Job '{job.name}' {'enabled' if enabled else 'disabled'}")
        return job

    def list_jobs(self) -> List[CronJob]:
        """列出所有定时任务。"""
        return self.store.list_all()

    def get_job(self, job_id: str) -> Optional[CronJob]:
        """获取单个任务。"""
        return self.store.get(job_id)

    # ── 内部 ─────────────────────────────────────────────────────────────

    def _schedule(self, job: CronJob) -> None:
        """将 CronJob 注册到 APScheduler。"""
        try:
            trigger = CronTrigger.from_crontab(job.cron_expr)
        except ValueError as e:
            print(f"[cron] Invalid cron expr for '{job.name}': {e}")
            return

        self.scheduler.add_job(
            self._execute,
            trigger=trigger,
            id=job.job_id,
            name=job.name,
            args=[job],
            replace_existing=True,
        )

    async def _execute(self, job: CronJob) -> None:
        """执行定时任务（由 APScheduler 触发）。"""
        now = datetime.now().isoformat()
        print(f"[cron] Executing: {job.name} | {job.instruction[:50]}")

        try:
            state = self.workflow_fn(job.instruction)
            result = state.status.value
        except Exception as e:
            print(f"[cron] Job '{job.name}' failed: {e}")
            result = f"error: {e}"

        # 更新执行记录
        job.last_run_at = now
        job.last_result = result
        self.store.update(job)

        print(f"[cron] Job '{job.name}' done: {result}")

        # 结果回调（如推送到微信）
        if self.on_result:
            try:
                self.on_result(job, state if 'state' in dir() else None)
            except Exception:
                pass
