"""FastAPI server for the Warehouse Robot Scheduling System.

Provides REST API for scheduling, map management, and runtime state.
Serves the React frontend in production.
Integrates WeChat messaging channel via iLink Bot API.
"""

import json
import os
import sys
import uuid
import time
import copy
import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Ensure project root is on path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
_venv_packages = os.path.join(_project_root, ".venv_packages")
if os.path.isdir(_venv_packages):
    sys.path.insert(0, _venv_packages)

from app.orchestration.workflow import Workflow
from app.services.robot_selector import mark_robot_idle, get_waiting_robots, get_busy_robots
from app.api.schemas import (
    SchedulingRequest,
    SchedulingResponse,
    TaskResultOutput,
    MetricsOutput,
    ReplanHistoryOutput,
    TimedPositionOutput,
    CronJobInput,
    CronJobOutput,
    CronJobToggle,
)

# ── App setup ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Warehouse Robot Scheduler",
    description="智能仓储机器人调度系统 API",
    version="0.1.0",
)

# CORS: allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Path resolution ─────────────────────────────────────────────────────────

BASE_DIR = Path(_project_root)
CONFIGS_DIR = BASE_DIR / "configs"
DEFAULT_MAP_PATH = CONFIGS_DIR / "warehouse_map.json"
DEFAULT_RUNTIME_PATH = CONFIGS_DIR / "warehouse_runtime.json"
API_CONFIG_PATH = CONFIGS_DIR / "api_config.json"
WEIXIN_CONFIG_PATH = CONFIGS_DIR / "weixin_config.json"
WEIXIN_ACCOUNT_PATH = CONFIGS_DIR / "weixin_account.json"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# ── Workflow cache ───────────────────────────────────────────────────────────

_workflow: Optional[Workflow] = None
_cron_manager = None
_pending_confirm = {}  # 确认存储: {session_key: {"action": "delete_all", "expires": timestamp}}


def get_workflow(
    map_path: Optional[str] = None,
    runtime_path: Optional[str] = None,
    max_timestep: int = 200,
) -> Workflow:
    """Get or create a Workflow instance."""
    global _workflow
    mp = map_path or str(DEFAULT_MAP_PATH)
    rp = runtime_path or str(DEFAULT_RUNTIME_PATH)
    acp = str(API_CONFIG_PATH) if API_CONFIG_PATH.exists() else None

    if _workflow is None or _workflow.map_path != mp or _workflow.runtime_path != rp:
        _workflow = Workflow(
            map_path=mp,
            runtime_path=rp,
            api_config_path=acp,
            max_timestep=max_timestep,
        )
    return _workflow


def reset_workflow():
    """Reset workflow cache (after map/runtime changes)."""
    global _workflow
    _workflow = None


# ── WeChat (iLink Bot API) integration ──────────────────────────────────────

_weixin_task: asyncio.Task = None


def _start_weixin():
    """启动微信通道（作为后台 asyncio 任务）。"""
    global _weixin_task

    if not WEIXIN_CONFIG_PATH.exists():
        print("[weixin] Config file not found, skipping")
        return

    try:
        config = json.loads(WEIXIN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[weixin] Failed to read config: {e}")
        return

    if not config.get("enabled"):
        print("[weixin] Disabled in config")
        return

    # 加载账户凭证
    if not WEIXIN_ACCOUNT_PATH.exists():
        print("[weixin] Account file not found. Run: python scripts/weixin_login.py")
        return

    try:
        account = json.loads(WEIXIN_ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[weixin] Failed to read account file: {e}")
        return

    token = account.get("token", "")
    account_id = account.get("account_id", "")
    if not token or not account_id:
        print("[weixin] Account missing token or account_id")
        return

    # 动态导入（避免未安装 aiohttp 时阻止启动）
    try:
        from app.channels.weixin.ilink_client import ILinkClient
        from app.channels.weixin.context_store import ContextStore
        from app.channels.weixin.message_handler import MessageHandler
        from app.channels.weixin.poller import MessagePoller
    except ImportError as e:
        print(f"[weixin] Dependencies missing: {e}")
        return

    base_url = account.get("base_url", "https://ilinkai.weixin.qq.com")
    data_dir = config.get("data_dir", "configs")

    # 初始化组件
    client = ILinkClient(
        token=token,
        account_id=account_id,
        base_url=base_url,
    )

    context_store = ContextStore(data_dir=data_dir)

    # Workflow 的 lambda：延迟获取（避免循环依赖）
    def workflow_fn(instruction: str):
        wf = get_workflow()
        return wf.run(instruction)

    # 构建位置名称映射（location_id → 中文名）
    location_names = {}
    wf = get_workflow()
    if wf.warehouse_map:
        for loc in wf.warehouse_map.locations:
            location_names[loc.location_id] = loc.name

    handler = MessageHandler(
        workflow_fn=workflow_fn,
        client=client,
        context_store=context_store,
        config=config,
        location_names=location_names,
    )

    # 创建媒体生成器（生成静态路径图）
    def make_media_generator(wf):
        from app.visualization.renderer import render_paths
        from app.domain.path_models import PathPlanResult
        import matplotlib
        matplotlib.use("Agg")
        import os

        def generate(state):
            """生成静态 PNG 路径图，返回 (png_path, None)。"""
            warehouse_map = wf.warehouse_map
            if not warehouse_map:
                return None, None

            paths = {}
            for tr in state.task_results:
                if tr.success and tr.path:
                    paths[tr.robot_id] = PathPlanResult(
                        success=True, path=tr.path, cost=len(tr.path),
                    )

            if not paths:
                return None, None

            os.makedirs("configs/media", exist_ok=True)
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            png_path = f"configs/media/{ts}_{state.request_id}.png"
            try:
                render_paths(warehouse_map, paths, title="Robot Paths", block=False)
                import matplotlib.pyplot as plt
                plt.savefig(png_path, dpi=200, bbox_inches="tight")
                plt.close()
            except Exception as e:
                print(f"[weixin] Failed to save map: {e}")
                png_path = None

            return png_path, None

        return generate

    handler.media_generator = make_media_generator(wf)

    # 注入定时任务管理器（微信端支持定时指令）
    global _cron_manager
    if _cron_manager:
        handler.cron_manager = _cron_manager

    # 注入 LLM 功能
    wf_instance = get_workflow()
    if wf_instance and wf_instance.parser:
        handler.classify_fn = wf_instance.parser.classify_intent
        # 问答回调
        async def answer_fn(text):
            return await _answer_question(text)
        handler.answer_fn = answer_fn

    poller = MessagePoller(
        client=client,
        handler=handler.handle,
        context_store=context_store,
    )

    # 启动长轮询（后台任务）
    _weixin_task = poller.start()
    print(f"[weixin] Channel started (account_id={account_id[:20]}...)")


def _start_cron():
    """启动定时任务调度器。"""
    global _cron_manager

    try:
        from app.scheduler import CronManager

        def workflow_fn(instruction: str):
            wf = get_workflow()
            return wf.run(instruction)

        _cron_manager = CronManager(
            workflow_fn=workflow_fn,
            jobs_path=str(CONFIGS_DIR / "cron_jobs.json"),
        )
        _cron_manager.start()
    except Exception as e:
        print(f"[cron] Failed to start: {e}")


@app.on_event("startup")
async def startup_event():
    """FastAPI 启动事件：初始化定时任务和微信通道。"""
    _start_cron()     # 先启动 cron（微信通道需要引用它）
    _start_weixin()


@app.on_event("shutdown")
async def shutdown_event():
    """FastAPI 关闭事件：停止微信通道。"""
    global _weixin_task
    if _weixin_task and not _weixin_task.done():
        _weixin_task.cancel()
        try:
            await _weixin_task
        except asyncio.CancelledError:
            pass


# ── Helper: convert PlanningState → SchedulingResponse ──────────────────────

def _state_to_response(state) -> dict:
    """Convert internal PlanningState to JSON-safe SchedulingResponse dict."""
    tasks = []
    for tr in state.task_results:
        path = []
        for p in tr.path:
            path.append({"x": p.x, "y": p.y, "time": p.time})
        tasks.append({
            "robot_id": tr.robot_id,
            "success": tr.success,
            "path": path,
            "makespan": path[-1]["time"] if path else 0,
            "failure_reason": tr.failure_reason,
            "replanned": tr.replanned,
            "start": list(tr.task.start) if tr.task else None,
            "goal": list(tr.task.selected_goal) if tr.task and tr.task.selected_goal else None,
            "goal_location_id": tr.task.goal_location_id if tr.task else None,
        })

    metrics = None
    if state.metrics:
        metrics = {
            "total_task_count": state.metrics.total_task_count,
            "planned_task_count": state.metrics.planned_task_count,
            "planning_failed_task_count": state.metrics.planning_failed_task_count,
            "planning_success_rate": state.metrics.planning_success_rate,
            "total_planning_time_ms": state.metrics.total_planning_time_ms,
            "parsing_time_ms": state.metrics.parsing_time_ms,
            "initial_planning_time_ms": state.metrics.initial_planning_time_ms,
            "replanning_time_ms": state.metrics.replanning_time_ms,
            "average_planning_time_per_task_ms": state.metrics.average_planning_time_per_task_ms,
            "initial_conflict_count": state.metrics.initial_conflict_count,
            "final_conflict_count": state.metrics.final_conflict_count,
            "replanning_triggered": state.metrics.replanning_triggered,
            "retry_count": state.metrics.retry_count,
            "astar_call_count": state.metrics.astar_call_count,
            "total_expanded_nodes": state.metrics.total_expanded_nodes,
        }

    replan_history = []
    for rd in state.replan_history:
        replan_history.append({
            "retry_index": getattr(rd, "retry_index", 0),
            "action": getattr(rd, "action", str(rd)),
            "affected_robots": getattr(rd, "affected_robots", []),
            "robot_to_replan": getattr(rd, "robot_to_replan", []),
            "explanation": getattr(rd, "explanation", ""),
        })

    return {
        "request_id": state.request_id,
        "batch_status": state.status.value,
        "tasks": tasks,
        "metrics": metrics,
        "replan_history": replan_history,
        "warnings": list(state.warnings),
        "errors": list(state.errors),
        "original_instruction": state.original_instruction,
    }


# ── API Routes ──────────────────────────────────────────────────────────────


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": time.time()}


@app.post("/api/schedule")
async def schedule(request: SchedulingRequest):
    """Run the scheduling workflow.

    Accepts either a natural language instruction or pre-structured tasks.
    Returns paths, metrics, and diagnostics.
    """
    wf = get_workflow(
        map_path=request.map_path,
        runtime_path=request.runtime_path,
        max_timestep=request.max_timestep,
    )

    # Check map loaded
    if wf.warehouse_map is None:
        return JSONResponse(
            status_code=400,
            content={
                "request_id": str(uuid.uuid4())[:8],
                "batch_status": "infeasible",
                "tasks": [],
                "metrics": None,
                "replan_history": [],
                "warnings": [],
                "errors": list(wf.map_errors),
                "original_instruction": request.instruction or "",
            },
        )

    # Determine mode: NL vs structured
    if request.instruction:
        state = wf.run(request.instruction)
    elif request.tasks:
        # Build structured tasks dict
        tasks_dict = {"tasks": []}
        for t in request.tasks:
            tasks_dict["tasks"].append({
                "robot_id": t.robot_id,
                "start": t.start,
                "goal_location_id": t.goal_location_id,
                "priority": t.priority,
            })
        # Add constraints
        if request.constraints:
            tasks_dict["runtime_constraints"] = []
            for c in request.constraints:
                tasks_dict["runtime_constraints"].append(c.model_dump())
        state = wf.run_structured(tasks_dict)
    else:
        return JSONResponse(
            status_code=400,
            content={
                "request_id": str(uuid.uuid4())[:8],
                "batch_status": "infeasible",
                "tasks": [],
                "errors": ["Either instruction or tasks must be provided"],
                "original_instruction": "",
            },
        )

    return _state_to_response(state)


class ChatRequest(BaseModel):
    message: str = Field(..., description="用户自然语言消息")


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """通用对话端点：LLM 意图分类 + 路由。

    接收任意自然语言，LLM 判断意图后执行相应操作：
    - schedule → 执行调度
    - cron_list → 返回定时任务列表
    - cron_create → 创建定时任务
    - cron_delete → 删除定时任务
    - cron_toggle → 切换定时任务状态
    """
    wf = get_workflow()
    text = request.message.strip()
    global _cron_manager

    # Step 1: LLM 意图分类
    intent_info = {"intent": "schedule"}
    confirm_needed = None
    if wf and wf.parser:
        try:
            intent_info = wf.parser.classify_intent(text)
        except Exception as e:
            intent_info = {"intent": "schedule"}

    intent = intent_info.get("intent", "schedule")
    reply_text = ""
    schedule_result = None
    image_url = None
    confirm_needed = None

    # ── 根据 LLM 意图分类执行 ──────────────────────────────────────
    if intent == "schedule":
        try:
            state = wf.run(text)
            schedule_result = _state_to_response(state)
            reply_text = _format_schedule_md(state)
            image_url = _generate_path_image(state, text)
            # 更新位置
            _update_robot_positions(state)
        except Exception as e:
            reply_text = f"❌ 调度失败: {e}"

    elif intent == "cron_list":
        global _cron_manager
        if _cron_manager:
            jobs = _cron_manager.list_jobs()
            reply_text = _format_cron_list_md(jobs)
        else:
            reply_text = "❌ 定时任务功能未启用"

    elif intent == "cron_create":
        reply_text = _do_cron_create(intent_info)

    elif intent == "cron_delete":
        reply_text = _do_cron_delete(intent_info)

    elif intent == "cron_delete_all":
        reply_text, confirm_needed = _handle_delete_all(intent_info)

    elif intent == "map_modify":
        reply_text, confirm_needed = _handle_map_modify(intent_info)

    elif intent == "robot_move":
        reply_text = _handle_robot_move(intent_info)

    elif intent == "cron_toggle":
        reply_text = _do_cron_toggle(intent_info)

    elif intent == "show_map":
        reply_text = "🗺️ 当前地图已渲染，请查看左侧画布"

    elif intent == "general_qa":
        reply_text = await _answer_question(text)

    else:
        # 未知意图 → 尝试问答
        try:
            reply_text = await _answer_question(text)
        except Exception:
            reply_text = "⚠️ 无法理解，请尝试：调度指令 / 查看定时任务 / 询问仓库信息"

    return {
        "intent": intent,
        "reply": reply_text,
        "schedule": schedule_result,
        "cron_jobs": _cron_jobs_list(),
        "confirm_needed": confirm_needed,
        "image_url": image_url,
    }


def _has_robot_id(text: str) -> bool:
    import re; return bool(re.search(r"R\d", text, re.IGNORECASE))


def _auto_select_robot(text: str, wf) -> str:
    import re
    if re.search(r"所有|全部|每个|各|都", text): return text
    if len(re.findall(r"R\d", text, re.IGNORECASE)) >= 2: return text
    return text


def _update_robot_positions(state) -> None:
    if state.status.value not in ("succeeded", "partially_succeeded"): return
    rp = CONFIGS_DIR / "warehouse_runtime.json"
    try:
        runtime = json.loads(rp.read_text(encoding="utf-8"))
        for tr in state.task_results:
            if tr.success and tr.path:
                last = tr.path[-1]
                for r in runtime.get("robots", []):
                    if r["robot_id"] == tr.robot_id:
                        r["position"] = [last.x, last.y]
        rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
        reset_workflow()
    except Exception: pass


def _handle_cargo_done(text: str) -> str:
    wf = get_workflow()
    results = []
    waiting = get_waiting_robots()
    if waiting:
        for r in waiting:
            msg = _return_to_parking(r["robot_id"])
            results.append(f"✅ {r['robot_id']} 卸货完成\n{msg}")
    if not results:
        busy = get_busy_robots()
        for r in busy:
            msg = _return_to_parking(r["robot_id"])
            results.append(f"✅ {r['robot_id']} 卸货完成\n{msg}")
    if not results:
        results.append("⚠️ 没有正在执行任务的机器人")
    return "\n\n".join(results)


def _return_to_parking(robot_id: str) -> str:
    mark_robot_idle(robot_id)
    wf = get_workflow()
    pos = wf.robot_registry.get_position(robot_id) if wf else None
    pos_str = f"({pos[0]},{pos[1]})" if pos else "当前位置"
    return f"🤖 {robot_id} 已标记为空闲，在{pos_str}"


async def _answer_question(question: str) -> str:
    import asyncio
    wf = get_workflow()
    if not wf or not wf.parser: return "❌ 系统未初始化"
    ctx = [f"仓库 {wf.warehouse_map.width}×{wf.warehouse_map.height}"]
    for loc in wf.warehouse_map.locations:
        ctx.append(f"{loc.name}({loc.type}): 设施{loc.facility_cells} 入口{loc.entry_cells}")
    for rid in wf.robot_registry.get_robot_ids():
        pos = wf.robot_registry.get_position(rid)
        ctx.append(f"机器人{rid}在{list(pos)}")
    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, lambda: wf.parser.client.chat.completions.create(
            model=wf.parser.model,
            messages=[{"role":"system","content":f"你是仓储助手，简洁回答。\n{chr(10).join(ctx)}"},{"role":"user","content":question}],
            temperature=0.3, max_tokens=300))
        return resp.choices[0].message.content.strip()
    except Exception as e: return f"❌ 回答失败: {e}"


def _handle_map_modify(info: dict) -> tuple:
    global _pending_confirm
    action = info.get("map_action", ""); target = info.get("map_target", "")
    if not action or not target: return "❌ 请指定操作和目标", None
    cn = {"block_corridor": "封闭", "unblock_corridor": "开放"}.get(action, action)
    if info.get("confirmed"): return _execute_map_modify(action, target), None
    _pending_confirm["default"] = {"action": "map_modify", "map_action": action, "map_target": target, "expires": time.time() + 120}
    return (f"⚠️ 确定要{cn}「{target}」吗？此操作影响后续调度。回复「确认」执行。"), "map_modify"


def _execute_map_modify(action: str, target: str) -> str:
    wf = get_workflow()
    if not wf or not wf.warehouse_map: return "❌ 地图未加载"
    rp = CONFIGS_DIR / "warehouse_runtime.json"
    runtime = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {"robots": [], "active_blockages": []}
    blockages = runtime.get("active_blockages", [])
    if action == "block_corridor":
        c = wf.warehouse_map.find_corridor(target)
        if not c: return f"❌ 未找到通道「{target}」"
        for b in blockages:
            if b.get("target_id") == c.corridor_id: return f"⚠️ 已被封闭"
        blockages.append({"blockage_id": f"chat_{c.corridor_id}", "target_type": "corridor", "target_id": c.corridor_id, "cells": [[x, y] for x, y in c.cells], "start_time": 0, "reason": "对话指令"})
        rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
        reset_workflow(); return f"🔒 已封闭「{c.name}」"
    elif action == "unblock_corridor":
        c = wf.warehouse_map.find_corridor(target)
        if not c: return f"❌ 未找到通道「{target}」"
        before = len(blockages)
        runtime["active_blockages"] = [b for b in blockages if b.get("target_id") != c.corridor_id]
        if len(runtime["active_blockages"]) == before: return f"⚠️ 未被封闭"
        rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
        reset_workflow(); return f"🔓 已开放「{c.name}」"
    return f"❌ 不支持: {action}"


def _handle_robot_move(info: dict) -> str:
    import re
    rid = info.get("robot_id", "").strip().upper()
    rid = re.sub(r"机器人\s*", "R", rid)
    if not rid.startswith("R"): rid = "R" + rid
    pos = info.get("target_position", None)
    if not rid or not pos or len(pos) != 2: return "❌ 请指定机器人ID和坐标"
    x, y = int(pos[0]), int(pos[1])
    wf = get_workflow()
    if not wf or not wf.warehouse_map: return "❌ 地图未加载"
    if not (0 <= x < wf.warehouse_map.width and 0 <= y < wf.warehouse_map.height): return f"❌ 坐标({x},{y})越界"
    if wf.warehouse_map.is_obstacle(x, y): return f"❌ ({x},{y})是障碍物"
    rp = CONFIGS_DIR / "warehouse_runtime.json"
    try:
        runtime = json.loads(rp.read_text(encoding="utf-8"))
        for r in runtime.get("robots", []):
            if r["robot_id"] == rid:
                old = list(r["position"]); r["position"] = [x, y]
                rp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
                reset_workflow(); return f"🤖 {rid} 已从 {old} 移动到 ({x},{y})"
        return f"❌ 未找到机器人「{rid}」"
    except Exception as e: return f"❌ 修改失败: {e}"


def _generate_path_image(state, instruction: str) -> str:
    """生成路径图片，返回可访问的 URL。"""
    import os, matplotlib
    matplotlib.use("Agg")
    from app.visualization.renderer import render_paths
    from app.domain.path_models import PathPlanResult
    import matplotlib.pyplot as plt

    wf = get_workflow()
    if not wf or not wf.warehouse_map:
        return None

    paths = {}
    for tr in state.task_results:
        if tr.success and tr.path:
            paths[tr.robot_id] = PathPlanResult(success=True, path=tr.path, cost=len(tr.path))

    if not paths:
        return None

    os.makedirs("configs/media", exist_ok=True)
    from datetime import datetime
    ts = datetime.now().strftime("%H%M%S")
    filename = f"{ts}_{state.request_id}.png"
    filepath = f"configs/media/{filename}"

    try:
        render_paths(wf.warehouse_map, paths, title="Robot Paths", block=False)
        plt.savefig(filepath, dpi=150, bbox_inches="tight")
        plt.close()
        return f"/media/{filename}"
    except Exception as e:
        print(f"[chat] Image generation failed: {e}")
        return None


@app.get("/media/{filename}")
async def serve_media(filename: str):
    """Serve generated media files."""
    from fastapi.responses import FileResponse
    filepath = CONFIGS_DIR.parent / "configs" / "media" / filename
    if filepath.exists():
        return FileResponse(str(filepath))
    raise HTTPException(status_code=404)


def _format_schedule_md(state) -> str:
    """将调度结果格式化为 Markdown。"""
    from app.channels.weixin.reply_formatter import format_schedule_result
    location_names = {}
    if state and state.task_results:
        wf = get_workflow()
        if wf and wf.warehouse_map:
            for loc in wf.warehouse_map.locations:
                location_names[loc.location_id] = loc.name
    return format_schedule_result(state, location_names=location_names)


def _format_cron_list_md(jobs) -> str:
    """格式化定时任务列表。"""
    from app.channels.weixin.message_handler import _cron_to_readable
    if not jobs:
        return "⏰ 暂无定时任务"
    lines = ["⏰ **定时任务列表**", "━━━━━━━━━━━━━━━━"]
    for j in jobs:
        icon = "🔵" if j.enabled else "⚪"
        time_str = _cron_to_readable(j.cron_expr)
        status = "✅" if j.last_result == "succeeded" else ("❌" if j.last_result else "—")
        lines.append(f"{icon} **{j.name}** · {time_str}")
        lines.append(f"   {j.instruction[:50]}")
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append(f"共 {len(jobs)} 个任务")
    return "\n".join(lines)


def _cron_jobs_list() -> list:
    """获取当前定时任务列表（供前端使用）。"""
    global _cron_manager
    if not _cron_manager:
        return []
    return [
        {
            "job_id": j.job_id, "name": j.name,
            "cron_expr": j.cron_expr, "instruction": j.instruction,
            "enabled": j.enabled, "created_at": j.created_at,
            "last_run_at": j.last_run_at, "last_result": j.last_result,
        }
        for j in _cron_manager.list_jobs()
    ]


def _do_cron_create(info: dict) -> str:
    global _cron_manager
    if not _cron_manager:
        return "❌ 定时任务功能未启用"
    name = info.get("cron_name", "定时任务")
    expr = info.get("cron_expr", "")
    inst = info.get("cron_instruction", "")
    if not expr or not inst:
        return "❌ 信息不完整，请提供时间描述和调度指令"
    try:
        job = _cron_manager.add_job(name, expr, inst)
        from app.channels.weixin.message_handler import _cron_to_readable
        return f"⏰ 已创建「**{job.name}**」· {_cron_to_readable(expr)}"
    except Exception as e:
        return f"❌ 创建失败: {e}"


def _do_cron_delete(info: dict) -> str:
    global _cron_manager
    target = info.get("target_job_name", "")
    if not target:
        return "❌ 请指定任务名称"
    for j in _cron_manager.list_jobs():
        if target in j.name:
            _cron_manager.remove_job(j.job_id)
            return f"🗑️ 已删除「{j.name}」"
    return f"❌ 未找到「{target}」"


def _do_cron_toggle(info: dict) -> str:
    global _cron_manager
    target = info.get("target_job_name", "")
    enable = "启用" in str(info)
    if not target:
        return "❌ 请指定任务名称"
    for j in _cron_manager.list_jobs():
        if target in j.name:
            _cron_manager.toggle_job(j.job_id, enable)
            a = "▶️ 已启用" if enable else "⏸️ 已禁用"
            return f"{a}「{j.name}」"
    return f"❌ 未找到「{target}」"


def _handle_delete_all(info: dict) -> tuple:
    """处理删除全部定时任务（含确认流程）。"""
    global _cron_manager, _pending_confirm
    if not _cron_manager:
        return "❌ 定时任务功能未启用", None
    jobs = _cron_manager.list_jobs()
    if not jobs:
        return "⏰ 当前没有定时任务可删除", None
    if info.get("confirmed"):
        count = len(jobs)
        for j in list(jobs):
            _cron_manager.remove_job(j.job_id)
        return f"🗑️ 已删除全部 {count} 个定时任务", None
    _pending_confirm["default"] = {"action": "delete_all", "expires": time.time() + 120}
    return (
        f"⚠️ **确定要删除全部 {len(jobs)} 个定时任务吗？**\n\n"
        "回复「确认」执行删除，回复「取消」放弃操作。"
    ), "delete_all"


@app.post("/api/chat/confirm")
async def confirm_action(request: ChatRequest):
    """确认/取消危险操作。"""
    global _pending_confirm, _cron_manager
    text = request.message.strip()
    pending = _pending_confirm.pop("default", None)
    if not pending or time.time() > pending.get("expires", 0):
        return {"reply": "⏰ 没有待确认的操作（可能已过期）", "confirm_needed": None}
    if pending["action"] == "delete_all":
        if text in ("确认", "是", "yes", "确定", "confirm", "ok", "好", "是的", "确认删除"):
            jobs = _cron_manager.list_jobs()
            count = len(jobs)
            for j in list(jobs):
                _cron_manager.remove_job(j.job_id)
            return {"reply": f"🗑️ 已删除全部 {count} 个定时任务", "confirm_needed": None, "cron_jobs": _cron_jobs_list()}
        else:
            return {"reply": "✅ 已取消删除操作", "confirm_needed": None}
    return {"reply": "未知操作", "confirm_needed": None}


@app.get("/api/map")
async def get_map():
    """Get the current warehouse map as JSON."""
    try:
        mp = str(DEFAULT_MAP_PATH)
        with open(mp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Map file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid map JSON: {e}")


@app.put("/api/map")
async def update_map(data: Dict[str, Any]):
    """Update the warehouse map and reload the workflow."""
    try:
        # Validate: must have minimal structure
        if "map" not in data:
            raise HTTPException(status_code=400, detail="Map data must contain 'map' key")
        mp = str(DEFAULT_MAP_PATH)
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        reset_workflow()
        return {"status": "ok", "message": "Map updated and workflow reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/runtime")
async def get_runtime():
    """Get the current runtime state (robot positions, blockages)."""
    try:
        rp = str(DEFAULT_RUNTIME_PATH)
        with open(rp, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Runtime file not found")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Invalid runtime JSON: {e}")


@app.put("/api/runtime")
async def update_runtime(data: Dict[str, Any]):
    """Update the runtime state and reload the workflow."""
    try:
        if "robots" not in data:
            raise HTTPException(status_code=400, detail="Runtime data must contain 'robots' key")
        rp = str(DEFAULT_RUNTIME_PATH)
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        reset_workflow()
        return {"status": "ok", "message": "Runtime updated and workflow reloaded"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cron job management ──────────────────────────────────────────────────


@app.get("/api/cron", response_model=List[CronJobOutput])
async def list_cron_jobs():
    """列出所有定时任务。"""
    global _cron_manager
    if not _cron_manager:
        return []
    jobs = _cron_manager.list_jobs()
    return [_job_to_output(j) for j in jobs]


@app.post("/api/cron", response_model=CronJobOutput)
async def create_cron_job(data: CronJobInput):
    """创建定时任务。"""
    global _cron_manager
    if not _cron_manager:
        raise HTTPException(status_code=500, detail="Cron manager not initialized")
    job = _cron_manager.add_job(data.name, data.cron_expr, data.instruction)
    return _job_to_output(job)


@app.delete("/api/cron/{job_id}")
async def delete_cron_job(job_id: str):
    """删除定时任务。"""
    global _cron_manager
    if not _cron_manager:
        raise HTTPException(status_code=500, detail="Cron manager not initialized")
    ok = _cron_manager.remove_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "ok"}


@app.put("/api/cron/{job_id}")
async def toggle_cron_job(job_id: str, data: CronJobToggle):
    """启用/禁用定时任务。"""
    global _cron_manager
    if not _cron_manager:
        raise HTTPException(status_code=500, detail="Cron manager not initialized")
    job = _cron_manager.toggle_job(job_id, data.enabled)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_output(job)


def _job_to_output(job) -> CronJobOutput:
    """CronJob → CronJobOutput。"""
    return CronJobOutput(
        job_id=job.job_id,
        name=job.name,
        cron_expr=job.cron_expr,
        instruction=job.instruction,
        enabled=job.enabled,
        created_at=job.created_at,
        last_run_at=job.last_run_at,
        last_result=job.last_result,
    )


# ── Static file serving (React frontend) ────────────────────────────────────

if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve React frontend for all non-API routes."""
        # API routes are already matched above
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    # Also serve root
    @app.get("/")
    async def serve_root():
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    @app.get("/")
    async def no_frontend():
        return {
            "message": "Warehouse Scheduler API is running",
            "docs": "/docs",
            "note": "Frontend not built. Run: cd frontend && npm run build",
        }


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    """Run the server with uvicorn."""
    import uvicorn
    uvicorn.run(
        "app.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
