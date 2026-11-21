#!/usr/bin/env python3
"""Elaborate Rocket and Gemmini with a signed-license mailbox for each array switch."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware_guard import ensure_guarded
from rtl.insert_off_switches import DEFAULT_SITES, GRANT, Module, connections, insert, load_sites
from rtl.license_mailbox import stage as stage_license_mailbox


ROOT = Path(__file__).resolve().parents[2]


def connect(source, instance):
    if any(port == GRANT for port, *_ in connections(instance)):
        raise ValueError(f"Instance {instance[2]} already connects {GRANT}")
    position = source.index("(", instance.start()) + 1
    return source[:position] + f"\n    .{GRANT} ({GRANT})," + source[position:]


def instance_of(module, child):
    matches = [instance for instance in module.instances.values() if instance[1] == child]
    if len(matches) != 1:
        raise ValueError(f"Expected one {child} instance in {module.header[1]}")
    return matches[0]


def route_grant(rtl, engines):
    # Move the elaboration-only output inside the system, then route it to Gemmini.
    # Editing the split RTL also lets the insertion pass use unmodified Gemmini sources.
    width = f"[{engines - 1}:0]"
    paths = [rtl / f"{name}.sv" for name in ("ExampleRocketSystem", "TilePRCIDomain", "RocketTile")]
    system, domain, tile = (Module(path.read_text()) for path in paths)
    system_source = connect(system.source, instance_of(system, "TilePRCIDomain"))
    packed_output = "" if engines == 1 else rf"\[{engines - 1}:0\]\s+"
    port = re.compile(rf",\s*output\s+{packed_output}{GRANT}\s*\);")
    system_source, count = port.subn(f"\n);\n  wire {width} {GRANT};", system_source)
    if count != 1:
        raise ValueError(f"Expected an elaboration output named {GRANT}")

    sources = [system_source]
    for module, child in ((domain, "RocketTile"), (tile, "Gemmini")):
        source = connect(module.source, instance_of(module, child))
        position = module.header.start(2)
        sources.append(source[:position] + f"\n  input {width} {GRANT}," + source[position:])
    for path, source in zip(paths, sources):
        path.write_text(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", nargs="?", type=Path, default=ROOT / "build/elab/gemmini-off-switch",
                        help="Empty output directory (default build/elab/gemmini-off-switch)")
    parser.add_argument("--sites", type=Path, default=DEFAULT_SITES, help="Ordered CSV with a path column")
    args = parser.parse_args()
    ensure_guarded()
    dest = args.dest.resolve()
    try:
        _, rows = load_sites(args.sites)
        engines = len(rows)
        if engines > 32:
            raise ValueError("The license MMIO aperture supports at most 32 array switches")
        if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
            raise ValueError("The output directory must be empty")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".gemmini-license-", dir=dest.parent) as temp:
            original = Path(temp) / "original"
            integrated = Path(temp) / "integrated"
            env = dict(os.environ, GEMMINI_LICENSE_ENGINES=str(engines), GEMMINI_ELAB_OUT=str(original))
            subprocess.run([ROOT / "scripts/rtl/elaborate_gemmini.sh"], env=env, check=True)
            insert(original, integrated, args.sites)
            route_grant(integrated, engines)
            stage_license_mailbox(integrated / "license")
            filelist = integrated / "filelist.f"
            filelist.write_text("-F license/license_mailbox.vc\n" + filelist.read_text())
            integrated.rename(dest)
        print(f"Wrote {dest} with {engines} independent license mailboxes")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(exc.returncode, f"{parser.prog}: {exc}\n")


if __name__ == "__main__":
    main()
