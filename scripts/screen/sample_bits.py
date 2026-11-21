"""Sample mesh signals using their RTL roles, tile coordinates, and bit lanes."""

import hashlib
import heapq
import math
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtl.insert_off_switches import ARRAY


def random_key(seed, value):
    return hashlib.sha256(f"{seed}\0{value}".encode()).digest()


BIT = re.compile(r"\[(\d+)\]$")
PE = re.compile(r"mesh_(\d+)_(\d+)\.tile_\d+_\d+\.(.+)")
TILE_OUTPUT = re.compile(r"_mesh_(\d+)_(\d+)_(io_out_.+)")
TILE_CONTROL = re.compile(r"mesh_(\d+)_(\d+)_io_in_control_\d+_(.+)")
MESH_INPUT = re.compile(r"(io_in_.+?)_(\d+)_\d+(.*)")
GENERATED = re.compile(r"(pipe_b|r)_\d+(?:_\d+)?(.*)")


def mesh_location(path):
    """Return the signal role, mesh row, and mesh column for a word path."""
    name = path.removeprefix(ARRAY)
    for scope, pattern in (("pe", PE), ("tile output", TILE_OUTPUT), ("tile control", TILE_CONTROL)):
        match = pattern.fullmatch(name)
        if match:
            return (scope, match[3]), int(match[1]), int(match[2])
    match = MESH_INPUT.fullmatch(name)
    if match:
        role = ("mesh input", match[1] + match[3])
        index = int(match[2])
        return (role, index, None) if match[1] == "io_in_a" else (role, None, index)
    match = GENERATED.fullmatch(name)
    if match:
        # The generated register suffixes are counters, not mesh coordinates.
        return ("mesh register", match[1] + match[2]), None, None
    return ("mesh", name), None, None


def sample_mesh_bits(eligible, count, seed):
    """Return a seeded prefix of eligible bit rows, grouped by RTL role and geometry.

    Eligible rows carry unique canonical paths. Vector lanes end in `[bit]`.
    The original rows are returned without edits.
    """
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    paths = set()
    for candidate in eligible:
        path = candidate["path"]
        if path in paths:
            raise ValueError(f"Duplicate mesh bit path: {path}")
        paths.add(path)
        match = BIT.search(path)
        word_path = path[:match.start()] if match else path
        lane = int(match[1]) if match else None
        role, row, column = mesh_location(word_path)
        groups[role, lane is not None][row, column][lane].append(
            (random_key(seed, path), path, candidate)
        )

    if not 1 <= count <= len(paths):
        raise ValueError(f"Sample size must be between 1 and {len(paths)}")

    populations = Counter()
    for role, coordinates in groups.items():
        for lanes in coordinates.values():
            for lane, candidates in lanes.items():
                populations[role] += len(candidates)
                lanes[lane] = deque(sorted(candidates))

    coordinate_keys = {(role, position): random_key(seed, repr((role, position)))
                       for role, coordinates in groups.items() for position in coordinates}
    selected = []
    row_counts = Counter()
    column_counts = Counter()
    tile_counts = Counter()
    role_tile_counts = Counter()
    role_lane_counts = Counter()
    role_counts = Counter()
    # Place roles with fewer available locations first so flexible roles can
    # use the remaining rows and columns.
    queue = [(0, len(coordinates), random_key(seed, repr(role)), role)
             for role, coordinates in groups.items()]
    heapq.heapify(queue)

    def coordinate_priority(role, position):
        row, column = position
        row_count = row_counts[row]
        column_count = column_counts[column]
        uncovered_axes = int(row is not None and not row_count)
        uncovered_axes += int(column is not None and not column_count)
        return (
            -uncovered_axes,
            row_count + column_count,
            tile_counts[position],
            role_tile_counts[role, position],
            coordinate_keys[role, position],
        )

    while len(selected) < count:
        _, _, role_key, role = heapq.heappop(queue)
        coordinates = groups[role]
        position = min(coordinates, key=lambda position: coordinate_priority(role, position))
        lanes = coordinates[position]
        lane = min(lanes, key=lambda bit: (role_lane_counts[role, bit], lanes[bit][0][:2]))
        selected.append(lanes[lane].popleft()[2])
        if not lanes[lane]:
            del lanes[lane]
        if not lanes:
            del coordinates[position]
        row, column = position
        if row is not None:
            row_counts[row] += 1
        if column is not None:
            column_counts[column] += 1
        tile_counts[position] += 1
        role_tile_counts[role, position] += 1
        role_lane_counts[role, lane] += 1
        role_counts[role] += 1

        # Give every role one seat, then scale its allocation by the square
        # root of its population so densely replicated roles do not dominate.
        if coordinates:
            priority = (role_counts[role] + 1) / math.sqrt(populations[role])
            heapq.heappush(queue, (priority, 0, role_key, role))
    return selected
