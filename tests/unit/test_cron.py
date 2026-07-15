"""Unit tests for cron job system."""

import os
import sys
import tempfile
import time

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, ".venv_packages"))

import pytest
from app.scheduler.job_store import JobStore, CronJob
from app.scheduler.cron_manager import CronManager


class TestJobStore:
    """Test JSON persistence for cron jobs."""

    def test_add_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            store = JobStore(path)

            job = CronJob(
                job_id="test1",
                name="每晚充电",
                cron_expr="0 22 * * *",
                instruction="所有机器人返回充电区",
            )
            store.add(job)

            jobs = store.list_all()
            assert len(jobs) == 1
            assert jobs[0].name == "每晚充电"

    def test_add_and_remove(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            store = JobStore(path)

            store.add(CronJob(job_id="j1", name="a", cron_expr="* * * * *", instruction="x"))
            store.add(CronJob(job_id="j2", name="b", cron_expr="* * * * *", instruction="y"))
            assert len(store.list_all()) == 2

            store.remove("j1")
            assert len(store.list_all()) == 1
            assert store.list_all()[0].name == "b"

    def test_persistence_across_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            store1 = JobStore(path)
            store1.add(CronJob(job_id="j1", name="test", cron_expr="* * * * *", instruction="x"))
            del store1

            store2 = JobStore(path)
            store2.load()
            assert len(store2.list_all()) == 1
            assert store2.list_all()[0].name == "test"

    def test_toggle_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            store = JobStore(path)
            store.add(CronJob(job_id="j1", name="test", cron_expr="* * * * *", instruction="x"))

            job = store.get("j1")
            job.enabled = False
            store.update(job)

            loaded = store.get("j1")
            assert loaded.enabled is False

    def test_empty_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            store = JobStore(path)
            assert store.list_all() == []
            assert store.get("nonexistent") is None

    def test_corrupt_file_handled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/jobs.json"
            with open(path, "w") as f:
                f.write("not json{{{")

            store = JobStore(path)
            store.load()
            assert store.list_all() == []  # 不应崩溃


class TestCronManager:
    """Test CronManager CRUD operations."""

    def test_add_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CronManager(
                workflow_fn=lambda x: None,
                jobs_path=f"{tmpdir}/jobs.json",
            )
            job = mgr.add_job("test", "0 22 * * *", "所有机器人返回充电区")
            assert job.name == "test"
            assert job.cron_expr == "0 22 * * *"
            assert job.enabled is True
            assert job.job_id

    def test_list_jobs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CronManager(
                workflow_fn=lambda x: None,
                jobs_path=f"{tmpdir}/jobs.json",
            )
            mgr.add_job("a", "* * * * *", "x")
            mgr.add_job("b", "* * * * *", "y")
            jobs = mgr.list_jobs()
            assert len(jobs) == 2

    def test_remove_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CronManager(
                workflow_fn=lambda x: None,
                jobs_path=f"{tmpdir}/jobs.json",
            )
            job = mgr.add_job("test", "* * * * *", "x")
            assert mgr.remove_job(job.job_id) is True
            assert mgr.remove_job("nonexistent") is False

    def test_toggle_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CronManager(
                workflow_fn=lambda x: None,
                jobs_path=f"{tmpdir}/jobs.json",
            )
            job = mgr.add_job("test", "* * * * *", "x")
            toggled = mgr.toggle_job(job.job_id, False)
            assert toggled.enabled is False
            toggled = mgr.toggle_job(job.job_id, True)
            assert toggled.enabled is True


class TestCronGuess:
    """Test cron expression guessing from natural language."""

    def test_already_cron(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("0 22 * * *") == "0 22 * * *"
        assert _guess_cron("*/30 * * * *") == "*/30 * * * *"

    def test_evening_time(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("每晚十点") == "0 22 * * *"
        assert _guess_cron("每晚10点") == "0 22 * * *"
        assert _guess_cron("晚上十点") == "0 22 * * *"

    def test_morning_time(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("每天早上八点") == "0 8 * * *"
        assert _guess_cron("每天早上8点") == "0 8 * * *"

    def test_weekday(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("工作日早上8点") == "0 8 * * 1-5"

    def test_every_hour(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("每小时") == "0 * * * *"
        assert _guess_cron("每隔一小时") == "0 * * * *"

    def test_unknown_returns_empty(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("不知道什么时间") == ""

    def test_time_with_colon(self):
        from app.channels.weixin.message_handler import _guess_cron
        assert _guess_cron("每天22:00") == "0 22 * * *"
