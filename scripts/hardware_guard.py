#!/usr/bin/env python3
"""Run one hardware build per user with a kernel-enforced memory budget."""

import os
from pathlib import Path
import subprocess
import sys

MIB = 1024 * 1024
MAX_MIB = 6144
UNIT = "gemmini-hardware-build.scope"
CGROUP = Path("/sys/fs/cgroup")


def current_group():
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            return CGROUP / line[3:].lstrip("/")
    raise ValueError("Hardware builds require Linux cgroup v2")


def host_memory(group):
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    total, available = (int(fields[key].split()[0]) * 1024 for key in ("MemTotal", "MemAvailable"))
    # An enclosing user or job cgroup may have less headroom than the whole host.
    for parent in (group, *group.parents):
        limit = parent / "memory.max"
        if parent == CGROUP:
            break
        if limit.exists() and (value := limit.read_text().strip()) != "max":
            maximum = int(value)
            total = min(total, maximum)
            available = min(available, maximum - int((parent / "memory.current").read_text()))
    return total // MIB, available // MIB


def budget(total, available, requested):
    reserve = max(1024, total // 4)
    cap = min(requested, MAX_MIB, total // 4, (available - reserve) // 2)
    if cap < 64:
        raise ValueError(f"Insufficient RAM for a hardware build ({available} MiB available, "
                         f"{reserve} MiB reserved)")
    return cap, reserve


def inside():
    group = current_group()
    if group.name != UNIT:
        return False
    cap = (group / "memory.max").read_text().strip()
    if (cap == "max" or int(cap) > MAX_MIB * MIB or
            (group / "memory.swap.max").read_text().strip() != "0" or
            (group / "memory.oom.group").read_text().strip() != "1"):
        raise ValueError(f"Kernel memory limits are missing from {UNIT}")
    return True


def launch(command):
    requested = int(os.environ.get("GEMMINI_BUILD_MEMORY_MIB", MAX_MIB))
    if not 64 <= requested <= MAX_MIB:
        raise ValueError(f"GEMMINI_BUILD_MEMORY_MIB must be between 64 and {MAX_MIB}")
    cap, reserve = budget(*host_memory(current_group()), requested)
    print(f"Hardware build limit {cap} MiB, swap disabled, host reserve {reserve} MiB.",
          file=sys.stderr, flush=True)
    # A fixed unit name makes admission atomic across checkouts and terminals.
    os.execvp("systemd-run", [
        "systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit={UNIT}",
        f"--property=MemoryMax={cap}M",
        "--property=MemorySwapMax=0", "--property=OOMPolicy=kill", "--",
        sys.executable, str(Path(__file__).resolve()), "--supervise", str(reserve), *command,
    ])


def ensure_guarded():
    """Re-enter the Python entry point inside the verified build scope."""
    if not inside():
        launch([sys.executable, *sys.argv])


def supervise(reserve, command):
    if not inside():
        raise ValueError("A hardware build can start only inside its memory-limited scope")
    parent = current_group().parent
    if host_memory(parent)[1] < reserve:
        raise ValueError("Host memory fell below the reserve before the build started")
    process = subprocess.Popen(command)
    while True:
        try:
            rc = process.wait(timeout=1)
            return rc if rc >= 0 else 128 - rc
        except subprocess.TimeoutExpired:
            if host_memory(parent)[1] < reserve:
                print(f"Stopping hardware build to retain {reserve} MiB for the host.",
                      file=sys.stderr, flush=True)
                # Stop the whole scope, including ABC and other grandchildren.
                subprocess.run(["systemctl", "--user", "kill", "--signal=KILL", UNIT], check=True)
                return 137


def main():
    args = sys.argv[1:]
    if args == ["--active"]:
        return 0 if inside() else 1
    if args[:1] == ["--supervise"] and len(args) >= 3:
        return supervise(int(args[1]), args[2:])
    if not args or args[0] in {"-h", "--help"}:
        print(f"Usage: {Path(__file__).name} COMMAND [ARG ...]")
        return 0 if args else 2
    if inside():
        os.execvp(args[0], args)
    launch(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Hardware build stopped: {exc}")
