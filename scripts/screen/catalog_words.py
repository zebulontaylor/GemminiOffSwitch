#!/usr/bin/env python3
"""Catalog named packed RTL words in Gemmini's optimized design hierarchy."""

import argparse
import csv
import gzip
import io
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_guard import ensure_guarded

IDENT = r"[a-zA-Z_][a-zA-Z_0-9$]*"
PATH = re.compile(rf"{IDENT}(?:\.{IDENT})*")
INSTANCE = re.compile(rf"^  ({IDENT})\s+({IDENT})\s*\(", re.M)
DECL = re.compile(rf"^  (wire|reg|logic)\s+(?:signed\s+)?(?:\[(\d+):(\d+)\]\s*)?({IDENT})", re.M)
PORT = re.compile(rf"(?:(input|output|inout)\s+)?(?:\[(\d+):(\d+)\]\s*)?({IDENT})")
CONTROL_PINS = {"CLK", "ARST", "SRST", "SET", "CLR"}
DECL_PRIORITY = {"reg": 0, "wire": 1, "logic": 1, "output": 2, "input": 3}
MULTIDIM = re.compile(rf"^  (?:wire|reg|logic)\s+(?:signed\s+)?(?:\[\d+:\d+\]\s*){{2,}}({IDENT})", re.M)


@contextmanager
def compressed_writer(path):
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as stream:
                yield csv.writer(stream, lineterminator="\n")



def load_modules(rtl):
    """Validate names against firtool's split, nonparameterized module declarations."""
    modules = {}
    for file in sorted(rtl.glob("*.sv")):
        text = file.read_text()
        head = re.search(rf"\bmodule\s+({IDENT})\s*\((.*?)\);", text, re.S)
        if not head:
            continue
        names = {}
        direction, width = None, 1
        for part in head[2].split(","):
            m = PORT.fullmatch(part.strip())
            if m:
                if m[1]:
                    direction = m[1]
                    width = 1
                if m[2]:
                    width = abs(int(m[2]) - int(m[3])) + 1
                names[m[4]] = (direction, width)
        for m in DECL.finditer(text):
            width = abs(int(m[2]) - int(m[3])) + 1 if m[2] else 1
            names[m[4]] = (m[1], width)
        for name in MULTIDIM.findall(text):
            names[name] = ("multidimensional", None)
        modules[head[1]] = (names, {inst: mod for mod, inst in INSTANCE.findall(text)})
    return modules


def collect(netlist, rtl, top, output, preferred=()):
    module = json.loads(netlist.read_bytes())["modules"][top]
    modules = load_modules(rtl)
    scopes = {"": top}

    def scope_module(scope):
        if scope not in scopes:
            parent, _, inst = scope.rpartition(".")
            parent_type = scope_module(parent)
            scopes[scope] = modules.get(parent_type, ({}, {}))[1].get(inst)
        return scopes[scope]

    excluded = set()
    for port in module["ports"].values():
        excluded.update(b for b in port["bits"] if isinstance(b, int))
    for cell in module["cells"].values():
        for pin in CONTROL_PINS & cell["connections"].keys():
            excluded.update(b for b in cell["connections"][pin] if isinstance(b, int))

    words = defaultdict(list)
    omitted = []
    top_inputs = {name for name, p in module["ports"].items() if p["direction"] == "input"}
    drivers = {}
    for cell in module["cells"].values():
        for port, bits in cell["connections"].items():
            if cell["port_directions"][port] == "output":
                drivers[tuple(bits)] = cell["type"]

    for path, net in module["netnames"].items():
        if net["hide_name"] or not PATH.fullmatch(path):
            omitted.append((path, "private_or_non_rtl_name"))
            continue
        scope, _, signal = path.rpartition(".")
        source_type = scope_module(scope)
        decl = modules.get(source_type, ({}, {}))[0].get(signal)
        bits = tuple(net["bits"])
        if decl and decl[0] == "multidimensional":
            omitted.append((path, "multidimensional_packed"))
            continue
        if not decl or decl[1] != len(bits):
            omitted.append((path, "missing_or_width_mismatch"))
            continue
        if not any(isinstance(b, int) for b in bits):
            omitted.append((path, "constant"))
            continue
        words[bits].append((path, decl[0], source_type))

    rows, aliases = [], []
    def preference(member):
        path, decl, _ = member
        return path not in preferred, DECL_PRIORITY.get(decl, 4), path.count("."), path

    for bits, members in words.items():
        cell_type = drivers.get(bits, "")
        path, decl, source_type = min(members, key=preference)
        region = path.rpartition(".")[0] or top
        block = region.split(".", 1)[0]
        distinct = len(set(bits)) == len(bits) and all(isinstance(b, int) for b in bits)
        candidate = int(bool(cell_type) and distinct and not any(b in excluded for b in bits))
        if decl == "reg" or "dff" in cell_type or "latch" in cell_type:
            kind = "state"
        elif "memrd" in cell_type:
            kind = "memread"
        elif any(m[0] in top_inputs for m in members):
            kind = "input"
        else:
            kind = "comb"
        rows.append((path, len(bits), region, kind, candidate, source_type, block, cell_type, len(members)))
        aliases.extend((m[0], path) for m in members if m[0] != path)
    rows.sort()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "words.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["id", "path", "width", "region", "kind", "candidate", "module", "block", "cell_type", "n_aliases"])
        for i, row in enumerate(rows):
            writer.writerow((i, *row))
    with compressed_writer(output / "aliases.csv.gz") as writer:
        writer.writerow(["alias", "canonical"])
        writer.writerows(sorted(aliases))
    with compressed_writer(output / "omitted_names.csv.gz") as writer:
        writer.writerow(["name", "reason"])
        writer.writerows(sorted(omitted))
    summary = dict(
        top=top, words=len(rows), bits=sum(r[1] for r in rows),
        candidates=sum(r[4] for r in rows),
        distinct_cell_driver_words=len(drivers),
        cell_driver_words_with_rtl_names=len(set(drivers) & words.keys()),
        public_names_represented=sum(len(m) for m in words.values()),
        omitted_names=dict(Counter(reason for _, reason in omitted)),
        total_yosys_netnames=len(module["netnames"]),
        scope="Distinct nonconstant scalar or one-dimensional packed word vectors with a validated source RTL path. Unpacked memory contents and unnamed synthesis intermediates are outside this catalog.",
        sampling="Each ID measures the listed original RTL representative. Ordered bit vectors coincide in the optimized baseline; a force on one RTL alias need not change every equivalent source path.",
    )
    (output / "word_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def path_arg(path):
    return '"' + str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def extract_netlist(sources, top, out, yosys="yosys"):
    ensure_guarded()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError("--top must be a plain Verilog identifier")
    out.parent.mkdir(parents=True, exist_ok=True)
    out = out.resolve()
    source_list = out.parent / f"{top}.sources.f"
    source_list.write_text("\n".join(path_arg(p) for p in sources) + "\n")
    script = out.with_suffix(".ys")
    script.write_text("\n".join([
        f"read_slang --keep-hierarchy --ignore-assertions -DSYNTHESIS --top {top} -f {source_list.name}",
        f"hierarchy -check -top {top}",
        "proc",
        "opt",
        "flatten -scopename",
        "opt",
        "memory_dff",
        "opt_clean",
        f"write_json {path_arg(out)}",
        "",
    ]))
    subprocess.run([yosys, "-Q", "-q", "-m", "slang", "-l",
                    str(out.with_suffix(".yosys.log")), "-s", str(script)], cwd=out.parent, check=True)


def _cmd_netlist(args, parser):
    try:
        extract_netlist(args.sources, args.top, args.out, args.yosys)
    except ValueError as exc:
        parser.error(str(exc))


def _cmd_catalog(args, _parser):
    preferred = set()
    if args.preferred is not None:
        with args.preferred.open() as stream:
            preferred = {row["path"] for row in csv.DictReader(stream)}
    summary = collect(args.netlist, args.rtl, args.top, args.output, preferred)
    if args.publish:
        args.publish.mkdir(parents=True, exist_ok=True)
        with (args.output / "words.csv").open("rb") as source, (args.publish / "words.csv.gz").open("wb") as dest:
            with gzip.GzipFile(filename="", fileobj=dest, mode="wb", mtime=0) as compressed:
                shutil.copyfileobj(source, compressed)
        for name in ("word_summary.json", "aliases.csv.gz", "omitted_names.csv.gz"):
            shutil.copyfile(args.output / name, args.publish / name)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    net_parser = subparsers.add_parser("netlist", help="Lower elaborated SV to flattened Yosys JSON")
    net_parser.add_argument("sources", nargs="+", type=Path, help="Elaborated SystemVerilog source files")
    net_parser.add_argument("--top", required=True, help="Synthesis top module")
    net_parser.add_argument("--out", required=True, type=Path, help="Yosys JSON output path")
    net_parser.add_argument("--yosys", default="yosys")
    net_parser.set_defaults(func=_cmd_netlist)

    cat_parser = subparsers.add_parser("catalog", help="Catalog named packed RTL words from Yosys JSON")
    cat_parser.add_argument("--netlist", type=Path, default=Path("build/netlist/Gemmini.json"))
    cat_parser.add_argument("--rtl", type=Path, default=Path("build/elab/gemmini"))
    cat_parser.add_argument("--top", default="Gemmini")
    cat_parser.add_argument("--output", type=Path, default=Path("build/campaign"))
    cat_parser.add_argument("--publish", type=Path, help="copy compressed catalog and summary to a results directory")
    cat_parser.add_argument("--preferred", type=Path,
                            help="optional CSV with a path column naming aliases to preserve")
    cat_parser.set_defaults(func=_cmd_catalog)

    args = parser.parse_args()
    args.func(args, parser)
