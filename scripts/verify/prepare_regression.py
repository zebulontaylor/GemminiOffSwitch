#!/usr/bin/env python3
"""Bind the original and switched Gemmini designs to the same workload."""

import argparse
import csv
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtl.insert_off_switches import SITE_PATH, unselected_slices
from rtl.license_mailbox import reset_nonce, sign_fixture


def write_ports(rtl, dest, *, port_bindings=None):
    header = (rtl / "Gemmini.sv").read_text().split("module Gemmini(", 1)[1].split(");", 1)[0]
    bindings = {
        "clock": "clock", "reset": "reset", "io_busy": "io_busy", "io_interrupt": "io_interrupt",
        "io_cmd_ready": "io_cmd_ready", "io_cmd_valid": "io_cmd_valid",
        "io_cmd_bits_inst_funct": "io_cmd_bits_inst_funct", "io_cmd_bits_inst_opcode": "7'h7b",
        "io_cmd_bits_inst_xs1": "1'b1", "io_cmd_bits_inst_xs2": "1'b1",
        "io_cmd_bits_rs1": "io_cmd_bits_rs1", "io_cmd_bits_rs2": "io_cmd_bits_rs2",
        "io_cmd_bits_status_prv": "2'd3", "io_cmd_bits_status_dprv": "2'd3",
        "io_cmd_bits_status_mpp": "2'd3", "io_resp_ready": "1'b1", "io_ptw_0_req_ready": "1'b1",
    }
    # The elaborated accelerator issues unprivileged physical DMA requests.
    # Permit its 64 KiB test RAM with one R/W NAPOT region.
    bindings.update({"io_ptw_0_pmp_0_cfg_a": "2'd3", "io_ptw_0_pmp_0_cfg_r": "1'b1",
                     "io_ptw_0_pmp_0_cfg_w": "1'b1", "io_ptw_0_pmp_0_addr": "30'h20001fff",
                     "io_ptw_0_pmp_0_mask": "32'hffff"})
    tl = {"a_ready": "a_ready", "a_valid": "a_valid", "a_bits_opcode": "a_opcode",
          "a_bits_source": "a_source", "a_bits_size": "a_size", "a_bits_address": "a_addr",
          "a_bits_data": "a_data", "a_bits_mask": "a_mask", "d_ready": "d_ready",
          "d_valid": "d_valid", "d_bits_opcode": "d_is_read ? 3'd1 : 3'd0",
          "d_bits_source": "d_source", "d_bits_size": "d_size", "d_bits_data": "d_data"}
    bindings.update({f"auto_spad_id_out_{name}": value for name, value in tl.items()})
    if port_bindings:
        bindings.update(port_bindings)
    lines = []
    direction = None
    for item in header.split(","):
        match = re.fullmatch(r"\s*(?:(input|output)\s+)?(?:\[\d+:\d+\]\s+)?(\w+)\s*", item)
        if not match:
            raise ValueError(f"Unsupported port declaration {item}")
        explicit, name = match.groups()
        direction = explicit or direction
        value = bindings.pop(name, "'0" if direction == "input" else "")
        lines.append(f"  .{name}({value})")
    if bindings:
        raise ValueError(f"Bindings missing from elaboration: {bindings}")
    (dest / "dut_ports.svh").write_text("Gemmini dut (\n" + ",\n".join(lines) + "\n);\n")


def prepare(original, switched, dest):
    dest.mkdir(parents=True, exist_ok=True)
    source = (original / "Gemmini.sv").read_text()
    (dest / "GemminiReference.sv").write_text(
        source.replace("module Gemmini(", "module GemminiReference(", 1)
    )
    write_ports(original, dest)
    ports = dest / "dut_ports.svh"
    (dest / "reference_ports.svh").write_text(
        ports.read_text().replace("Gemmini dut", "GemminiReference dut", 1)
    )
    with (switched / "switch-sites.csv").open(newline="") as manifest:
        rows = list(csv.DictReader(manifest))
    packed = {}
    for row in rows:
        path = row["path"]
        match = SITE_PATH.fullmatch(path)
        if not match:
            raise ValueError(f"Unsupported switch path {path!r}")
        if match["index"] is None:
            continue
        bounds = row.get("packed_range", "")
        if not re.fullmatch(r"[0-9]+:[0-9]+", bounds):
            raise ValueError(f"Missing or invalid packed range for {path}")
        left, right = map(int, bounds.split(":"))
        index = int(match["index"])
        packed_left, packed_right, selected = packed.setdefault(match["signal"], (left, right, set()))
        if (left, right) != (packed_left, packed_right) or not min(left, right) <= index <= max(left, right):
            raise ValueError(f"Inconsistent packed range for {path}")
        selected.add(index)
    write_ports(switched, dest, port_bindings={
        "off_switch_grant": "cycle_tb.grant",
    })

    nonce_values = [reset_nonce(index) for index in range(len(rows))]
    (dest / "license-nonces.hex").write_text("".join(f"{nonce:064x}\n" for nonce in nonce_values))
    (dest / "license-signatures.hex").write_text("".join(
        f"{sign_fixture(nonce, key):0128x}\n"
        for nonce in nonce_values for key in (2, 3)
    ))

    (dest / "off_switch_count.svh").write_text(f"localparam int SWITCH_COUNT = {len(rows)};\n")
    checks = [
        "bit [SWITCH_COUNT-1:0] saw_active;",
        "task automatic check_switched_signals;",
    ]
    for row in rows:
        index = int(row["grant_bit"])
        path = row["path"]
        reference = f"gold.reference.dut.{path}"
        protected = f"fault.protected_lane.dut.{path}"
        checks.extend([
            "  if (!reset && (&grant)) begin",
            f"    if ({protected} !== {reference})",
            f'      $fatal(1, "Grant changed {path} at cycle %0d", cycles);',
            f"    if (!$isunknown({reference}) && {reference} != '0)",
            f"      saw_active[{index}] = 1;",
            f"  end else if (grant[{index}] === 1'b0 && {protected} !== '0)",
            f'    $fatal(1, "Denial did not zero {path} at cycle %0d", cycles);',
        ])
    checks += ["endtask", "`ifdef OFF_SWITCH_DIRECT",
               "bit [SWITCH_COUNT-1:0] pulsed_active;",
               "`ifdef OFF_SWITCH_LOCK",
               "wire [SWITCH_COUNT-1:0] raw_active;",
               "`endif",
               "task automatic check_gate_values;"]
    for path, (left, right, selected) in packed.items():
        parent, signal = path.rsplit(".", 1)
        gated = f"fault.protected_lane.dut.{path}"
        raw = f"fault.protected_lane.dut.{parent}.off_switch_raw_{signal}"
        for selector in unselected_slices(left, right, selected):
            checks += [
                f"  if ({gated}{selector} !== {raw}{selector})",
                f'    $fatal(1, "Grant changed unclamped sibling bits at {path}{selector}");',
            ]
    for row in rows:
        index = int(row["grant_bit"])
        path = row["path"]
        parent, signal = path.rsplit(".", 1)
        gated = f"fault.protected_lane.dut.{path}"
        raw = f"fault.protected_lane.dut.{parent}.off_switch_raw_{signal}"
        checks += [
            f"  if ({gated} !== (direct_grant[{index}] ? {raw} : '0))",
            f'    $fatal(1, "Grant bit {index} routed incorrectly at {path}");',
        ]
    checks += ["endtask", "`ifdef OFF_SWITCH_LOCK"]
    for row in rows:
        index = int(row["grant_bit"])
        parent, signal = row["path"].rsplit(".", 1)
        raw = f"fault.protected_lane.dut.{parent}.off_switch_raw_{signal}"
        checks.append(f"assign raw_active[{index}] = ((|{raw}) === 1'b1);")
    checks += ["`endif", "task automatic pulse_active_switches;"]
    for row in rows:
        index = int(row["grant_bit"])
        path = row["path"]
        gated = f"fault.protected_lane.dut.{path}"
        checks += [
            f"  if (!pulsed_active[{index}] && !$isunknown({gated}) && {gated} != '0) begin",
            f"    direct_grant[{index}] = 0;",
            "    #0.001;",
            f"    if ({gated} !== '0)",
            f'      $fatal(1, "Grant bit {index} did not independently zero {path}");',
            "    check_gate_values();",
            f"    direct_grant[{index}] = 1;",
            "    #0.001;",
            f"    pulsed_active[{index}] = 1;",
            "  end",
        ]
    checks += ["endtask", "`endif"]
    (dest / "off_switch_checks.svh").write_text("\n".join(checks) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("switched", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    prepare(args.original, args.switched, args.dest)
