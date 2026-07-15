"""机器人选择器：根据任务自动选择最优空闲机器人。"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _distance(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _load_robots(runtime_path: str) -> List[dict]:
    try:
        data = json.loads(Path(runtime_path).read_text(encoding="utf-8"))
        return data.get("robots", [])
    except Exception:
        return []


def _save_robots(runtime_path: str, robots: List[dict]) -> None:
    try:
        data = json.loads(Path(runtime_path).read_text(encoding="utf-8"))
        data["robots"] = robots
        Path(runtime_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def select_robot_for_task(source_pos: Tuple[int, int], runtime_path: str = "configs/warehouse_runtime.json") -> Optional[dict]:
    robots = _load_robots(runtime_path)
    idle = [r for r in robots if r.get("status", "idle") == "idle" and r.get("enabled", True)]
    if not idle: return None
    best = min(idle, key=lambda r: _distance(tuple(r["position"]), source_pos))
    return {"robot_id": best["robot_id"], "position": best["position"], "distance": _distance(tuple(best["position"]), source_pos)}


def get_idle_robots(runtime_path: str = "configs/warehouse_runtime.json") -> List[dict]:
    return [r for r in _load_robots(runtime_path) if r.get("status", "idle") == "idle" and r.get("enabled", True)]


def get_busy_robots(runtime_path: str = "configs/warehouse_runtime.json") -> List[dict]:
    return [r for r in _load_robots(runtime_path) if r.get("status") not in ("idle",)]


def get_waiting_robots(runtime_path: str = "configs/warehouse_runtime.json") -> List[dict]:
    return [r for r in _load_robots(runtime_path) if r.get("status") == "waiting"]


def mark_robot_busy(robot_id: str, task_info: dict, runtime_path: str = "configs/warehouse_runtime.json") -> bool:
    robots = _load_robots(runtime_path)
    for r in robots:
        if r["robot_id"] == robot_id:
            r["status"] = "busy"; r["assigned_task"] = task_info
            _save_robots(runtime_path, robots); return True
    return False


def mark_robot_idle(robot_id: str, runtime_path: str = "configs/warehouse_runtime.json") -> bool:
    robots = _load_robots(runtime_path)
    for r in robots:
        if r["robot_id"] == robot_id:
            r["status"] = "idle"; r["assigned_task"] = None
            _save_robots(runtime_path, robots); return True
    return False


def mark_robot_returning(robot_id: str, runtime_path: str = "configs/warehouse_runtime.json") -> Optional[dict]:
    robots = _load_robots(runtime_path)
    for r in robots:
        if r["robot_id"] == robot_id:
            r["status"] = "returning"; r["assigned_task"] = None
            _save_robots(runtime_path, robots); return r
    return None


def set_robot_waiting(robot_id: str, runtime_path: str = "configs/warehouse_runtime.json") -> bool:
    robots = _load_robots(runtime_path)
    for r in robots:
        if r["robot_id"] == robot_id:
            r["status"] = "waiting"
            _save_robots(runtime_path, robots); return True
    return False


def get_robot_by_delivery_target(target_location: str, runtime_path: str = "configs/warehouse_runtime.json") -> Optional[dict]:
    robots = _load_robots(runtime_path)
    for r in robots:
        task = r.get("assigned_task") or {}
        if task.get("target") == target_location and r.get("status") in ("busy", "waiting"):
            return r
    return None
