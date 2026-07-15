"""API schemas for structured input/output."""

from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field


class TaskInput(BaseModel):
    robot_id: str
    start: List[int]
    goal_location_id: str
    priority: int = 1


class ConstraintInput(BaseModel):
    constraint_type: str
    target_id: Optional[str] = None
    target_type: Optional[str] = None
    cells: Optional[List[List[int]]] = None
    start_time: int = 0
    end_time: Optional[int] = None
    reason: str = ""


class SchedulingRequest(BaseModel):
    instruction: Optional[str] = None
    tasks: Optional[List[TaskInput]] = None
    constraints: Optional[List[ConstraintInput]] = None
    max_timestep: int = 200
    map_path: Optional[str] = None
    runtime_path: Optional[str] = None


class TimedPositionOutput(BaseModel):
    x: int
    y: int
    time: int


class TaskResultOutput(BaseModel):
    robot_id: str
    success: bool
    path: List[TimedPositionOutput] = Field(default_factory=list)
    makespan: int = 0
    failure_reason: Optional[str] = None
    replanned: bool = False
    start: Optional[List[int]] = None
    goal: Optional[List[int]] = None
    goal_location_id: Optional[str] = None


class MetricsOutput(BaseModel):
    total_task_count: int
    planned_task_count: int
    planning_failed_task_count: int
    planning_success_rate: float
    total_planning_time_ms: float
    parsing_time_ms: float = 0.0
    initial_planning_time_ms: float = 0.0
    replanning_time_ms: float = 0.0
    average_planning_time_per_task_ms: float = 0.0
    initial_conflict_count: int = 0
    final_conflict_count: int = 0
    replanning_triggered: bool = False
    retry_count: int = 0
    astar_call_count: int = 0
    total_expanded_nodes: int = 0


class ReplanHistoryOutput(BaseModel):
    retry_index: int
    action: str
    affected_robots: List[str]
    robot_to_replan: List[str]
    explanation: str


class SchedulingResponse(BaseModel):
    request_id: str
    batch_status: str
    tasks: List[TaskResultOutput] = Field(default_factory=list)
    metrics: Optional[MetricsOutput] = None
    replan_history: List[ReplanHistoryOutput] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    original_instruction: str = ""


# ── Cron job schemas ──────────────────────────────────────────────────────


class CronJobInput(BaseModel):
    """创建定时任务的请求体。"""
    name: str = Field(..., description="任务名称，如「每晚充电」")
    cron_expr: str = Field(..., description="Cron 表达式，如 0 22 * * *")
    instruction: str = Field(..., description="调度指令")


class CronJobOutput(BaseModel):
    """定时任务响应体。"""
    job_id: str
    name: str
    cron_expr: str
    instruction: str
    enabled: bool
    created_at: str = ""
    last_run_at: str = ""
    last_result: str = ""


class CronJobToggle(BaseModel):
    """启用/禁用请求体。"""
    enabled: bool
