#!/usr/bin/env python3
"""Stage the ECDSA license mailbox RTL and generate deterministic test fixtures."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = ROOT / "off-switch" / "verilog" / "rtl"
MAILBOX = ROOT / "infra" / "license_mailbox"
UPSTREAM_SOURCES = (
    "arith_pkg.sv",
    "comb_add.sv",
    "mod_add.sv",
    "mod_mul.sv",
    "mod_inv.sv",
    "arith.sv",
    "trng.sv",
    "base_pkg.sv",
    "ecdsa_pkg.sv",
    "ecdsa.sv",
    "hss_pkg.sv",
    "security_block.sv",
)
BRIDGE = "off_switch_license_mailbox.sv"

CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
GENERATOR_X = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798


def stage(output_dir, ecdsa_blackbox=False):
    output_dir = Path(output_dir).resolve()
    if output_dir == UPSTREAM:
        raise ValueError("Choose an output directory outside the off-switch RTL source directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in UPSTREAM_SOURCES:
        shutil.copyfile(UPSTREAM / source, output_dir / source)
    shutil.copyfile(MAILBOX / BRIDGE, output_dir / BRIDGE)
    subprocess.run(
        ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(MAILBOX / "off_switch.patch")],
        cwd=output_dir,
        stdout=sys.stderr,
        check=True,
    )
    if ecdsa_blackbox:
        ecdsa = output_dir / "ecdsa.sv"
        header, close, _ = ecdsa.read_text().partition("\n);\n")
        if not close:
            raise ValueError("Could not locate the end of the ECDSA port declaration")
        ecdsa.write_text(f"{header}{close}endmodule\n")
    sources = "".join(f"{source}\n" for source in (*UPSTREAM_SOURCES, BRIDGE))
    (output_dir / "license_mailbox.vc").write_text(sources)
    return sources


def reset_nonce(instance_id):
    lfsr = 1
    # The first request samples the state after 101 clocks out of reset.
    for _ in range(101):
        feedback = (lfsr >> 255) ^ (lfsr >> 253) ^ (lfsr >> 250) ^ (lfsr >> 245)
        lfsr = ((lfsr << 1) | (feedback & 1)) & ((1 << 256) - 1)
    return ((0x4F530000 | instance_id) << 224) | (lfsr & ((1 << 224) - 1))


def sign_fixture(nonce, key):
    # The published prototype keys and fixed signing scalar k=1 make these
    # reproducible test fixtures.
    r = GENERATOR_X
    s = (nonce + r * key) % CURVE_ORDER
    return (r << 256) | s


def _cmd_stage(args, parser):
    try:
        print(stage(args.output_dir, ecdsa_blackbox=args.ecdsa_blackbox), end="")
    except ValueError as exc:
        parser.error(str(exc))


def _cmd_vectors(args, parser):
    nonce = reset_nonce(args.instance) if args.nonce is None else args.nonce
    if not 0 <= nonce < (1 << 256):
        parser.error("Nonce must be an unsigned 256-bit value")
    print(json.dumps({
        "nonce": f"{nonce:064x}",
        "signer0": f"{sign_fixture(nonce, 2):0128x}",
        "signer1": f"{sign_fixture(nonce, 3):0128x}",
        "invalid": f"{sign_fixture(nonce, 4):0128x}",
    }, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage_parser = subparsers.add_parser("stage", help="Stage mailbox RTL and license_mailbox.vc")
    stage_parser.add_argument("output_dir", type=Path)
    stage_parser.add_argument(
        "--ecdsa-blackbox", action="store_true",
        help="Stage only the ECDSA port declaration for partitioned area mapping",
    )
    stage_parser.set_defaults(func=_cmd_stage)

    vec_parser = subparsers.add_parser("vectors", help="Print test nonce and signatures as JSON")
    vec_parser.add_argument("instance", type=int, choices=range(32))
    vec_parser.add_argument("--nonce", type=lambda value: int(value, 16),
                            help="Sign this hexadecimal nonce instead of the reset nonce")
    vec_parser.set_defaults(func=_cmd_vectors)

    args = parser.parse_args()
    args.func(args, parser)


if __name__ == "__main__":
    main()
