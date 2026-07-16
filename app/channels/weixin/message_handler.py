"""iLink 消息处理器：去重、过滤、调用调度引擎、回复。

参照 Hermes Agent gateway/platforms/weixin.py 的 _process_message() 设计。
"""

import hashlib
import logging
import time
import traceback
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

from app.channels.weixin.ilink_client import ILinkClient
from app.channels.weixin.context_store import ContextStore
from app.channels.weixin.reply_formatter import (
    format_schedule_result,
    format_error,
    HELP_TEXT,
)

# 媒体文件临时目录
MEDIA_TMP_DIR = Path(tempfile.gettempdir()) / "warehouse_weixin_media"

logger = logging.getLogger(__name__)

# 去重窗口（秒）
DEDUP_TTL_SECONDS = 300
# 文本批处理窗口（秒）
TEXT_BATCH_WINDOW_SECONDS = 3


class MessageDeduplicator:
    """消息去重器：基于 message_id + 内容 MD5 双重去重。

    每个条目在 TTL 秒后自动过期。
    """

    def __init__(self, ttl: float = DEDUP_TTL_SECONDS):
        self._ttl = ttl
        self._entries: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        """检查是否重复。不重复则记录并返回 False。"""
        self._cleanup()

        now = time.time()
        if key in self._entries:
            return True

        self._entries[key] = now
        return False

    def _cleanup(self) -> None:
        """清理过期条目。"""
        now = time.time()
        expired = [k for k, v in self._entries.items() if now - v > self._ttl]
        for k in expired:
            del self._entries[k]

    @staticmethod
    def make_message_key(sender_id: str, msg_id: str, content: str) -> str:
        """生成消息去重 key。"""
        return f"msg:{msg_id}"

    @staticmethod
    def make_content_key(sender_id: str, content: str) -> str:
        """生成内容 MD5 去重 key。"""
        md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"content:{sender_id}:{md5}"


class MessageHandler:
    """处理 iLink 消息：去重 → 过滤 → 调度 → 回复。

    使用方法:
        handler = MessageHandler(workflow_fn, client, context_store, config)
        await handler.handle(msg)
    """

    def __init__(
        self,
        workflow_fn: Callable[[str], Any],
        client: ILinkClient,
        context_store: ContextStore,
        config: Dict[str, Any],
        location_names: Dict[str, str] = None,
    ):
        self.workflow_fn = workflow_fn
        self.client = client
        self.context_store = context_store
        self.dm_policy = config.get("dm_policy", "open")
        self.allowed_users: Set[str] = set(config.get("allowed_users", []))
        self.deduplicator = MessageDeduplicator(ttl=DEDUP_TTL_SECONDS)
        self.location_names = location_names or {}
        self.media_generator = None  # 由外部设置
        self.cron_manager = None     # 由外部设置
        self.classify_fn = None      # fn(text) -> dict, LLM 意图分类（旧路径）
        self.tool_manager = None     # ToolManager 实例（新路径，优先使用）
        self.answer_fn = None        # fn(text) -> str, LLM 问答

    async def handle(self, msg: Dict[str, Any]) -> None:
        """处理一条 iLink 消息。

        消息结构（参考 Hermes 解析的 iLink 消息体）：
        {
            "message_id": "...",
            "from_user": "wxid_xxx",
            "chat_type": "dm" | "group",
            "content": "文本内容",
            "context_token": "...",
            "message_type": "text",
        }
        """
        try:
            await self._handle_impl(msg)
        except Exception:
            print(f"[weixin] Handler error: {e}")

    # ── 内部实现 ─────────────────────────────────────────────────────────

    async def _handle_impl(self, msg: Dict[str, Any]) -> None:
        # iLink API 消息字段映射:
        #   from_user_id     → 发送者
        #   to_user_id       → 接收者 (Bot)
        #   message_type     → 1=文本
        #   group_id         → 空=私聊，非空=群聊
        #   item_list[0].text_item.text → 文本内容
        #   context_token    → 上下文令牌

        user_id = msg.get("from_user_id", msg.get("from_user", ""))
        msg_id = str(msg.get("message_id", ""))
        group_id = msg.get("group_id", "")
        context_token = msg.get("context_token", "")

        # 1. 过滤自己的消息
        if user_id == self.client.account_id:
            return

        # 2. 仅处理私聊
        if group_id:
            return  # 群聊消息，忽略

        # 3. 提取文本内容
        content = ""
        item_list = msg.get("item_list", [])
        if item_list:
            text_item = item_list[0].get("text_item", {})
            content = text_item.get("text", "")

        if not content:
            return  # 非文本或无内容

        # 4. 去重（仅基于 message_id，避免用户重复发送相同文本被误判）
        if content and self.deduplicator.is_duplicate(
            MessageDeduplicator.make_message_key(user_id, msg_id, content)
        ):
            return

        # 5. 保存 context_token
        if context_token:
            self.context_store.update_context_token(user_id, context_token)

        # 6. 访问控制
        if self.dm_policy == "disabled":
            return
        if self.dm_policy == "allowlist":
            if user_id not in self.allowed_users:
                await self._send_reply(user_id, "❌ 无权限，请联系管理员")
                return

        # 7. 处理指令
        text = content.strip()
        if not text:
            await self._send_reply(user_id, "请输入调度指令，发送「帮助」查看说明")
            return

        print(f"[weixin] Processing msg from {user_id}: {text[:80]}")

        if text in ("帮助", "help", "?", "/help"):
            await self._send_reply(user_id, HELP_TEXT)
            return

        # === 工具调用 或 LLM 意图分类 + 路由 ===
        intent_info = {"intent": "schedule"}

        # 快速路径：以 "定时" 开头 → 直接走 cron 命令（不调 LLM）
        if text.startswith("定时"):
            reply = await self._handle_cron_command(text, user_id)
            await self._send_reply(user_id, reply)
            return

        # 主路径 1（新）：ToolManager 工具调用
        if self.tool_manager is not None:
            try:
                result = self.tool_manager.process(text)
                if result["success"]:
                    data = result.get("data", "")
                    if isinstance(data, dict):
                        reply = data.get("summary", str(data))
                    else:
                        reply = str(data)
                else:
                    reply = f"❌ {result.get('error', 'Unknown error')}"
                print(f"[weixin] ToolManager: {result.get('tool_name')} ({result.get('llm_time_ms', 0):.0f}ms)")
                await self._send_reply(user_id, reply)
                # 调度成功时生成图片
                if result.get("tool_name") == "schedule_robots" and self.media_generator:
                    try:
                        state = self.workflow_fn(text)
                        png_path, _gif_path = self.media_generator(state)
                        if png_path and Path(png_path).exists():
                            await self.client.send_image(
                                to_user=user_id, file_path=str(png_path),
                                context_token=context_token,
                            )
                    except Exception:
                        pass
                return
            except Exception as e:
                print(f"[weixin] ToolManager failed: {e}")
                # 降级到旧路径

        # 主路径 2（旧）：LLM 意图分类 + if/elif
        if self.classify_fn:
            try:
                intent_info = self.classify_fn(text)
            except Exception:
                pass  # LLM 失败则走默认 schedule

        intent = intent_info.get("intent", "schedule")
        print(f"[weixin] Intent: {intent}")
        state = None  # 只有 schedule 意图才有

        if intent == "schedule":
            # 调度指令 → Workflow.run()
            try:
                state = self.workflow_fn(text)
                reply = format_schedule_result(state, location_names=self.location_names)
            except Exception as e:
                print(f"[weixin] Workflow failed: {e}")
                reply = format_error(f"调度处理异常: {e}")

        elif intent == "cron_list":
            reply = self._format_cron_list()

        elif intent == "cron_create":
            reply = self._create_cron_from_intent(intent_info)

        elif intent == "cron_delete":
            reply = self._delete_cron_from_intent(intent_info)

        elif intent == "cron_delete_all":
            reply = self._handle_delete_all_from_intent(intent_info)

        elif intent == "cron_toggle":
            reply = self._toggle_cron_from_intent(intent_info)

        elif intent == "general_qa":
            reply = await self._answer_question(text)

        elif intent == "show_map":
            reply = "🗺️ 当前地图已渲染"

        elif intent == "cargo_done":
            reply = self._handle_cargo_done_wx()

        elif intent == "robot_move":
            reply = self._handle_robot_move_wx(intent_info)

        elif intent == "map_modify":
            reply = self._handle_map_modify_wx(intent_info)

        else:
            # unknown → 尝试 LLM 问答兜底
            try:
                reply = await self._answer_question(text)
            except Exception:
                reply = "⚠️ 无法理解，请尝试：调度指令 / 查看定时任务 / 询问仓库信息"
        await self._send_reply(user_id, reply)
        print(f"[weixin] Reply sent")

        # 10. 生成并发送静态路径图（仅调度成功时）
        if self.media_generator and state is not None:
            try:
                png_path, _gif_path = self.media_generator(state)
                if png_path and Path(png_path).exists():
                    await self.client.send_image(
                        to_user=user_id,
                        file_path=str(png_path),
                        context_token=context_token,
                    )
            except Exception as e:
                print(f"[weixin] Media send failed: {e}")
                import traceback; traceback.print_exc()

    async def _handle_cron_command(self, text: str, user_id: str) -> str:
        """处理定时任务指令。

        支持格式:
        - 定时 列表
        - 定时 删除 <任务名>
        - 定时 禁用 <任务名>
        - 定时 启用 <任务名>
        - 定时 <描述> <指令>
            如: 定时 每晚十点 所有机器人返回充电区
        """
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"

        text = text.strip()
        parts = text.split(None, 1)  # ["定时", "剩余部分"]
        rest = parts[1].strip() if len(parts) > 1 else ""

        # 定时 列表
        if rest in ("列表", "list", "ls"):
            jobs = self.cron_manager.list_jobs()
            if not jobs:
                return "⏰ 暂无定时任务"
            lines = ["⏰ **定时任务列表**", "━" * 16]
            for j in jobs:
                icon = "🔵" if j.enabled else "⚪"
                last = f"上次: {j.last_run_at[:16]}" if j.last_run_at else "尚未执行"
                lines.append(
                    f"{icon} **{j.name}** · {_cron_to_readable(j.cron_expr)}\n"
                    f"   {j.instruction[:40]}\n"
                    f"   {last} | {'✅' if j.last_result == 'succeeded' else '❌' if j.last_result else '—'}"
                )
            lines.append("━" * 16)
            lines.append(f"共 {len(jobs)} 个任务")
            return "\n".join(lines)

        # 定时 删除 <name>
        if rest.startswith("删除 ") or rest.startswith("del "):
            name = rest.split(None, 1)[1].strip()
            for j in self.cron_manager.list_jobs():
                if j.name == name:
                    self.cron_manager.remove_job(j.job_id)
                    return f"🗑️ 已删除定时任务「{name}」"
            return f"❌ 未找到任务「{name}」"

        # 定时 禁用 <name>
        if rest.startswith("禁用 ") or rest.startswith("disable "):
            name = rest.split(None, 1)[1].strip()
            for j in self.cron_manager.list_jobs():
                if j.name == name:
                    self.cron_manager.toggle_job(j.job_id, False)
                    return f"⏸️ 已禁用定时任务「{name}」"
            return f"❌ 未找到任务「{name}」"

        # 定时 启用 <name>
        if rest.startswith("启用 ") or rest.startswith("enable "):
            name = rest.split(None, 1)[1].strip()
            for j in self.cron_manager.list_jobs():
                if j.name == name:
                    self.cron_manager.toggle_job(j.job_id, True)
                    return f"▶️ 已启用定时任务「{name}」"
            return f"❌ 未找到任务「{name}」"

        # 定时 <描述> <指令> — 创建定时任务
        # 格式: 定时 每晚十点 所有机器人返回充电区
        # 最后一段是调度指令，前面描述用来做任务名
        if rest:
            # 简单规则: 按空格分词，取第一段做名称
            words = rest.split()
            if len(words) >= 2:
                name = " ".join(words[:2])  # 前两个词做名称
                instruction = " ".join(words[2:]) if len(words) > 2 else rest
            else:
                name = rest
                instruction = rest

            # Cron 表达式: 用简单规则映射
            cron_expr = _guess_cron(rest)
            if not cron_expr:
                return (
                    "❌ 无法解析时间描述。请使用标准 cron 格式:\n"
                    "定时 <名称> <cron> <指令>\n"
                    "如: 定时 充电 0 22 * * * 所有机器人返回充电区\n\n"
                    "常用 Cron:\n"
                    "每天22:00 → `0 22 * * *`\n"
                    "工作日8:00 → `0 8 * * 1-5`\n"
                    "每小时 → `0 * * * *`"
                )

            try:
                job = self.cron_manager.add_job(name, cron_expr, instruction)
                return (
                    f"⏰ 定时任务已创建\n"
                    f"━" * 16 + "\n"
                    f"🔵 **{job.name}**\n"
                f"   时间: {_cron_to_readable(job.cron_expr)} (`{job.cron_expr}`)\n"
                    f"   指令: {job.instruction}\n"
                    f"━" * 16 + "\n"
                    f"发送「定时 列表」查看所有任务"
                )
            except Exception as e:
                return f"❌ 创建失败: {e}"

        return (
            "📋 **定时任务指令**\n\n"
            "• `定时 列表` — 查看所有任务\n"
            "• `定时 删除 <名称>` — 删除任务\n"
            "• `定时 禁用 <名称>` — 暂停任务\n"
            "• `定时 启用 <名称>` — 恢复任务\n"
            "• `定时 <描述> <cron> <指令>` — 创建\n"
            "  如: `定时 充电 0 22 * * * 所有机器人回充电区`"
        )

    def _format_cron_list(self) -> str:
        """格式化定时任务列表。"""
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"
        jobs = self.cron_manager.list_jobs()
        if not jobs:
            return "⏰ 暂无定时任务\n\n发送「定时 <描述> <cron> <指令>」创建"
        lines = ["⏰ **定时任务列表**", "━" * 16]
        for j in jobs:
            icon = "🔵" if j.enabled else "⚪"
            last = f"上次: {j.last_run_at[:16]}" if j.last_run_at else "尚未执行"
            status = "✅" if j.last_result == "succeeded" else ("❌" if j.last_result else "—")
            lines.append(
                f"{icon} **{j.name}** · {_cron_to_readable(j.cron_expr)}\n"
                f"   {j.instruction[:50]}\n"
                f"   {last} | {status}"
            )
        lines.append("━" * 16)
        lines.append(f"共 {len(jobs)} 个任务")
        return "\n".join(lines)

    def _create_cron_from_intent(self, info: dict) -> str:
        """从 LLM 意图信息创建定时任务。"""
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"
        name = info.get("cron_name", "定时任务")
        cron_expr = info.get("cron_expr", "")
        instruction = info.get("cron_instruction", "")
        if not cron_expr or not instruction:
            return (
                "❌ 创建定时任务需要 cron 表达式和调度指令。\n"
                "请使用格式: 定时 <名称> <cron> <指令>"
            )
        try:
            job = self.cron_manager.add_job(name, cron_expr, instruction)
            return (
                f"⏰ 定时任务已创建\n━" + "━" * 14 + "\n"
                f"🔵 **{job.name}**\n"
                f"   Cron: `{job.cron_expr}`\n"
                f"   指令: {job.instruction}\n━" + "━" * 14
            )
        except Exception as e:
            return f"❌ 创建失败: {e}"

    def _delete_cron_from_intent(self, info: dict) -> str:
        """从 LLM 意图信息删除定时任务。"""
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"
        target = info.get("target_job_name", "")
        if not target:
            return "❌ 请指定要删除的任务名称"
        for j in self.cron_manager.list_jobs():
            if target in j.name:
                self.cron_manager.remove_job(j.job_id)
                return f"🗑️ 已删除「{j.name}」"
        return f"❌ 未找到包含「{target}」的任务"

    def _toggle_cron_from_intent(self, info: dict) -> str:
        """从 LLM 意图信息切换定时任务状态。"""
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"
        target = info.get("target_job_name", "")
        enable = "启用" in info.get("intent", "") or "enable" in str(info)
        if not target:
            return "❌ 请指定要操作的任务名称"
        for j in self.cron_manager.list_jobs():
            if target in j.name:
                self.cron_manager.toggle_job(j.job_id, enable)
                return f"{'▶️' if enable else '⏸️'} 已{'启用' if enable else '禁用'}「{j.name}」"
        return f"❌ 未找到包含「{target}」的任务"

    def _handle_delete_all_from_intent(self, info: dict) -> str:
        """处理删除全部定时任务（含确认）。"""
        if not self.cron_manager:
            return "❌ 定时任务功能未启用"
        jobs = self.cron_manager.list_jobs()
        if not jobs:
            return "⏰ 当前没有定时任务可删除"
        if info.get("confirmed"):
            count = len(jobs)
            for j in list(jobs):
                self.cron_manager.remove_job(j.job_id)
            return f"🗑️ 已删除全部 {count} 个定时任务"
        return (
            f"⚠️ **确定要删除全部 {len(jobs)} 个定时任务吗？**\n"
            "回复「确认」执行删除，回复「取消」放弃操作。"
        )

    def _handle_cargo_done_wx(self) -> str:
        """微信端卸货确认。"""
        from app.services.robot_selector import get_waiting_robots, get_busy_robots, mark_robot_idle
        results = []
        for r in get_waiting_robots():
            mark_robot_idle(r["robot_id"])
            results.append(f"✅ {r['robot_id']} 卸货完成")
        for r in get_busy_robots():
            mark_robot_idle(r["robot_id"])
            results.append(f"✅ {r['robot_id']} 卸货完成")
        return "\n".join(results) if results else "⚠️ 没有正在执行任务的机器人"

    def _handle_robot_move_wx(self, info: dict) -> str:
        """微信端移动机器人。"""
        import re, json
        from pathlib import Path
        rid = info.get("robot_id", "").strip().upper()
        rid = re.sub(r"机器人\s*", "R", rid)
        if not rid.startswith("R"): rid = "R" + rid
        pos = info.get("target_position", None)
        if not rid or not pos or len(pos) != 2: return "❌ 请指定机器人ID和坐标"
        x, y = int(pos[0]), int(pos[1])
        rp = Path("configs/warehouse_runtime.json")
        try:
            runtime = json.loads(rp.read_text(encoding="utf-8"))
            for r in runtime.get("robots", []):
                if r["robot_id"] == rid:
                    r["position"] = [x, y]
                    rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
                    return f"🤖 {rid} 已移动到 ({x},{y})"
            return f"❌ 未找到{rid}"
        except Exception as e: return f"❌ {e}"

    def _handle_map_modify_wx(self, info: dict) -> str:
        """微信端地图修改。"""
        import json
        from pathlib import Path
        action = info.get("map_action", ""); target = info.get("map_target", "")
        if action == "block_corridor" and target:
            rp = Path("configs/warehouse_runtime.json")
            runtime = json.loads(rp.read_text(encoding="utf-8"))
            runtime.setdefault("active_blockages", []).append({
                "blockage_id": f"wx_{target}", "target_type": "corridor", "target_id": target,
                "start_time": 0, "reason": "微信指令"
            })
            rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"🔒 已封闭「{target}」"
        return "❌ 不支持的操作"

    async def _answer_question(self, text: str) -> str:
        """调用 LLM 回答问题。"""
        if self.answer_fn:
            try:
                result = self.answer_fn(text)
                if hasattr(result, '__await__'):
                    return await result
                return result
            except Exception as e:
                return f"❌ 回答失败: {e}"
        return "⚠️ 问答功能未配置"

    async def _send_reply(self, user_id: str, text: str) -> None:
        """发送回复消息。"""
        ctx_token = self.context_store.get_context_token(user_id)
        print(f"[weixin] Sending to {user_id}, ctx_token_len={len(ctx_token)}, text_len={len(text)}")
        try:
            result = await self.client.sendmessage(
                to_user=user_id,
                text=text,
                context_token=ctx_token,
            )
            print(f"[weixin] Send result: {result}")
        except Exception as e:
            print(f"[weixin] Failed to send reply to {user_id}: {e}")
            import traceback; traceback.print_exc()


# ── Cron 表达式 → 可读时间 ──────────────────────────────────────────────


def _cron_to_readable(cron_expr: str) -> str:
    """将 cron 表达式转为 24h 制可读时间。

    "0 22 * * *" → "每天 22:00"
    "0 8 * * 1-5" → "工作日 08:00"
    "*/30 * * * *" → "每 30 分钟"
    "0 * * * *" → "每小时"
    """
    parts = cron_expr.strip().split()
    if len(parts) < 5:
        return cron_expr

    minute, hour, dom, month, dow = parts[:5]

    # 特殊模式
    if minute.startswith("*/"):
        return f"每 {minute[2:]} 分钟"
    if minute == "0" and hour == "*" and dow == "*":
        return "每小时整点"

    # 构建时间
    h = int(hour) if hour.isdigit() else 0
    m = int(minute) if minute.isdigit() else 0
    time_str = f"{h:02d}:{m:02d}"

    # 构建日期描述
    if dow == "*" and dom == "*":
        freq = "每天"
    elif dow == "1-5":
        freq = "工作日"
    elif dow in ("6,0", "0,6"):
        freq = "周末"
    elif dow == "1":
        freq = "每周一"
    elif dow == "2":
        freq = "每周二"
    elif dow == "3":
        freq = "每周三"
    elif dow == "4":
        freq = "每周四"
    elif dow == "5":
        freq = "每周五"
    elif dow == "6":
        freq = "每周六"
    elif dow == "0":
        freq = "每周日"
    else:
        freq = f"cron: {cron_expr}"

    return f"{freq} {time_str}"


def _guess_cron(text: str) -> str:
    """从自然语言描述尝试推断 cron 表达式。"""
    import re

    # 已经是标准 cron 格式
    cron_pattern = r"^(\S+\s+){4,5}\S+$"
    if re.match(cron_pattern, text):
        parts = text.strip().split()[:5]
        return " ".join(parts)

    # 中文数字映射
    cn_digits = {
        "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        "十一": 11, "十二": 12, "十三": 13, "十四": 14,
        "十五": 15, "十六": 16, "十七": 17, "十八": 18,
        "十九": 19, "二十": 20, "二十一": 21, "二十二": 22, "二十三": 23,
    }

    hour = None
    minute = 0
    weekday = "*"

    # 提取时间 (如 22:00, 8:30)
    time_match = re.search(r"(\d{1,2})[:：](\d{2})", text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
    else:
        # 阿拉伯数字 + [点时] (如 8点, 22时)
        hour_match = re.search(r"(\d{1,2})\s*[点时]", text)
        if hour_match:
            hour = int(hour_match.group(1))
        else:
            # 中文数字 + [点时] (如 十点, 八点)
            cn_match = re.search(
                r"(十一|十二|十三|十四|十五|十六|十七|十八|十九|"
                r"二十一|二十二|二十三|"
                r"二十|十|[零一二两三四五六七八九])\s*[点时]",
                text,
            )
            if cn_match:
                cn_word = cn_match.group(1)
                hour = cn_digits.get(cn_word)
                if hour is None and "十" in cn_word:
                    hour = 10

        # 下午/晚上 +12
        if hour is not None:
            if "晚" in text or "夜" in text or "晚上" in text:
                if hour is not None and hour < 12:
                    hour += 12
            elif "下午" in text and hour < 12:
                hour += 12

    # 提取星期
    if "工作" in text or "周一到周五" in text:
        weekday = "1-5"
    elif "周末" in text:
        weekday = "6,0"
    elif "周一" in text:
        weekday = "1"
    elif "周二" in text:
        weekday = "2"
    elif "周三" in text:
        weekday = "3"
    elif "周四" in text:
        weekday = "4"
    elif "周五" in text:
        weekday = "5"
    elif "周六" in text:
        weekday = "6"
    elif "周日" in text:
        weekday = "0"

    if "每小时" in text or "每隔一小时" in text:
        return "0 * * * *"
    if "每30分钟" in text:
        return "*/30 * * * *"

    if hour is not None:
        return f"{minute} {hour} * * {weekday}"

    return ""
