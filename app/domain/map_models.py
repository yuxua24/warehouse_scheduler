"""Domain models for warehouse map and related entities."""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict


@dataclass
class CoordinateSystem:
    format: str = "[x, y]"
    origin: str = "top_left"
    x_direction: str = "right"
    y_direction: str = "down"


@dataclass
class MovementRules:
    allow_up: bool = True
    allow_down: bool = True
    allow_left: bool = True
    allow_right: bool = True
    allow_wait: bool = True
    allow_diagonal: bool = False
    move_cost: float = 1.0
    wait_cost: float = 1.0


@dataclass
class Location:
    location_id: str
    name: str
    aliases: List[str] = field(default_factory=list)
    type: str = ""
    facility_cells: List[Tuple[int, int]] = field(default_factory=list)
    entry_cells: List[Tuple[int, int]] = field(default_factory=list)
    capacity: int = 1


@dataclass
class StaticObstacle:
    obstacle_id: str
    type: str = ""
    cells: List[Tuple[int, int]] = field(default_factory=list)


@dataclass
class Corridor:
    corridor_id: str
    name: str
    cells: List[Tuple[int, int]] = field(default_factory=list)
    direction: Optional[str] = None
    capacity: Optional[int] = None


@dataclass
class WarehouseMap:
    map_id: str
    name: str
    width: int
    height: int
    coordinate_system: CoordinateSystem = field(default_factory=CoordinateSystem)
    movement: MovementRules = field(default_factory=MovementRules)
    static_obstacles: List[StaticObstacle] = field(default_factory=list)
    locations: List[Location] = field(default_factory=list)
    corridors: List[Corridor] = field(default_factory=list)

    _obstacle_set: Optional[set] = field(default=None, repr=False)
    _location_by_id: Optional[Dict[str, Location]] = field(default=None, repr=False)
    _location_by_alias: Optional[Dict[str, Location]] = field(default=None, repr=False)
    _corridor_by_id: Optional[Dict[str, Corridor]] = field(default=None, repr=False)
    _corridor_by_name: Optional[Dict[str, Corridor]] = field(default=None, repr=False)

    def build_indices(self):
        """Build fast lookup indices after loading."""
        self._obstacle_set = set()
        for obs in self.static_obstacles:
            self._obstacle_set.update(tuple(c) for c in obs.cells)
        # 设施本体格也视为不可通行
        for loc in self.locations:
            self._obstacle_set.update(tuple(c) for c in loc.facility_cells)

        self._location_by_id = {}
        self._location_by_alias = {}
        for loc in self.locations:
            self._location_by_id[loc.location_id] = loc
            for alias in loc.aliases:
                self._location_by_alias[alias.lower()] = loc

        self._corridor_by_id = {}
        self._corridor_by_name = {}
        for corr in self.corridors:
            self._corridor_by_id[corr.corridor_id] = corr
            self._corridor_by_name[corr.name] = corr

    def is_obstacle(self, x: int, y: int) -> bool:
        if self._obstacle_set is None:
            self.build_indices()
        return (x, y) in self._obstacle_set

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_walkable(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and not self.is_obstacle(x, y)

    def find_location(self, identifier: str) -> Optional[Location]:
        if self._location_by_id is None:
            self.build_indices()
        key = identifier.lower()
        return self._location_by_id.get(identifier) or self._location_by_alias.get(key)

    def find_corridor(self, identifier: str) -> Optional[Corridor]:
        if self._corridor_by_id is None:
            self.build_indices()
        key = identifier.lower()
        return self._corridor_by_id.get(identifier) or self._corridor_by_name.get(key)
