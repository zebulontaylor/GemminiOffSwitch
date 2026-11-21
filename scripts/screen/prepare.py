#!/usr/bin/env python3
"""Prepare universal uncloned Mesh fault-injection RTL and candidate tables for fast screening."""

import argparse
import csv
import gzip
from collections import defaultdict
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtl.insert_off_switches import (
    ARRAY, CLOCK_RESET, IDENT, SITE_PATH, Module,
    assignment_targets, connections, replace,
)
from screen.sample_bits import sample_mesh_bits

MESH_PORTS_IN = (
    ("input  [11:0]", "off_switch_mesh_sig"),
    ("input  [8:0]", "off_switch_tile"),
    ("input  [3:0]", "off_switch_pe_sig"),
    ("input  [31:0]", "off_switch_mask"),
)
MESH_PORTS_OUT = (
    ("output", "off_switch_raw_active"),
)
GEMMINI_TAP_OUT = (
    ("output", "off_switch_acc_row_valid"),
    ("output [3:0]", "off_switch_acc_row_addr"),
)
TILE_PORTS_IN = (
    ("input", "off_switch_tile_en"),
    ("input  [3:0]", "off_switch_pe_sig"),
    ("input  [31:0]", "off_switch_mask"),
)
TILE_PORTS_OUT = (
    ("output", "off_switch_raw_active"),
)

DEFAULT_RTL = Path("build/elab/gemmini")
DEFAULT_WORDS = Path("results/scaled/catalog/words.csv.gz")


def load_mesh_words(words_path):
    opener = gzip.open if words_path.suffix == ".gz" else open
    with opener(words_path, "rt", newline="") as stream:
        words = [row for row in csv.DictReader(stream) if row["path"].startswith(ARRAY)]
    if not words:
        raise ValueError(f"{words_path}: no mesh words found under {ARRAY[:-1]}")
    for index, word in enumerate(words):
        word["catalog_id"] = word["id"]
        word["id"] = str(index)
    return words


def expand_single_bits(words):
    """Expand every eligible mesh word into single-bit sites with dense mesh IDs."""
    bits = []
    next_id = len(words)
    for word in words:
        width = int(word["width"])
        base = dict(word, word_width=str(width), bit_index="")
        if word["candidate"] != "1":
            continue
        if width == 1:
            bits.append(base)
            continue
        for bit_index in range(width):
            row = dict(base, path=f"{word['path']}[{bit_index}]", width="1", bit_index=str(bit_index))
            bits.append(row)
    return bits, next_id


def add_passthrough(module, child_instance, ports_in, ports_out, renamed_child_ports=None, extra_out=(), extra_assigns=()):
    edits = []
    port_decls = "".join(f"\n  {typ} {name}," for typ, name in [*ports_in, *ports_out, *extra_out])
    edits.append((module.header.start(2), module.header.start(2), port_decls))
    inst = module.instances[child_instance]
    port_binds = "".join(f"\n    .{name} ({name})," for _, name in [*ports_in, *ports_out])
    edits.append((inst.end(3), inst.end(3), port_binds))
    if renamed_child_ports:
        for pname, name_at, _, _ in connections(inst):
            if pname in renamed_child_ports:
                edits.append((name_at, name_at + len(pname), renamed_child_ports[pname]))
    if extra_assigns:
        end = module.code.rindex("endmodule")
        edits.append((end, end, "".join(extra_assigns)))
    return replace(module.source, edits)


def collect_output_drivers(module, modules, sigs):
    drivers = defaultdict(list)
    for inst_name, inst in module.instances.items():
        child = modules.get(inst[1])
        for pname, _, start, stop in connections(inst):
            expr = module.code[start:stop]
            used = set(re.findall(IDENT, expr)) & sigs
            if not used:
                continue
            if pname in CLOCK_RESET:
                raise ValueError(f"Clamping would gate {inst_name}.{pname}")
            if child is None or pname not in child.ports:
                raise ValueError(f"Unknown port direction at {inst_name}.{pname}")
            if child.ports[pname][0] != "output":
                continue
            sig = expr.strip()
            if sig not in used:
                raise ValueError(f"Unsupported output connection at {inst_name}.{pname}")
            offset = start + len(expr) - len(expr.lstrip())
            drivers[sig].append(offset)
    return drivers


def prepare_signal_gating(module, modules, sigs):
    drivers = collect_output_drivers(module, modules, sigs)
    assignments = assignment_targets(module, sigs, module.header[1] + ".")
    clocked = set(re.findall(rf"\b(?:posedge|negedge)\s+({IDENT})", module.code))
    edits = []
    info = {}
    aliases = {}

    for sig in sorted(sigs):
        if sig in clocked:
            raise ValueError(f"Clamping {sig} would gate an edge-sensitive event")
        raw = f"off_switch_raw_{sig}"
        if re.search(rf"(?<![\w$]){re.escape(raw)}(?![\w$])", module.code):
            raise ValueError(f"Raw name {raw} already exists in {module.header[1]}")
        decl = module.declarations.get(sig)
        if sig in module.ports:
            kind, msb, lsb, offset = module.ports[sig]
        elif decl:
            kind, msb, lsb, _ = decl.groups()
            offset = decl.start(4)
        else:
            raise ValueError(f"Missing declaration for {sig} in {module.header[1]}")

        packed = f" [{msb}:{lsb}]" if msb is not None else ""
        width = abs(int(msb) - int(lsb)) + 1 if packed else 1
        packed_range = f"{msb}:{lsb}" if packed else ""
        targets = drivers[sig] + assignments[sig]
        initialized = decl is not None and module.code[decl.end()] == "="
        if kind == "input":
            if targets:
                raise ValueError(f"Unexpected driver for input {sig}")
            aliases[sig] = raw
        else:
            driver_count = len(targets) + initialized
            if driver_count == 0:
                raise ValueError(f"No driver found for {sig}")
            if driver_count > 1 and (kind != "reg" or drivers[sig]):
                raise ValueError(f"Multiple drivers for {sig}")
            edits.extend((start, start + len(sig), raw) for start in targets)

        if kind != "output":
            edits.append((offset, offset + len(sig), raw))
        info[sig] = (kind, packed, width, packed_range, raw)
    return edits, info, aliases


def emit_pe_gates(sig_to_id, info, extra_raw_active):
    decls = []
    assigns = []
    case_items = []
    for sig, sig_id in sorted(sig_to_id.items(), key=lambda kv: kv[1]):
        kind, packed, width, _, raw = info[sig]
        decls.append(f"\n  wire{packed} {raw if kind == 'output' else sig};")
        mask_slice = "off_switch_mask[0]" if width == 1 else f"off_switch_mask[{width-1}:0]"
        assigns.append(
            f"  assign {sig} = (off_switch_tile_en && off_switch_pe_sig == 4'd{sig_id}) "
            f"? ({raw} & {mask_slice}) : {raw};\n"
        )
        case_items.append(f"        4'd{sig_id}: off_switch_local_active = |({raw} & ~{mask_slice});\n")

    active_block = [
        "  reg off_switch_local_active;\n",
        "  always @(*) begin\n",
        "    off_switch_local_active = 1'b0;\n",
        "    if (off_switch_tile_en) begin\n",
        "      case (off_switch_pe_sig)\n",
        *case_items,
        "        default: off_switch_local_active = 1'b0;\n",
        "      endcase\n",
        "    end\n",
        "  end\n",
        f"  assign off_switch_raw_active = {extra_raw_active};\n",
    ]
    return decls, assigns, active_block


def emit_mesh_layer_block(layer_name, items, info, sel_expr, mask_expr):
    decls = []
    defaults = []
    cases = []
    for sig, sig_id in items:
        kind, packed, width, _, raw = info[sig]
        if kind == "output":
            raise ValueError(f"Unexpected output signal {sig} in Mesh layer {layer_name}")
        decls.append(f"\n  reg{packed} {sig};")
        mask_slice = f"{mask_expr}[0]" if width == 1 else f"{mask_expr}[{width-1}:0]"
        defaults.append(f"    {sig} = {raw};\n")
        cases.append(
            f"        12'd{sig_id}: begin {sig} = {raw} & {mask_slice}; "
            f"{layer_name}_active = |({raw} & ~{mask_slice}); end\n"
        )
    lo = items[0][1]
    hi = items[-1][1]
    cond = f"{sel_expr} <= 12'd{hi}" if lo == 0 else f"{sel_expr} >= 12'd{lo} && {sel_expr} <= 12'd{hi}"
    block = [
        f"  reg {layer_name}_active;\n",
        "  always @(*) begin\n",
        *defaults,
        f"    {layer_name}_active = 1'b0;\n",
        f"    if ({cond}) begin\n",
        f"      case ({sel_expr})\n",
        *cases,
        "        default: ;\n",
        "      endcase\n",
        "    end\n",
        "  end\n",
    ]
    return decls, block


def prepare_universal_rtl(rtl_dir, words_path, out_dir):
    rtl_dir, out_dir = rtl_dir.resolve(), out_dir.resolve()
    if rtl_dir == out_dir or rtl_dir in out_dir.parents or out_dir in rtl_dir.parents:
        raise ValueError("The original and output RTL directories must be separate")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise ValueError(f"Use an empty output directory: {out_dir}")

    modules = {}
    for source in sorted([*rtl_dir.glob("*.sv"), *rtl_dir.glob("*.v")]):
        module = Module(source.read_text())
        if module.header:
            modules[module.header[1]] = module
    for required in ("Gemmini", "ExecuteController", "MeshWithDelays", "Mesh", "Tile", "PE_256", "MacUnit"):
        if required not in modules:
            raise ValueError(f"Missing required module {required} in {rtl_dir}")

    mesh_words = load_mesh_words(words_path)
    candidates = [row for row in mesh_words if row["candidate"] == "1"]
    unexpected_modules = sorted({row["module"] for row in candidates} - {"Mesh", "PE_256", "MacUnit"})
    if unexpected_modules:
        raise ValueError(f"Unsupported candidate modules in mesh catalog: {unexpected_modules}")

    mac_sigs = sorted({r["path"].rsplit(".", 1)[-1] for r in candidates if r["module"] == "MacUnit"})
    pe_sigs = sorted({r["path"].rsplit(".", 1)[-1] for r in candidates if r["module"] == "PE_256"})
    mesh_sigs = sorted({r["path"].removeprefix(ARRAY) for r in candidates if r["module"] == "Mesh"})
    if len(mac_sigs) + len(pe_sigs) > 15:
        raise ValueError("PE/MacUnit signal count exceeds 4-bit selector capacity (15)")
    if len(mesh_sigs) > 4095:
        raise ValueError("Mesh signal count exceeds 12-bit selector capacity (4095)")

    mac_id = {s: i for i, s in enumerate(mac_sigs)}
    pe_id = {s: len(mac_sigs) + i for i, s in enumerate(pe_sigs)}

    shutil.copytree(rtl_dir, out_dir, dirs_exist_ok=True)

    # 1. MacUnit
    mac_mod = modules["MacUnit"]
    edits, mac_info, mac_aliases = prepare_signal_gating(mac_mod, modules, set(mac_sigs))
    decls, assigns, active = emit_pe_gates(mac_id, mac_info, "off_switch_local_active")
    port_decls = "".join(f"\n  {t} {n}," for t, n in [*TILE_PORTS_IN, *TILE_PORTS_OUT])
    edits.append((mac_mod.header.start(2), mac_mod.header.start(2), port_decls))
    edits.append((mac_mod.header.end(), mac_mod.header.end(), "".join(decls)))
    end = mac_mod.code.rindex("endmodule")
    edits.append((end, end, "".join(assigns) + "".join(active)))
    (out_dir / "MacUnit.sv").write_text(replace(mac_mod.source, edits))

    # 2. PE_256
    pe_mod = modules["PE_256"]
    edits, pe_info, pe_aliases = prepare_signal_gating(pe_mod, modules, set(pe_sigs))
    decls, assigns, active = emit_pe_gates(pe_id, pe_info, "off_switch_local_active | _mac_unit_off_switch_raw_active")
    port_decls = "".join(f"\n  {t} {n}," for t, n in [*TILE_PORTS_IN, *TILE_PORTS_OUT])
    edits.append((pe_mod.header.start(2), pe_mod.header.start(2), port_decls))
    decls.append("\n  wire _mac_unit_off_switch_raw_active;")
    edits.append((pe_mod.header.end(), pe_mod.header.end(), "".join(decls)))
    mac_inst = pe_mod.instances["mac_unit"]
    mac_binds = (
        "\n    .off_switch_tile_en (off_switch_tile_en),"
        "\n    .off_switch_pe_sig (off_switch_pe_sig),"
        "\n    .off_switch_mask (off_switch_mask),"
        "\n    .off_switch_raw_active (_mac_unit_off_switch_raw_active),"
    )
    edits.append((mac_inst.end(3), mac_inst.end(3), mac_binds))
    for pname, name_at, _, _ in connections(mac_inst):
        if pname in mac_aliases:
            edits.append((name_at, name_at + len(pname), mac_aliases[pname]))
    end = pe_mod.code.rindex("endmodule")
    edits.append((end, end, "".join(assigns) + "".join(active)))
    (out_dir / "PE_256.sv").write_text(replace(pe_mod.source, edits))

    # 3. Tile
    (out_dir / "Tile.sv").write_text(
        add_passthrough(modules["Tile"], "tile_0_0", TILE_PORTS_IN, TILE_PORTS_OUT, pe_aliases)
    )

    # 4. Mesh: separate 4 input ports from 1,575 registers and 512 Tile outputs, and latch
    # fault selector registers during reset so fault inputs never enter Verilator's _eval_ico.
    mesh_mod = modules["Mesh"]
    edits, mesh_info, mesh_aliases = prepare_signal_gating(mesh_mod, modules, set(mesh_sigs))
    layer0_sigs = sorted(s for s in mesh_sigs if mesh_info[s][0] == "reg")
    layer1_sigs = sorted(s for s in mesh_sigs if mesh_info[s][0] == "wire")
    input_sigs = sorted(s for s in mesh_sigs if mesh_info[s][0] == "input")
    if len(layer0_sigs) + len(layer1_sigs) + len(input_sigs) != len(mesh_sigs):
        raise ValueError("Unsupported signal kind in Mesh candidate set")

    mesh_id = {}
    layer0_items, layer1_items, input_items = [], [], []
    for target_list, sig_list in ((layer0_items, layer0_sigs), (layer1_items, layer1_sigs), (input_items, input_sigs)):
        for s in sig_list:
            mesh_id[s] = len(mesh_id)
            target_list.append((s, mesh_id[s]))

    d0, b0 = emit_mesh_layer_block("off_switch_layer0", layer0_items, mesh_info, "off_switch_mesh_sig_r", "off_switch_mask_r")
    d1, b1 = emit_mesh_layer_block("off_switch_layer1", layer1_items, mesh_info, "off_switch_mesh_sig_r", "off_switch_mask_r")
    di, bi = emit_mesh_layer_block("off_switch_inputs", input_items, mesh_info, "off_switch_mesh_sig_r", "off_switch_mask_r")

    port_decls = "".join(f"\n  {t} {n}," for t, n in [*MESH_PORTS_IN, *MESH_PORTS_OUT])
    edits.append((mesh_mod.header.start(2), mesh_mod.header.start(2), port_decls))

    latch_decls = """
  /* verilator no_inline_module */
  reg [11:0]  off_switch_mesh_sig_r;
  reg [8:0]   off_switch_tile_r;
  reg [3:0]   off_switch_pe_sig_r;
  reg [31:0]  off_switch_mask_r;
  reg [255:0] off_switch_tile_en_r;
"""
    latch_block = """  always @(posedge clock) begin
    if (reset) begin
      off_switch_mesh_sig_r <= off_switch_mesh_sig;
      off_switch_tile_r     <= off_switch_tile;
      off_switch_pe_sig_r   <= off_switch_pe_sig;
      off_switch_mask_r     <= off_switch_mask;
      off_switch_tile_en_r  <= (off_switch_tile < 9'd256) ? (256'd1 << off_switch_tile) : 256'd0;
    end
  end
"""
    decls = [latch_decls, *d0, *d1, *di]
    tile_case = [
        "  reg off_switch_tile_active;\n",
        "  always @(*) begin\n",
        "    off_switch_tile_active = 1'b0;\n",
        "    if (off_switch_tile_r < 9'd256) begin\n",
        "      case (off_switch_tile_r)\n",
    ]
    tile_count = 0
    for inst_name, inst in mesh_mod.instances.items():
        match = re.fullmatch(r"mesh_(\d+)_(\d+)", inst_name)
        if not match:
            continue
        tid = int(match[1]) * 16 + int(match[2])
        tile_count += 1
        wire_name = f"_off_switch_tile_active_{tid}"
        decls.append(f"\n  wire {wire_name};")
        binds = (
            f"\n    .off_switch_tile_en (off_switch_tile_en_r[{tid}]),"
            "\n    .off_switch_pe_sig (off_switch_pe_sig_r),"
            "\n    .off_switch_mask (off_switch_mask_r),"
            f"\n    .off_switch_raw_active ({wire_name}),"
        )
        edits.append((inst.end(3), inst.end(3), binds))
        tile_case.append(f"        9'd{tid}: off_switch_tile_active = {wire_name};\n")
    if tile_count != 256:
        raise ValueError(f"Expected 256 Mesh tile instances, found {tile_count}")
    tile_case.extend([
        "        default: off_switch_tile_active = 1'b0;\n",
        "      endcase\n",
        "    end\n",
        "  end\n",
        "  assign off_switch_raw_active = off_switch_layer0_active | off_switch_layer1_active "
        "| off_switch_inputs_active | off_switch_tile_active;\n",
    ])
    edits.append((mesh_mod.header.end(), mesh_mod.header.end(), "".join(decls)))
    end = mesh_mod.code.rindex("endmodule")
    edits.append((end, end, latch_block + "".join(b0) + "".join(b1) + "".join(bi) + "".join(tile_case)))
    (out_dir / "Mesh.sv").write_text(replace(mesh_mod.source, edits))

    # 5. Wrapper passthroughs + Gemmini accumulator row write tap for golden scheduling
    (out_dir / "MeshWithDelays.sv").write_text(
        add_passthrough(modules["MeshWithDelays"], "mesh", MESH_PORTS_IN, MESH_PORTS_OUT, mesh_aliases)
    )
    (out_dir / "ExecuteController.sv").write_text(
        add_passthrough(modules["ExecuteController"], "mesh", MESH_PORTS_IN, MESH_PORTS_OUT)
    )
    gemmini_assigns = (
        "  assign off_switch_acc_row_valid = _ex_controller_io_acc_write_0_valid;\n",
        "  assign off_switch_acc_row_addr = _ex_controller_io_acc_write_0_bits_addr[3:0];\n",
    )
    (out_dir / "Gemmini.sv").write_text(
        add_passthrough(
            modules["Gemmini"], "ex_controller", MESH_PORTS_IN, MESH_PORTS_OUT,
            extra_out=GEMMINI_TAP_OUT, extra_assigns=gemmini_assigns,
        )
    )

    with (out_dir / "signal_map.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["path", "module", "width", "packed_range", "mesh_sig", "tile", "pe_sig"])
        for row in candidates:
            rel = row["path"].removeprefix(ARRAY)
            mod = row["module"]
            if mod == "Mesh":
                _, _, width, packed_range, _ = mesh_info[rel]
                writer.writerow([row["path"], mod, width, packed_range, mesh_id[rel], 511, 15])
            else:
                parts = rel.split(".")
                tm = re.fullmatch(r"mesh_(\d+)_(\d+)", parts[0])
                if not tm:
                    raise ValueError(f"Unsupported tile path {row['path']!r}")
                tid = int(tm[1]) * 16 + int(tm[2])
                sig = parts[-1]
                if mod == "MacUnit":
                    _, _, width, packed_range, _ = mac_info[sig]
                    sid = mac_id[sig]
                else:
                    _, _, width, packed_range, _ = pe_info[sig]
                    sid = pe_id[sig]
                writer.writerow([row["path"], mod, width, packed_range, 4095, tid, sid])
    (out_dir / "mesh_io_tap.vlt").write_text(
        "`verilator_config\n"
        'public_flat_rd -module "Mesh" -var "io_*"\n'
        'public_flat_rd -module "Mesh" -var "off_switch_raw_io_*"\n'
    )
    return len(candidates)


def prepare_sites(signal_map_path, words_path, out_dir, sites_path=None, count=None, seed=20261119, all_bits=False):
    with signal_map_path.open(newline="") as stream:
        sig_map = {row["path"]: row for row in csv.DictReader(stream)}

    if sites_path is not None:
        with sites_path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows or "path" not in rows[0]:
            raise ValueError(f"{sites_path}: expected a non-empty CSV with a path column")
    else:
        mesh_words = load_mesh_words(words_path)
        bits, next_id = expand_single_bits(mesh_words)
        if all_bits:
            rows = bits
        else:
            if count is None or not 1 <= count <= len(bits):
                raise ValueError(f"Choose 1–{len(bits)} single-bit candidates")
            rows = sample_mesh_bits(bits, count, seed)
        for row in rows:
            if row["bit_index"] != "":
                row["id"] = str(next_id)
                next_id += 1

    seen_paths = set()
    switch_rows = []
    compiled_rows = []
    has_packed = False
    for bit, row in enumerate(rows):
        path = row["path"]
        match = SITE_PATH.fullmatch(path or "")
        if not match or not path.startswith(ARRAY):
            raise ValueError(f"Expected a signal path under {ARRAY[:-1]}: {path!r}")
        if path in seen_paths:
            raise ValueError(f"Duplicate site path: {path}")
        seen_paths.add(path)

        word_path = match["signal"]
        if word_path not in sig_map:
            raise ValueError(f"Signal {word_path!r} is not an eligible mesh candidate in {signal_map_path}")
        info = sig_map[word_path]
        word_width = int(info["width"])
        packed_range = info["packed_range"]
        bit_index_str = match["index"]
        if bit_index_str is None:
            width = word_width
            mask = 0
            packed = ""
        else:
            bit_index = int(bit_index_str)
            if not packed_range:
                raise ValueError(f"Signal {word_path!r} is scalar; cannot select bit [{bit_index}]")
            left, right = map(int, packed_range.split(":"))
            if not min(left, right) <= bit_index <= max(left, right):
                raise ValueError(f"Bit {path} is outside [{packed_range}]")
            if bit_index >= 32:
                raise ValueError(f"Bit {path} exceeds 32-bit mask width")
            width = 1
            mask = (~(1 << bit_index)) & 0xFFFFFFFF
            packed = packed_range
            has_packed = True

        result = {**row, "grant_bit": bit, "width": width}
        if packed:
            result["packed_range"] = packed
        switch_rows.append(result)
        compiled_rows.append((bit, info["mesh_sig"], info["tile"], info["pe_sig"], f"{mask:08x}"))

    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["grant_bit", "path", "width"]
    if has_packed:
        fields.append("packed_range")
    metadata = [name for name in rows[0] if name not in fields]
    with (out_dir / "switch-sites.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[*fields, *metadata], lineterminator="\n")
        writer.writeheader()
        for row in switch_rows:
            if has_packed and "packed_range" not in row:
                row["packed_range"] = ""
            writer.writerow(row)
    with (out_dir / "candidates_compiled.csv").open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["grant_bit", "mesh_sig", "tile", "pe_sig", "mask"])
        writer.writerows(compiled_rows)
    return len(switch_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    rtl_cmd = sub.add_parser("rtl", help="Generate universal uncloned fault-injectable RTL")
    rtl_cmd.add_argument("out", type=Path)
    rtl_cmd.add_argument("--rtl", type=Path, default=DEFAULT_RTL)
    rtl_cmd.add_argument("--words", type=Path, default=DEFAULT_WORDS)

    sites_cmd = sub.add_parser("sites", help="Resolve candidates into switch-sites.csv and candidates_compiled.csv")
    sites_cmd.add_argument("signal_map", type=Path)
    sites_cmd.add_argument("out", type=Path)
    sites_cmd.add_argument("--words", type=Path, default=DEFAULT_WORDS)
    selection = sites_cmd.add_mutually_exclusive_group(required=True)
    selection.add_argument("--sites", type=Path, help="Existing candidate CSV with a path column")
    selection.add_argument("--count", type=int, help="Sample N single-bit mesh candidates")
    selection.add_argument("--all", action="store_true", help="Include all eligible single-bit mesh candidates")
    sites_cmd.add_argument("--seed", type=int, default=20261119)

    args = parser.parse_args()
    try:
        if args.command == "rtl":
            total = prepare_universal_rtl(args.rtl, args.words, args.out)
            print(f"Prepared universal Mesh RTL covering {total} candidate words in {args.out}")
        else:
            total = prepare_sites(
                args.signal_map, args.words, args.out,
                sites_path=args.sites, count=args.count, seed=args.seed, all_bits=args.all,
            )
            print(f"Prepared {total} screening candidates in {args.out}")
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
