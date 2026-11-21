#!/usr/bin/env python3
"""Map an elaborated RTL design to a Liberty library and report cell area."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_guard import ensure_guarded

ABC_FAST = "strash; &get -n; &nf; &put"


def path_arg(path):
    return '"' + str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", required=True)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--rtl", type=Path, help="Split elaboration directory")
    sources.add_argument("--filelist", type=Path, help="Slang file list with file-relative paths")
    parser.add_argument(
        "--extra-filelist", type=Path, action="append", default=[],
        help="Add a Slang file list with file-relative paths",
    )
    parser.add_argument("--liberty", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--abc-fast", action="store_true", help="Map directly with ABC &nf")
    parser.add_argument(
        "--exclude-macro", action="append", default=[], metavar="MODULE=COUNT",
        help="Exclude the named macro from cell area and require its expected instance count",
    )
    args = parser.parse_args()
    ensure_guarded()
    identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    if not re.fullmatch(identifier, args.top):
        parser.error("--top must be a plain Verilog identifier")

    macros = {}
    for exclusion in args.exclude_macro:
        match = re.fullmatch(rf"({identifier})=([1-9][0-9]*)", exclusion)
        if not match or match[1] in macros:
            parser.error(f"invalid or duplicate macro exclusion: {exclusion}")
        macros[match[1]] = int(match[2])
    args.out.mkdir(parents=True, exist_ok=True)
    if args.rtl:
        sources = sorted(p for p in args.rtl.iterdir() if p.suffix in {".sv", ".v"})
        if not sources:
            parser.error(f"no RTL sources in {args.rtl}")
        source_list = list(map(path_arg, sources))
    else:
        source_list = [f"-F {path_arg(args.filelist)}"]
    source_list.extend(f"-F {path_arg(filelist)}" for filelist in args.extra_filelist)
    (args.out / "sources.f").write_text("\n".join(source_list) + "\n")
    liberty = path_arg(args.liberty)
    script = [
        f"read_slang --keep-hierarchy --ignore-assertions -DSYNTHESIS --top {args.top} -f sources.f",
        f"hierarchy -check -top {args.top}",
    ]
    for name in macros:
        # Slang appends the elaborated instance path to module names.
        selection = f"N:{name} N:{name}$*"
        script.extend([
            f"select -assert-any {selection}",
            f"blackbox {selection}",
        ])
    abc = "abc"
    if args.abc_fast:
        (args.out / "fast.abc").write_text(ABC_FAST + "\n")
        abc = "abc -script fast.abc"
    script.extend([
        f"synth -top {args.top} -flatten -noabc -noshare",
        "delete t:$scopeinfo",
        f"dfflibmap -liberty {liberty}",
        f"{abc} -liberty {liberty}",
        "clean",
        f"read_liberty -lib {liberty}",
        "check -assert -mapped",
        f"tee -o stat.json stat -json -top {args.top} -liberty {liberty}",
    ])
    for name, count in macros.items():
        script.append(f"select -assert-count {count} t:{name} t:{name}$*")
    (args.out / "synth.ys").write_text("\n".join(script) + "\n")

    for report in ("summary.json", "stat.json"):
        (args.out / report).unlink(missing_ok=True)
    with (args.out / "console.log").open("w") as console:
        result = subprocess.run(
            [args.yosys, "-m", "slang", "-Q", "-T", "-l", "synthesis.log", "-s", "synth.ys"],
            cwd=args.out, stdout=console, stderr=subprocess.STDOUT,
        )
    if result.returncode:
        parser.exit(result.returncode, f"Yosys failed. See {args.out / 'console.log'}\n")

    stats = json.loads((args.out / "stat.json").read_text())
    top = stats["modules"][f"\\{args.top}"]
    if top["num_memories"] or top["num_submodules"]:
        parser.exit(1, "Expected a flat mapped design with no inferred memories\n")
    macro_instances = sum(macros.values())
    standard_cells = top["num_cells"] - macro_instances
    if standard_cells and "area" not in top:
        parser.exit(1, "Yosys did not report area for the mapped cells\n")
    summary = {
        "top": args.top,
        "yosys": stats["creator"],
        "abc_mapping": ABC_FAST if args.abc_fast else "default",
        "liberty": args.liberty.name,
        "liberty_sha256": hashlib.sha256(args.liberty.read_bytes()).hexdigest(),
        "area_um2": top.get("area", 0),
        "sequential_area_um2": top.get("sequential_area", 0),
        "standard_cells": standard_cells,
        "excluded_macro_instances": macros,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
