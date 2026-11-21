#!/usr/bin/env python3
"""Compile and run a simulation using the project's CAE VCS baseline."""

import re
import subprocess
import sys

BASELINE = "Y-2026.03_Full64"


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in {"compile", "run", "batch"}:
        raise SystemExit("usage: vcs.py {compile <VCS options>|run ./simv <plusargs>|batch <runner> <args>}")
    if sys.argv[1] == "compile":
        result = subprocess.run(["synopsys-run", "vcs", "-full64", "-ID"],
                                capture_output=True, text=True, check=True)
        version = re.search(r"Compiler version\s*=\s*VCS\s+(\S+)", result.stdout)
        if not version or version[1] != BASELINE:
            raise SystemExit(f"Expected VCS {BASELINE}. Version report:\n{result.stdout}")
        print(f"VCS {BASELINE}", flush=True)
        command = ["synopsys-run", "vcs", "-full64", "-sverilog", "-timescale=1ns/1ps",
                   *sys.argv[2:]]
    elif sys.argv[1] == "batch":
        # Start each worker group inside one licensed CAE container. Each case
        # still runs a fresh simv process with its own exit code and output.
        command = ["synopsys-run", "bash", *sys.argv[2:]]
    else:
        command = ["synopsys-run", sys.argv[2], "-exitstatus", *sys.argv[3:]]
    return subprocess.run(command).returncode


if __name__ == "__main__":
    raise SystemExit(main())
