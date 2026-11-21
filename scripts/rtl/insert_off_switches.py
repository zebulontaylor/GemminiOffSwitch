#!/usr/bin/env python3
"""Insert independently controlled zero clamps into the split Gemmini RTL."""

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
import re
import shutil


ARRAY = "ex_controller.mesh.mesh."
DEFAULT_SITES = Path(__file__).resolve().parents[2] / "infra/gemmini/array-switch-sites.csv"
GRANT = "off_switch_grant"
VARIANTS = "OffSwitchVariants.sv"
IDENT = r"[A-Za-z_][\w$]*"
SITE_PATH = re.compile(rf"(?P<signal>{IDENT}(?:\.{IDENT})*)(?:\[(?P<index>0|[1-9][0-9]*)\])?")
MODULE_NAME = re.compile(rf"^\s*module\s+({IDENT})(?![\w$])", re.M)
MODULE = re.compile(rf"^module\s+({IDENT})\s*\((.*?)\);", re.M | re.S)
INSTANCE = re.compile(
    rf"^  ({IDENT})\s+(?:#\s*\([\s\S]*?\)\s*)?({IDENT})\s*(\()\s*(?=\.)[\s\S]*?\);", re.M
)
CONNECTION = re.compile(rf"\.({IDENT})\s*\(")
TOKEN = re.compile(rf"{IDENT}|===|!==|<<<|>>>|==|!=|>=|<=|<<=|>>=|[-+*/%&|^]=|\+\+|--|[^\s]")
ASSIGNMENT = {"=", "<=", "<<=", ">>=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "++", "--"}
CLOCK_RESET = {"clock", "reset"}
MASK = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"')
PORT = re.compile(rf"\s*(?:(input|output)\s+)?(?:\[(\d+):(\d+)\]\s*)?({IDENT})\s*")
DECLARATION = re.compile(
    rf"^[ \t]*(wire|reg)\s+(?:\[(\d+):(\d+)\]\s*)?({IDENT})\s*(?=[;=])", re.M
)


@dataclass
class Node:
    bits: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)

    def all_bits(self):
        local = [bit for selections in self.bits.values() for bit in selections.values()]
        return sorted([*local, *(bit for node in self.children.values() for bit in node.all_bits())])


@dataclass
class Module:
    source: str

    @cached_property
    def code(self):
        return MASK.sub(lambda m: re.sub(r"[^\n]", " ", m[0]), self.source)

    @cached_property
    def header(self):
        return MODULE.search(self.code)

    @cached_property
    def ports(self):
        ports = {}
        direction = msb = lsb = None
        for item in re.finditer(r"[^,]+", self.header[2]):
            match = PORT.fullmatch(item[0])
            if not match:
                raise ValueError(f"Unsupported {self.header[1]} port declaration {item[0]!r}")
            if match[1]:
                direction, msb, lsb = match[1], match[2], match[3]
            ports[match[4]] = (direction, msb, lsb, self.header.start(2) + item.start() + match.start(4))
        return ports

    @cached_property
    def instances(self):
        return {match[2]: match for match in INSTANCE.finditer(self.code, self.header.end())}

    @cached_property
    def declarations(self):
        return {match[4]: match for match in DECLARATION.finditer(self.code, self.header.end())}


def connections(instance):
    """Return named port and expression spans for a firtool instance."""
    source = instance[0]
    position = instance.end(3) - instance.start()
    while match := CONNECTION.search(source, position):
        depth = 1
        for index in range(match.end(), len(source)):
            depth += (source[index] == "(") - (source[index] == ")")
            if depth == 0:
                offset = instance.start()
                yield match[1], offset + match.start(1), offset + match.end(), offset + index
                position = index + 1
                break
        else:
            raise ValueError(f"Unbalanced ports for instance {instance[2]}")


def assignment_targets(module, signals, path):
    """Collect firtool's whole-word writes and reject unsupported lvalues."""
    body_start = module.header.end()
    code = module.code
    tokens = list(TOKEN.finditer(code, body_start))
    delimiters = {")": "(", "]": "[", "}": "{"}
    stack = []
    matching = {}
    targets = defaultdict(list)
    declaration_offsets = {match.start(4) for match in module.declarations.values()}

    for index, token in enumerate(tokens):
        word = token[0]
        if word in delimiters.values():
            stack.append(index)
        elif word in delimiters and stack and tokens[stack[-1]][0] == delimiters[word]:
            matching[index] = stack.pop()

        if word not in ASSIGNMENT or index == 0:
            continue
        end = index - 1
        start = end
        while tokens[start][0] == "]" and start in matching and matching[start] > 0:
            start = matching[start] - 1
        if tokens[start][0] == "}" and start in matching:
            start = matching[start]
        if not (used := ({tok[0] for tok in tokens[start:end + 1]} & signals)):
            continue

        left = tokens[start].start()
        if start == end and left in declaration_offsets and word == "=":
            continue
        line_start = code.rfind("\n", body_start, left) + 1
        prefix = code[line_start:left].strip()
        if start != end or prefix not in ("", "assign") or word not in ("=", "<="):
            raise ValueError(f"Unsupported assignment or lvalue for {path}{', '.join(sorted(used))}")
        targets[tokens[start][0]].append(left)

    return targets


def load_sites(path):
    tree = Node()
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or "path" not in rows[0]:
        raise ValueError("The site list must contain a path column and at least one row")
    for bit, row in enumerate(rows):
        path = row["path"]
        match = SITE_PATH.fullmatch(path or "")
        if not match or not path.startswith(ARRAY):
            raise ValueError(f"Expected a signal path under {ARRAY[:-1]}: {path!r}")
        node = tree
        *parents, signal = match["signal"].split(".")
        index = int(match["index"]) if match["index"] is not None else None
        if signal in CLOCK_RESET:
            raise ValueError(f"Clock and reset signals cannot be clamped: {path}")
        for parent in parents:
            node = node.children.setdefault(parent, Node())
        selections = node.bits.setdefault(signal, {})
        if index in selections:
            raise ValueError(f"Duplicate signal path {path}")
        if selections and (index is None or None in selections):
            raise ValueError(f"Overlapping whole-word and bit selections for {match['signal']}")
        selections[index] = bit
    return tree, rows


def unselected_slices(msb, lsb, selected):
    """Return selectors for contiguous runs of unselected packed bits."""
    step = 1 if lsb > msb else -1
    start = msb
    for index in sorted(selected, reverse=step < 0) + [lsb + step]:
        if index != start:
            end = index - step
            yield f"[{start}]" if start == end else f"[{start}:{end}]"
        start = index + step


def replace(source, edits):
    end = len(source)
    parts = []
    for start, stop, value in sorted(edits, reverse=True):
        if stop > end:
            raise ValueError("Overlapping RTL edits")
        parts.extend((source[stop:end], value))
        end = start
    parts.append(source[:end])
    return "".join(reversed(parts))


class Inserter:
    def __init__(self, modules, module_names):
        self.modules = modules
        self.module_names = module_names
        self.variants = []
        self.widths = {}
        self.packed_ranges = {}

    def render(self, module_name, node, path=""):
        if module_name not in self.modules:
            raise ValueError(f"Missing split RTL module {module_name} at {path.rstrip('.')}")
        module = self.modules[module_name]
        if len(MODULE_NAME.findall(module.code)) != 1:
            raise ValueError(f"Expected one split RTL module in the source for {module_name}")
        bits = node.all_bits()
        local = {bit: index for index, bit in enumerate(bits)}
        variant = f"OffSwitch_{module_name}_{bits[0]}" if path else module_name
        if path and variant in self.module_names:
            raise ValueError(f"Module name {variant} already exists")
        if re.search(rf"(?<![\w$]){GRANT}(?![\w$])", module.code):
            raise ValueError(f"{module_name} already contains {GRANT}")
        edits = [(module.header.start(1), module.header.end(1), variant),
                 (module.header.start(2), module.header.start(2), f"\n  input [{len(bits)-1}:0] {GRANT},")]
        drivers = defaultdict(list)
        for instance_name, instance in module.instances.items():
            child_module = self.modules.get(instance[1])
            child_node = node.children.get(instance_name)
            if child_node is None and not node.bits:
                continue
            binding = list(connections(instance))
            if child_node is not None:
                child_name, child_ports, child_source = self.render(instance[1], child_node, path + instance_name + ".")
                self.variants.append(child_source)
                edits.append((instance.start(1), instance.end(1), child_name))
                args = [f"{GRANT}[{local[bit]}]" for bit in reversed(child_node.all_bits())]
                grant = args[0] if len(args) == 1 else "{" + ", ".join(args) + "}"
                start = instance.end(3)
                edits.append((start, start, f"\n    .{GRANT} ({grant}),"))
                if missing := child_ports.keys() - {name for name, *_ in binding}:
                    raise ValueError(f"Missing port bindings on {path}{instance_name}: {sorted(missing)}")
                for name, name_at, _, _ in binding:
                    if name in child_ports:
                        edits.append((name_at, name_at + len(name), child_ports[name]))
            if not node.bits:
                continue
            for name, _, start, stop in binding:
                expr = module.code[start:stop]
                used = set(re.findall(IDENT, expr)) & node.bits.keys()
                if not used:
                    continue
                if name in CLOCK_RESET:
                    raise ValueError(f"Clamping {path}{', '.join(sorted(used))} would gate {instance_name}.{name}")
                if child_module is None or name not in child_module.ports:
                    raise ValueError(f"Unknown port direction at {path}{instance_name}.{name}")
                if child_module.ports[name][0] != "output":
                    continue
                signal = expr.strip()
                if signal not in used:
                    raise ValueError(f"Unsupported output connection at {path}{instance_name}.{name}")
                offset = start + len(expr) - len(expr.lstrip())
                drivers[signal].append(offset)
        if missing := node.children.keys() - module.instances.keys():
            raise ValueError(f"Missing RTL instances under {path or module_name}: {sorted(missing)}")

        declarations, assignments, aliases = self.gates(module, node, path, local, drivers, edits)
        if declarations:
            end = module.code.rindex("endmodule")
            edits.extend([(module.header.end(), module.header.end(), "".join(declarations)),
                          (end, end, "".join(assignments))])
        return variant, aliases, replace(module.source, edits)

    def gates(self, module, node, path, local, drivers, edits):
        declarations = []
        gate_assignments = []
        aliases = {}
        if not node.bits:
            return declarations, gate_assignments, aliases
        assignments = assignment_targets(module, node.bits.keys(), path)
        clocked = set(re.findall(rf"\b(?:posedge|negedge)\s+({IDENT})", module.code))
        for signal, selections in node.bits.items():
            if signal in clocked:
                raise ValueError(f"Clamping {path}{signal} would gate an edge-sensitive event")
            raw = f"off_switch_raw_{signal}"
            if re.search(rf"(?<![\w$]){re.escape(raw)}(?![\w$])", module.code):
                raise ValueError(f"Raw name {raw} already exists at {path}")
            declaration = module.declarations.get(signal)
            if signal in module.ports:
                kind, msb, lsb, offset = module.ports[signal]
            elif declaration:
                kind, msb, lsb, _ = declaration.groups()
                offset = declaration.start(4)
            else:
                raise ValueError(f"Missing or unsupported RTL declaration for {path}{signal}")

            packed = f" [{msb}:{lsb}]" if msb is not None else ""
            if kind == "output":
                declarations.append(f"\n  wire{packed} {raw};")
            else:
                edits.append((offset, offset + len(signal), raw))
                declarations.append(f"\n  wire{packed} {signal};")
            if None in selections:
                bit = selections[None]
                self.widths[bit] = abs(int(msb) - int(lsb)) + 1 if packed else 1
                gate_assignments.append(f"  assign {signal} = {GRANT}[{local[bit]}] ? {raw} : '0;\n")
            else:
                if not packed:
                    raise ValueError(f"Expected a packed declaration for bit selection on {path}{signal}")
                left, right = int(msb), int(lsb)
                for index, bit in selections.items():
                    if not min(left, right) <= index <= max(left, right):
                        raise ValueError(f"Bit {path}{signal}[{index}] is outside {packed.strip()}")
                    self.widths[bit] = 1
                    self.packed_ranges[bit] = f"{left}:{right}"
                    gate_assignments.append(
                        f"  assign {signal}[{index}] = {GRANT}[{local[bit]}] ? {raw}[{index}] : 1'b0;\n"
                    )
                for selector in unselected_slices(left, right, selections):
                    gate_assignments.append(f"  assign {signal}{selector} = {raw}{selector};\n")

            targets = drivers[signal] + assignments[signal]
            initialized = declaration is not None and module.code[declaration.end()] == "="
            if kind == "input":
                if targets:
                    raise ValueError(f"Unexpected driver for input {path}{signal}")
                aliases[signal] = raw
                continue
            driver_count = len(targets) + initialized
            if driver_count == 0:
                raise ValueError(f"No driver found for {path}{signal}")
            if driver_count > 1 and (kind != "reg" or drivers[signal]):
                raise ValueError(f"Multiple drivers for {path}{signal}")
            edits.extend((start, start + len(signal), raw) for start in targets)
        return declarations, gate_assignments, aliases


def insert(rtl, dest, sites):
    rtl, dest = rtl.resolve(), dest.resolve()
    if rtl == dest or rtl in dest.parents or dest in rtl.parents:
        raise ValueError("The original and output RTL directories must be separate")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ValueError("The output directory must be empty")
    if (rtl / VARIANTS).exists() or (rtl / "switch-sites.csv").exists():
        raise ValueError("Expected the original split RTL as input")

    modules = {}
    files = {}
    module_names = set()
    for source in sorted([*rtl.glob("*.sv"), *rtl.glob("*.v")]):
        module = Module(source.read_text())
        for name in MODULE_NAME.findall(module.code):
            if name in module_names:
                raise ValueError(f"Duplicate RTL module {name}")
            module_names.add(name)
        if module.header:
            name = module.header[1]
            modules[name] = module
            files[name] = source.name
    if "Gemmini" not in modules:
        raise ValueError("No Gemmini module in the split RTL directory")
    tree, rows = load_sites(sites)
    inserter = Inserter(modules, module_names)
    _, _, top = inserter.render("Gemmini", tree)
    shutil.copytree(rtl, dest, dirs_exist_ok=True)
    (dest / files["Gemmini"]).write_text(top)
    (dest / VARIANTS).write_text("\n".join(inserter.variants))
    filelist = dest / "filelist.f"
    if filelist.exists():
        filelist.write_text(filelist.read_text().rstrip("\n") + f"\n{VARIANTS}\n")
    with (dest / "switch-sites.csv").open("w", newline="") as stream:
        fields = ["grant_bit", "path", "width"]
        if inserter.packed_ranges:
            fields.append("packed_range")
        metadata = [name for name in rows[0] if name not in fields]
        writer = csv.DictWriter(stream, fieldnames=[*fields, *metadata], lineterminator="\n")
        writer.writeheader()
        for bit, row in enumerate(rows):
            result = {**row, "grant_bit": bit, "width": inserter.widths[bit]}
            if inserter.packed_ranges:
                result["packed_range"] = inserter.packed_ranges.get(bit, "")
            writer.writerow(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rtl", type=Path, help="Original split Gemmini RTL directory")
    parser.add_argument("dest", type=Path, help="Empty output directory")
    parser.add_argument("--sites", type=Path, default=DEFAULT_SITES, help="Ordered CSV with a path column")
    args = parser.parse_args()
    try:
        insert(args.rtl, args.dest, args.sites)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
