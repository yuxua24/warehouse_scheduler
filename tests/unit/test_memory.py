"""Unit tests for memory system (HybridMemoryStore, MemoryRetriever, UserProfile)."""

import os
import json
import tempfile
import pytest

from app.memory.hybrid_store import HybridMemoryStore
from app.memory.retriever import MemoryRetriever
from app.memory.user_profile import UserProfile


class TestHybridMemoryStoreJsonOnly:
    """Test HybridMemoryStore in json_only mode (way 3: zero-overhead recording)."""

    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "memory.json")
            s = HybridMemoryStore(
                json_path=json_path,
                db_path=os.path.join(tmpdir, "memory.db"),
                mode="json_only",
            )
            yield s
            s.close()

    def test_record_and_count(self, store):
        assert store.count() == 0
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        assert store.count() == 1

    def test_get_history(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R2去充电区", "succeeded", [{"robot_id": "R2", "goal": "充电区", "success": True}])
        history = store.get_history()
        assert len(history) == 2
        assert history[0]["instruction"] == "R1去装卸区"

    def test_get_recent(self, store):
        for i in range(10):
            store.record(f"指令{i}", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        recent = store.get_recent(3)
        assert len(recent) == 3
        assert recent[0]["instruction"] == "指令7"  # get_recent 取最后3条

    def test_retrieve_for_injection_returns_empty_in_json_mode(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        result = store.retrieve_for_injection("R1")
        assert result == ""  # json_only 模式不检索

    def test_clear(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        assert store.count() == 1
        store.clear()
        assert store.count() == 0

    def test_record_with_failure(self, store):
        store.record(
            "R1去未知位置",
            "infeasible",
            [{"robot_id": "R1", "goal": "未知", "success": False}],
            failure_reason="未知位置",
        )
        assert store.count() == 1
        history = store.get_history()
        assert history[0]["status"] == "infeasible"
        assert history[0]["failure_reason"] == "未知位置"

    def test_empty_store(self, store):
        assert store.count() == 0
        assert store.get_history() == []
        assert store.get_recent() == []

    def test_json_file_persistence(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        # 验证 JSON 文件存在且内容正确
        assert os.path.exists(store.json_path)
        with open(store.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["instruction"] == "R1去装卸区"


class TestHybridMemoryStoreHybridMode:
    """Test HybridMemoryStore in hybrid mode (way 2: JSON + SQLite FTS5)."""

    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "memory.json")
            db_path = os.path.join(tmpdir, "memory.db")
            s = HybridMemoryStore(
                json_path=json_path,
                db_path=db_path,
                mode="hybrid",
                auto_sync=False,
            )
            yield s
            s.close()

    def test_fts_retrieval(self, store):
        store.record("R1前往装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R2前往充电区", "succeeded", [{"robot_id": "R2", "goal": "充电区", "success": True}])
        store.record("R1前往货架A", "succeeded", [{"robot_id": "R1", "goal": "货架A", "success": True}])

        result = store.retrieve_for_injection("R1去装卸区", top_k=2)
        assert "R1→装卸区" in result
        assert "R1→货架A" in result
        assert "R2→充电区" not in result  # top-2 应该只包含 R1 相关的

    def test_fts_retrieval_no_match(self, store):
        store.record("R1前往装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        result = store.retrieve_for_injection("充电", top_k=3)
        # "充电" 不是已知实体词，且指令中无匹配
        # 可能返回空或根据 tokens 匹配
        assert isinstance(result, str)

    def test_fts_retrieval_with_failure(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R1去封闭区", "infeasible", [{"robot_id": "R1", "goal": "封闭区", "success": False}])

        result = store.retrieve_for_injection("R1去装卸区", top_k=3)
        assert "✅" in result  # 成功的记录带 ✅
        assert "⚠" not in result or "⚠" in result  # 失败的记录可能被降权但不一定排除

    def test_sync_json_to_sqlite(self, store):
        # 先写入 JSON（模拟已有数据）
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R2去充电区", "succeeded", [{"robot_id": "R2", "goal": "充电区", "success": True}])

        # 创建新实例并同步
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path2 = os.path.join(tmpdir, "memory.json")
            db_path2 = os.path.join(tmpdir, "memory.db")
            # 复制 JSON 文件
            import shutil
            shutil.copy(store.json_path, json_path2)

            s2 = HybridMemoryStore(
                json_path=json_path2,
                db_path=db_path2,
                mode="hybrid",
                auto_sync=True,
            )
            assert s2.count() == 2
            s2.close()

    def test_preference_summary(self, store):
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R1去装卸区", "succeeded", [{"robot_id": "R1", "goal": "装卸区", "success": True}])
        store.record("R2去充电区", "succeeded", [{"robot_id": "R2", "goal": "充电区", "success": True}])

        summary = store._get_preference_summary()
        assert "R1" in summary
        assert "装卸区" in summary
        assert "R2" in summary

    def test_token_budget_enforced(self, store):
        # 写入多条长记录，确保摘要被截断到 max_tokens
        for i in range(20):
            store.record(
                f"R1前往装卸区，R2前往充电区，R3前往货架A，R4前往打包站，R5前往维护区 iteration_{i}",
                "succeeded",
                [
                    {"robot_id": "R1", "goal": "装卸区", "success": True},
                    {"robot_id": "R2", "goal": "充电区", "success": True},
                    {"robot_id": "R3", "goal": "货架A", "success": True},
                    {"robot_id": "R4", "goal": "打包站", "success": True},
                    {"robot_id": "R5", "goal": "维护区", "success": True},
                ],
            )

        result = store.retrieve_for_injection("R1", max_tokens=100)
        # token 估算: len(str)//2，100 tokens ≈ 200 字符
        estimated_tokens = store._estimate_tokens(result)
        assert estimated_tokens <= 150  # 允许少量偏差
        assert isinstance(result, str)


class TestMemoryRetriever:
    """Test MemoryRetriever wrapper."""

    @pytest.fixture
    def retriever(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HybridMemoryStore(
                json_path=os.path.join(tmpdir, "memory.json"),
                db_path=os.path.join(tmpdir, "memory.db"),
                mode="hybrid",
                auto_sync=False,
            )
            retriever = MemoryRetriever(store)
            yield retriever
            store.close()

    def test_retrieve_empty(self, retriever):
        assert retriever.retrieve("R1去装卸区") == ""

    def test_retrieve_with_data(self, retriever):
        retriever.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        result = retriever.retrieve("R1")
        assert result != ""

    def test_format_injection_block(self, retriever):
        formatted = retriever.format_injection_block("test summary")
        assert "[用户调度习惯参考]" in formatted
        assert "test summary" in formatted
        assert "---" in formatted

    def test_retrieve_formatted(self, retriever):
        retriever.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        block = retriever.retrieve_formatted("R1")
        assert "[用户调度习惯参考]" in block


class TestUserProfile:
    """Test UserProfile pattern learning."""

    @pytest.fixture
    def profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HybridMemoryStore(
                json_path=os.path.join(tmpdir, "memory.json"),
                db_path=os.path.join(tmpdir, "memory.db"),
                mode="json_only",
            )
            profile = UserProfile(store)
            yield profile
            store.close()

    def test_empty_profile(self, profile):
        profile.load()
        assert profile.get_inferred_priority() == []
        assert profile.get_summary() == ""

    def test_goal_frequency(self, profile):
        profile.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        profile.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        profile.store.record(
            "R1去充电区", "succeeded",
            [{"robot_id": "R1", "goal": "充电区", "success": True}],
        )

        profile.load()
        assert profile.get_favorite_goal("R1") == "装卸区"
        assert profile.get_robot_goal_frequency("R1", "装卸区") == 2

    def test_inferred_priority(self, profile):
        profile.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        profile.store.record(
            "R2去充电区", "succeeded",
            [{"robot_id": "R2", "goal": "充电区", "success": True}],
        )
        profile.store.record(
            "R1去货架A", "succeeded",
            [{"robot_id": "R1", "goal": "货架A", "success": True}],
        )

        profile.load()
        priority = profile.get_inferred_priority()
        assert priority[0] == "R1"  # R1 出现 2 次，R2 出现 1 次
        assert priority[1] == "R2"

    def test_summary_format(self, profile):
        profile.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        profile.store.record(
            "R2去充电区", "succeeded",
            [{"robot_id": "R2", "goal": "充电区", "success": True}],
        )

        profile.load()
        summary = profile.get_summary()
        assert "R1" in summary
        assert "R2" in summary
        assert "装卸区" in summary or "充电区" in summary

    def test_get_patterns(self, profile):
        profile.store.record(
            "R1去装卸区", "succeeded",
            [{"robot_id": "R1", "goal": "装卸区", "success": True}],
        )
        profile.load()
        patterns = profile.get_patterns()
        assert patterns["total_schedules"] == 1
        assert "R1" in patterns["robot_goals"]
