"""FastAPI server for the Warehouse Robot Scheduling System.

Provides REST API for scheduling, map management, and runtime state.
Serves the React frontend in production.
"""

import json
import os
import sys
import uuid
import time
import copy
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
from app.api.schemas import (
    SchedulingRequest,
    SchedulingResponse,
    TaskResultOutput,
    MetricsOutput,
    ReplanHistoryOutput,
    TimedPositionOutput,
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
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# ── Workflow cache ───────────────────────────────────────────────────────────

_workflow: Optional[Workflow] = None


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
