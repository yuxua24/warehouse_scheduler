"""LangGraph node functions for the warehouse scheduling workflow.

Each node receives the full GraphState and returns a partial dict of
fields to update (merged via the reducer rules defined in GraphState).

Nodes are pure functions: they do NOT hold mutable state between invocations.
All context (map, obstacles, metrics, etc.) flows through the GraphState dict.
"""

import time
import uuid
from typing import Dict, Set, Tuple, List, Optional

from app.domain.graph_state import GraphState
from app.domain.task_models import TaskBatch, RobotTask
from app.domain.path_models import PathPlanResult, TimedPosition, FailureReason
from app.domain.conflict_models import Conflict
from app.domain.planning_state import (
    BatchStatus,
    ReplanDecision,
    SingleTaskResult,
    PlanningMetrics,
)
from app.domain.runtime_models import DynamicBlockage
from app.domain.map_models import WarehouseMap
from app.tools.astar_planner import AStarPlanner
from app.tools.reservation_table import ReservationTable
from app.tools.conflict_detector import ConflictDetector
from app.tools.path_validator import PathValidator
from app.services.location_resolver import LocationResolver
from app.services.metrics_collector import MetricsCollector


# ═══════════════════════════════════════════════════════════════════════════════
# Node 1: Parse natural language instruction  →  task_batch
# ═══════════════════════════════════════════════════════════════════════════════

def parse_instruction(state: GraphState) -> dict:
    """Parse the natural language instruction into a structured TaskBatch.

    Requires in state:
      - original_instruction
      - warehouse_map (WarehouseMap)
    Also needs a RobotRegistry — but we thread the registry through the
    graph plumbing; for now the registry is expected to be attached to the
    TaskParserAgent which is injected as a callable.

    NOTE: This node requires a pre-configured TaskParserAgent callable stored
    in the graph's bound kwargs or passed via a partial.  We handle this by
    making the node a factory: the actual callable is `make_parse_instruction(parser)`.
    """
    # This stub is replaced by the factory below.
    raise NotImplementedError("use make_parse_instruction(parser)")


def make_parse_instruction(parser):
    """Factory: returns a parse_instruction node bound to a TaskParserAgent."""
    def _node(state: GraphState) -> dict:
        instruction = state.get("original_instruction", "")
        t0 = time.time()
        task_batch = parser.parse(instruction)
        elapsed = (time.time() - t0) * 1000

        updates: dict = {
            "task_batch": task_batch,
            "status": BatchStatus.PARSED,
        }

        if task_batch.parse_errors:
            updates["errors"] = list(task_batch.parse_errors)
        if task_batch.parse_warnings:
            updates["warnings"] = list(task_batch.parse_warnings)
        if not task_batch.is_valid:
            updates["status"] = BatchStatus.INFEASIBLE
            updates["failure_reason"] = "Parsing failed: " + "; ".join(
                task_batch.parse_errors
            )

        return updates

    return _node


# ═══════════════════════════════════════════════════════════════════════════════
# Node 2: Validate tasks & resolve goal entry cells
# ═══════════════════════════════════════════════════════════════════════════════

def validate_and_resolve_goals(state: GraphState) -> dict:
    """Validate the TaskBatch and resolve each task's goal to a specific entry cell.

    Requires in state:
      - task_batch
      - warehouse_map
    """
    task_batch: TaskBatch = state.get("task_batch")
    wmap: WarehouseMap = state.get("warehouse_map")

    if task_batch is None:
        return {
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": "No task batch available",
            "errors": ["No task batch available"],
        }

    resolver = LocationResolver(wmap)
    errors: list[str] = []
    occupied_goals: Set[Tuple[int, int]] = set()

    for task in task_batch.tasks:
        goal = resolver.select_best_goal(
            task.goal_location_id,
            task.start,
            occupied_goals,
        )
        if goal is None:
            errors.append(
                f"Robot {task.robot_id}: no free entry for {task.goal_location_id}"
            )
        else:
            task.selected_goal = goal
            occupied_goals.add(goal)

    if errors:
        return {
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": "Some tasks have no valid goal",
            "errors": errors,
        }

    # Sort by priority, establish priority order
    task_batch.tasks.sort(key=lambda t: t.priority)
    priority_order = [t.robot_id for t in task_batch.tasks]

    return {
        "task_batch": task_batch,
        "priority_order": priority_order,
        "status": BatchStatus.VALIDATED,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 3: Build obstacle sets from map + blockages
# ═══════════════════════════════════════════════════════════════════════════════

def build_obstacles(state: GraphState) -> dict:
    """Derive static_obstacles and dynamic_obstacles from the map and blockages.

    Requires in state:
      - warehouse_map
      - blockages (list of DynamicBlockage)
      - max_timestep
    """
    wmap: WarehouseMap = state.get("warehouse_map")
    blockages: list = state.get("blockages", [])
    max_t = state.get("max_timestep", 200)

    # Static obstacles (障碍物 + 设施本体格均不可通行)
    static_obs: Set[Tuple[int, int]] = set()
    for so in wmap.static_obstacles:
        static_obs.update(tuple(c) for c in so.cells)
    for loc in wmap.locations:
        static_obs.update(tuple(c) for c in loc.facility_cells)

    # Dynamic obstacles from blockages
    dynamic_obs: Set[Tuple[int, int, int]] = set()
    for b in blockages:
        cells = b.cells or []
        for t in range(max_t + 1):
            if b.is_active_at(t):
                for cx, cy in cells:
                    dynamic_obs.add((cx, cy, t))

    # Also include user corridor closure constraints from task_batch
    task_batch: TaskBatch = state.get("task_batch")
    if task_batch is not None:
        for constraint in task_batch.runtime_constraints:
            if constraint.get("constraint_type") == "closed_corridor":
                target_id = constraint.get("target_id", "")
                corridor = wmap.find_corridor(target_id)
                if corridor:
                    for t in range(max_t + 1):
                        for cx, cy in corridor.cells:
                            dynamic_obs.add((cx, cy, t))

    return {
        "static_obstacles": static_obs,
        "dynamic_obstacles": dynamic_obs,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 4: Initial independent planning (no inter-robot reservations)
# ═══════════════════════════════════════════════════════════════════════════════

def initial_plan(state: GraphState) -> dict:
    """Run A* independently for each robot (no mutual reservations).

    Requires in state:
      - task_batch
      - warehouse_map
      - static_obstacles
      - dynamic_obstacles
      - max_timestep
    """
    task_batch: TaskBatch = state.get("task_batch")
    wmap: WarehouseMap = state.get("warehouse_map")
    static_obs = state.get("static_obstacles", set())
    dynamic_obs = state.get("dynamic_obstacles", set())
    max_t = state.get("max_timestep", 200)

    results: Dict[str, PathPlanResult] = {}
    errors: list[str] = []

    for task in task_batch.tasks:
        planner = AStarPlanner(
            width=wmap.width,
            height=wmap.height,
            static_obstacles=static_obs,
            max_timestep=max_t,
            move_cost=wmap.movement.move_cost,
            wait_cost=wmap.movement.wait_cost,
        )
        planner.set_dynamic_obstacles(dynamic_obs)
        result = planner.plan(
            start=task.start,
            goal=task.selected_goal,
            robot_id=task.robot_id,
        )
        results[task.robot_id] = result

        if not result.success:
            errors.append(
                f"Robot {task.robot_id}: initial planning failed "
                f"({result.failure_reason.value})"
            )

    # If all failed, fail fast
    if errors and len(errors) == len(results):
        return {
            "initial_paths": results,
            "current_paths": dict(results),
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": "All tasks failed initial planning",
            "errors": errors,
        }

    return {
        "initial_paths": results,
        "current_paths": dict(results),
        "status": BatchStatus.INITIAL_PLANNED,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 5: Conflict detection
# ═══════════════════════════════════════════════════════════════════════════════

def conflict_check(state: GraphState) -> dict:
    """Run ConflictDetector on current_paths.

    Requires in state:
      - current_paths
      - initial_conflicts (may be unset on first call)
    """
    current_paths: Dict[str, PathPlanResult] = state.get("current_paths", {})
    detector = ConflictDetector()
    conflicts = detector.detect(current_paths)

    # On first call, also set initial_conflicts
    existing_initial = state.get("initial_conflicts")
    if existing_initial is None or len(existing_initial) == 0:
        return {
            "initial_conflicts": list(conflicts),
            "current_conflicts": list(conflicts),
            "status": BatchStatus.CONFLICT_CHECKED,
        }

    return {
        "current_conflicts": list(conflicts),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Node 6: Replanning decision (diagnose conflicts → ReplanDecision)
# ═══════════════════════════════════════════════════════════════════════════════

def make_replan_decide(replanning_agent):
    """Factory: returns a replan_decide node bound to a ReplanningAgent."""
    def _node(state: GraphState) -> dict:
        current_conflicts = state.get("current_conflicts", [])
        current_paths = state.get("current_paths", {})
        priority_order = state.get("priority_order", [])
        retry_count = state.get("retry_count", 0)

        decision = replanning_agent.decide(
            current_conflicts,
            current_paths,
            priority_order,
            retry_count,
        )
        return {
            "replan_history": [decision],
        }
    return _node


# ═══════════════════════════════════════════════════════════════════════════════
# Node 7: Apply replan decision (update paths via ReplanningPolicy)
# ═══════════════════════════════════════════════════════════════════════════════

def make_apply_replan(replanning_policy):
    """Factory: returns an apply_replan node bound to a ReplanningPolicy."""
    def _node(state: GraphState) -> dict:
        # Get the latest replan decision
        replan_history = state.get("replan_history", [])
        if not replan_history:
            return {"errors": ["apply_replan called with no replan decision"]}

        decision = replan_history[-1]
        wmap: WarehouseMap = state.get("warehouse_map")
        static_obs = state.get("static_obstacles", set())
        dynamic_obs = state.get("dynamic_obstacles", set())
        max_t = state.get("max_timestep", 200)
        current_paths = dict(state.get("current_paths", {}))
        priority_order = list(state.get("priority_order", []))
        task_batch: TaskBatch = state.get("task_batch")
        retry_count = state.get("retry_count", 0)

        # Apply the decision (inline logic — no dependency on PlanningState)
        t0 = time.time()
        replan_set = set(decision.robot_to_replan)

        # Build reservation table from non-replanned robots
        reservation = ReservationTable()
        for rid, rp in current_paths.items():
            if rid not in replan_set and rp.success:
                reservation.reserve_path(rp.path, rid, max_t)

        # Apply priority changes
        if decision.priority_changes:
            for rid, new_prio in decision.priority_changes.items():
                for task in task_batch.tasks:
                    if task.robot_id == rid:
                        task.priority = new_prio
            task_batch.tasks.sort(key=lambda t: t.priority)
            priority_order = [t.robot_id for t in task_batch.tasks]

        # Build combined dynamic obstacles with transition constraints
        combined_dynamic = set(dynamic_obs)
        for constraint in decision.constraints:
            if constraint.get("type") == "vertex_window":
                px, py = constraint["position"]
                for t in range(
                    constraint.get("time_start", 0),
                    constraint.get("time_end", 0) + 1,
                ):
                    combined_dynamic.add((px, py, t))

        # Replan affected robots in priority order
        replan_order = [rid for rid in priority_order if rid in replan_set]
        for rid in replan_order:
            task = next(
                (t for t in task_batch.tasks if t.robot_id == rid), None
            )
            if task is None:
                continue

            planner = AStarPlanner(
                width=wmap.width,
                height=wmap.height,
                static_obstacles=static_obs,
                max_timestep=max_t,
                move_cost=wmap.movement.move_cost,
                wait_cost=wmap.movement.wait_cost,
            )
            planner.set_dynamic_obstacles(combined_dynamic)
            planner.set_reservation_table(reservation)

            result = planner.plan(
                start=task.start,
                goal=task.selected_goal,
                robot_id=rid,
            )
            current_paths[rid] = result
            if result.success:
                reservation.reserve_path(result.path, rid, max_t)

        return {
            "current_paths": current_paths,
            "task_batch": task_batch,
            "priority_order": priority_order,
            "retry_count": retry_count + 1,
        }
    return _node


# ═══════════════════════════════════════════════════════════════════════════════
# Node 8: Partial execution (search for a feasible subset)
# ═══════════════════════════════════════════════════════════════════════════════

def partial_execution(state: GraphState) -> dict:
    """Search for a feasible subset of tasks when full planning fails.

    Requires in state:
      - task_batch
      - current_paths
      - current_conflicts
      - warehouse_map, static_obstacles, dynamic_obstacles, max_timestep
    """
    task_batch: TaskBatch = state.get("task_batch")
    wmap: WarehouseMap = state.get("warehouse_map")
    static_obs = state.get("static_obstacles", set())
    dynamic_obs = state.get("dynamic_obstacles", set())
    max_t = state.get("max_timestep", 200)
    current_paths = state.get("current_paths", {})
    current_conflicts = state.get("current_conflicts", [])

    all_tasks = task_batch.tasks
    n = len(all_tasks)
    if n <= 1:
        return {
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": "Only 1 task but still infeasible",
        }

    # Identify failed/conflicting robots
    failed_robots: set = set()
    for rid, rp in current_paths.items():
        if not rp.success:
            failed_robots.add(rid)
    for c in current_conflicts:
        failed_robots.update(c.robot_ids)

    # Sort by priority (higher number = lower priority) and try removing
    sorted_tasks = sorted(all_tasks, key=lambda t: -t.priority)
    removed_robots: set = set()

    for task in sorted_tasks:
        if task.robot_id not in failed_robots:
            continue
        removed_robots.add(task.robot_id)

        subset_tasks = [t for t in all_tasks if t.robot_id not in removed_robots]
        if len(subset_tasks) < 1:
            break

        # Keep removed robots occupying their start positions
        occupied_starts = {
            t.robot_id: t.start
            for t in all_tasks
            if t.robot_id in removed_robots
        }

        result = _plan_subset(
            subset_tasks,
            static_obs,
            dynamic_obs,
            occupied_starts,
            wmap,
            max_t,
        )
        if result is not None:
            removed_list = list(removed_robots)
            return {
                "current_paths": result,
                "warnings": [
                    f"Partial execution: removed robots {removed_list}"
                ],
                "status": BatchStatus.PARTIALLY_SUCCEEDED,
            }

    return {
        "status": BatchStatus.INFEASIBLE,
        "failure_reason": "No feasible subset found",
    }


def _plan_subset(
    subset_tasks: List[RobotTask],
    static_obs: Set[Tuple[int, int]],
    dynamic_obs: Set[Tuple[int, int, int]],
    occupied_starts: Dict[str, Tuple[int, int]],
    wmap: WarehouseMap,
    max_t: int,
) -> Optional[Dict[str, PathPlanResult]]:
    """Internal: plan a subset with occupied starts as obstacles. Returns None if infeasible."""
    combined_dynamic = set(dynamic_obs)
    for pos in occupied_starts.values():
        for t in range(max_t + 1):
            combined_dynamic.add((pos[0], pos[1], t))

    sorted_tasks = sorted(subset_tasks, key=lambda t: t.priority)
    reservation = ReservationTable()
    results: Dict[str, PathPlanResult] = {}

    for task in sorted_tasks:
        planner = AStarPlanner(
            width=wmap.width,
            height=wmap.height,
            static_obstacles=static_obs,
            max_timestep=max_t,
            move_cost=wmap.movement.move_cost,
            wait_cost=wmap.movement.wait_cost,
        )
        planner.set_dynamic_obstacles(combined_dynamic)
        planner.set_reservation_table(reservation)

        result = planner.plan(
            start=task.start,
            goal=task.selected_goal,
            robot_id=task.robot_id,
        )
        results[task.robot_id] = result
        if result.success:
            reservation.reserve_path(result.path, task.robot_id, max_t)
        else:
            return None

    # Check residual conflicts
    detector = ConflictDetector()
    if detector.detect(results):
        return None
    for rid, rp in results.items():
        if not rp.success:
            continue
        for node in rp.path:
            if (node.x, node.y, node.time) in combined_dynamic:
                return None

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Node 9: Final path validation
# ═══════════════════════════════════════════════════════════════════════════════

def validate_final(state: GraphState) -> dict:
    """Run PathValidator on the final current_paths.

    Requires in state:
      - current_paths
      - warehouse_map
      - status (may be SUCCEEDED or PARTIALLY_SUCCEEDED)
    """
    current_paths = state.get("current_paths", {})
    wmap: WarehouseMap = state.get("warehouse_map")
    current_status = state.get("status")

    validator = PathValidator(wmap)
    validation_errors = validator.validate_multi_robot(current_paths)

    if validation_errors:
        return {
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": "Final validation failed: " + "; ".join(validation_errors),
            "errors": validation_errors,
        }

    # If already PARTIALLY_SUCCEEDED, keep it; otherwise SUCCEEDED
    if current_status == BatchStatus.PARTIALLY_SUCCEEDED:
        return {"status": BatchStatus.PARTIALLY_SUCCEEDED}

    # Check individual path failures
    failed = [rid for rid, rp in current_paths.items() if not rp.success]
    if failed and len(failed) < len(current_paths):
        return {
            "status": BatchStatus.PARTIALLY_SUCCEEDED,
            "failure_reason": f"Some tasks failed: {failed}",
        }
    elif failed:
        return {
            "status": BatchStatus.INFEASIBLE,
            "failure_reason": f"All tasks failed: {failed}",
        }

    return {"status": BatchStatus.SUCCEEDED}


# ═══════════════════════════════════════════════════════════════════════════════
# Node 10: Compute final metrics and per-task results
# ═══════════════════════════════════════════════════════════════════════════════

def compute_metrics(state: GraphState) -> dict:
    """Build PlanningMetrics and SingleTaskResult list.

    Requires in state:
      - task_batch
      - current_paths
      - retry_count
      - replan_history
      - initial_conflicts
      - current_conflicts
    """
    task_batch: TaskBatch = state.get("task_batch")
    current_paths = state.get("current_paths", {})
    replan_history = state.get("replan_history", [])
    initial_conflicts = state.get("initial_conflicts", [])
    current_conflicts = state.get("current_conflicts", [])
    retry_count = state.get("retry_count", 0)

    successful = sum(1 for rp in current_paths.values() if rp.success)
    total = task_batch.task_count if task_batch else len(current_paths)

    # Tally A* expanded nodes from all path results
    total_expanded_nodes = 0
    astar_call_count = 0
    for rp in state.get("initial_paths", {}).values():
        astar_call_count += 1
        total_expanded_nodes += rp.expanded_nodes
    # Also count replanning A* calls (those beyond initial)
    # Replanning adds extra A* calls; approximate from retry count
    if retry_count > 0:
        # Each retry replans at least one robot
        for dec in replan_history:
            astar_call_count += len(dec.robot_to_replan)

    # Determine which robots were replanned
    replanned_robots = set()
    for dec in replan_history:
        replanned_robots.update(dec.robot_to_replan)

    task_results: list[SingleTaskResult] = []
    for task in (task_batch.tasks if task_batch else []):
        rp = current_paths.get(task.robot_id)
        task_results.append(
            SingleTaskResult(
                robot_id=task.robot_id,
                task=task,
                path=rp.path if rp and rp.success else [],
                success=rp.success if rp else False,
                failure_reason=(
                    rp.failure_reason.value
                    if rp and rp.failure_reason
                    else None
                ),
                replanned=task.robot_id in replanned_robots,
            )
        )

    return {
        "task_results": task_results,
        "metrics": PlanningMetrics(
            total_task_count=total,
            planned_task_count=successful,
            planning_failed_task_count=total - successful,
            planning_success_rate=successful / max(total, 1),
            initial_conflict_count=len(initial_conflicts),
            final_conflict_count=len(current_conflicts),
            replanning_triggered=retry_count > 0,
            retry_count=retry_count,
            astar_call_count=astar_call_count,
            total_expanded_nodes=total_expanded_nodes,
        ),
    }
