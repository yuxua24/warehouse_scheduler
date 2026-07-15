"""Unit tests for ContextStore (WeChat iLink Bot state persistence)."""

import json
import tempfile
import os
from pathlib import Path

import pytest

# Ensure project root is on path
import sys
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _project_root)

from app.channels.weixin.context_store import ContextStore


class TestContextStore:
    """Tests for ContextStore — sync_buf and context_token persistence."""

    def test_initial_state_empty(self):
        """新创建的 ContextStore 返回空 sync_buf 和空 token。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ContextStore(data_dir=tmpdir)
            assert store.get_sync_buf() == ""
            assert store.get_context_token("any_user") == ""

    def test_save_and_load_sync_buf(self):
        """保存 sync_buf 后能正确读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ContextStore(data_dir=tmpdir)
            store.save_sync_buf("test_buf_12345")
            assert store.get_sync_buf() == "test_buf_12345"

    def test_sync_buf_empty_string_does_not_overwrite(self):
        """传入空字符串不应覆盖已有的 sync_buf。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ContextStore(data_dir=tmpdir)
            store.save_sync_buf("existing_buf")
            store.save_sync_buf("")  # 空字符串
            assert store.get_sync_buf() == "existing_buf"

    def test_sync_buf_restart_persistence(self):
        """sync_buf 持久化到磁盘，重新创建 ContextStore 后仍能读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = ContextStore(data_dir=tmpdir)
            store1.save_sync_buf("persistent_buf")
            del store1

            store2 = ContextStore(data_dir=tmpdir)
            assert store2.get_sync_buf() == "persistent_buf"

    def test_update_and_get_context_token(self):
        """更新 context_token 后能正确读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ContextStore(data_dir=tmpdir)
            store.update_context_token("user_1", "token_abc")
            store.update_context_token("user_2", "token_xyz")

            assert store.get_context_token("user_1") == "token_abc"
            assert store.get_context_token("user_2") == "token_xyz"
            assert store.get_context_token("unknown") == ""

    def test_context_token_empty_does_not_overwrite(self):
        """传入空 token 不应覆盖已有的 token。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ContextStore(data_dir=tmpdir)
            store.update_context_token("user_1", "existing_token")
            store.update_context_token("user_1", "")  # 空
            assert store.get_context_token("user_1") == "existing_token"

    def test_context_token_persistence(self):
        """context_token 持久化到磁盘，重启后仍能读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = ContextStore(data_dir=tmpdir)
            store1.update_context_token("user_1", "token_1")
            store1.update_context_token("user_2", "token_2")
            del store1

            store2 = ContextStore(data_dir=tmpdir)
            assert store2.get_context_token("user_1") == "token_1"
            assert store2.get_context_token("user_2") == "token_2"

    def test_corrupt_context_token_file(self):
        """损坏的 context_tokens.json 文件应被优雅处理。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 写入损坏的 JSON
            bad_file = Path(tmpdir) / "weixin_context_tokens.json"
            bad_file.write_text("this is not json{{{")

            store = ContextStore(data_dir=tmpdir)
            # 应该成功创建，返回空
            assert store.get_context_token("any") == ""
