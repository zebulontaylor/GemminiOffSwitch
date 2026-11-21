#!/usr/bin/env python3
"""Validate the individual grant-lock regression and report its output counts."""

import argparse
import csv
import gzip
import re
from pathlib import Path


WORKLOADS = (
    "Dense pseudorandom inputs from −2 through 2",
    "Dense nonzero inputs drawn from −2, −1, 1 and 2",
    "Signed permutation matrix times a full-range signed-byte matrix",
    "Dense inputs from −8 through 7, exercising output saturation",
)
CYCLES = (1767, 14001)
SWITCHES = 32
TILE_BYTES = 256
IDENTITY = ("rank", "path", "mesh_id", "catalog_id")
BIT_FIELDS = ("word_width", "bit_index")
RESULT_FIELDS = (
    "workload", "lock_bit", "cycles", "stock_tiles", "returned_tiles", "correct_tiles",
    "wrong_tiles", "wrong_bytes", "unknown_bytes", "missing_tiles", "missing_bytes",
    "stock_bytes", "failed_bytes", "invalid_request_cycle", "masked_active_cycles",
)
FORCE_FIELDS = {
    "stock_tiles": "golden_tiles_after_inject",
    "returned_tiles": "faulty_tiles_after_inject",
    "correct_tiles": "successful_tiles",
    "wrong_tiles": "wrong_tiles",
    "missing_tiles": "missing_tiles",
    "missing_bytes": "missing_bytes",
    "stock_bytes": "expected_bytes",
    "known_wrong_bytes": "corrupt_bytes",
    "unknown_bytes": "unknown_elements",
}


def read_table(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def read_sites(selection_path, inserted_path):
    selection = read_table(selection_path)
    inserted = sorted(read_table(inserted_path), key=lambda row: int(row["grant_bit"]))
    if len(selection) != SWITCHES or len(inserted) != SWITCHES:
        raise ValueError(f"Expected {SWITCHES} rows in both site manifests")
    if any(len({row[key] for row in selection}) != SWITCHES for key in ("path", "mesh_id")):
        raise ValueError(f"{selection_path}: duplicate switch path or mesh ID")
    bit_mode = any(field in row for row in (selection[0], inserted[0]) for field in BIT_FIELDS)
    identity = (*IDENTITY, *BIT_FIELDS) if bit_mode else IDENTITY
    for bit, (selected, site) in enumerate(zip(selection, inserted, strict=True)):
        width = int(site["width"])
        if (int(site["grant_bit"]) != bit or int(selected["rank"]) != bit + 1
                or any(key not in site or key not in selected or site[key] != selected[key]
                       for key in identity)
                or width != int(selected["width"]) or width <= 0):
            raise ValueError(f"{inserted_path}: grant bit {bit} does not match selection row {bit + 1}")
    if bit_mode:
        validate_bit_sites(inserted_path, inserted)
    return inserted


def validate_bit_sites(path, sites):
    parents = {}
    parent_ids = {}
    for site in sites:
        catalog_id = int(site["catalog_id"])
        word_width = int(site["word_width"])
        index = site["bit_index"]
        parent_path = site["path"]
        match = re.fullmatch(r"(.+)\[(0|[1-9][0-9]*)\]", parent_path)
        if index == "":
            valid = word_width == 1 and match is None
        else:
            valid = (match is not None and match[2] == index
                     and word_width > 1 and int(index) < word_width)
            if match is not None:
                parent_path = match[1]
        parent = (parent_path, word_width)
        if (int(site["width"]) != 1 or catalog_id < 0 or not valid
                or parents.setdefault(catalog_id, parent) != parent
                or parent_ids.setdefault(parent_path, catalog_id) != catalog_id):
            raise ValueError(f"{path}: invalid packed-bit identity for grant bit {site['grant_bit']}")


def require_success(directory, name):
    path = directory / f"{name}.exitcode"
    rc = int(path.read_text())
    if rc:
        raise ValueError(f"{path}: exit code {rc}")


def read_log(directory, workload, bit, checkpoints=CYCLES):
    name = f"workload-{workload}-lock-{bit}"
    require_success(directory, name)
    path = directory / f"{name}.log"
    if path.is_file():
        log = path.read_text()
    else:
        path = directory / f"{name}.log.gz"
        with gzip.open(path, "rt") as stream:
            log = stream.read()

    rows = {}
    passed = False
    for line in log.splitlines():
        if line == "OFF_SWITCH_LOCK PASS":
            if passed or tuple(rows) != checkpoints:
                raise ValueError(f"{path}: misplaced or duplicate PASS marker")
            passed = True
        if not line.startswith("LOCK_RESULT "):
            continue
        pairs = [field.split("=", 1) for field in line.split()[1:]]
        if sorted(key for key, _ in pairs) != sorted(RESULT_FIELDS):
            raise ValueError(f"{path}: unexpected or duplicate result fields")
        row = {key: int(value) for key, value in pairs}
        cycle = row["cycles"]
        if (passed or row["workload"] != workload or row["lock_bit"] != bit
                or cycle in rows or cycle not in checkpoints):
            raise ValueError(f"{path}: unexpected or duplicate result at cycle {cycle}")
        validate_result(path, row, cycle, bit)
        row["known_wrong_bytes"] = row["wrong_bytes"] - row["unknown_bytes"]
        rows[cycle] = row

    if not passed:
        raise ValueError(f"{path}: missing PASS marker")
    if len(checkpoints) == 1:
        return rows
    early, late = (rows[cycle] for cycle in checkpoints)
    cumulative_fields = (
        "stock_tiles", "returned_tiles", "correct_tiles", "wrong_tiles", "wrong_bytes",
        "unknown_bytes", "masked_active_cycles",
    )
    if any(early[field] > late[field] for field in cumulative_fields):
        raise ValueError(f"{path}: cumulative counts decreased between checkpoints")
    request_cycle = late["invalid_request_cycle"]
    if early["invalid_request_cycle"] != (request_cycle if request_cycle <= checkpoints[0] else 0):
        raise ValueError(f"{path}: first invalid request changed between checkpoints")
    return rows


def validate_result(path, row, cycle, bit):
    stock = row["stock_tiles"]
    returned = row["returned_tiles"]
    wrong_tiles = row["wrong_tiles"]
    wrong_bytes = row["wrong_bytes"]
    missing = max(0, stock - returned)
    if (stock < 2 or returned < 0
            or not 0 <= wrong_tiles <= returned
            or not wrong_tiles <= wrong_bytes <= wrong_tiles * TILE_BYTES
            or not 0 <= row["unknown_bytes"] <= wrong_bytes
            or row["correct_tiles"] != returned - wrong_tiles
            or row["missing_tiles"] != missing or row["missing_bytes"] != missing * TILE_BYTES
            or row["stock_bytes"] != stock * TILE_BYTES
            or row["failed_bytes"] != wrong_bytes + row["missing_bytes"]
            or not 0 <= row["invalid_request_cycle"] <= cycle
            or not 0 <= row["masked_active_cycles"] <= cycle):
        raise ValueError(f"{path}: inconsistent result counts at cycle {cycle}")
    if bit == -1 and (returned != stock or wrong_tiles or row["invalid_request_cycle"]
                      or row["masked_active_cycles"]):
        raise ValueError(f"{path}: all-granted control failed at cycle {cycle}")


def read_force_cases(path, sites):
    selected = {site["rank"]: site for site in sites}
    identity = (*IDENTITY, "width", *BIT_FIELDS) if "bit_index" in sites[0] else IDENTITY
    cases = {}
    for row in read_table(path):
        if (site := selected.get(row["rank"])) is None:
            continue
        workload = int(row["case"])
        bit = int(site["grant_bit"])
        case = (bit, workload)
        if (any(row.get(key) != site[key] for key in identity) or case in cases
                or not 0 <= workload < len(WORKLOADS)):
            raise ValueError(f"{path}: mismatched or duplicate cold trial for grant bit {bit}, workload {workload}")
        cases[case] = {field: int(row[source]) for field, source in FORCE_FIELDS.items()}
    if len(cases) != SWITCHES * len(WORKLOADS):
        raise ValueError(f"{path}: missing cold trials for selected sites")
    return cases


def collect(directory, sites, force_cases):
    require_success(directory, "compile")
    require_success(directory, "run")
    rows = {}
    for workload in range(len(WORKLOADS)):
        control = read_log(directory, workload, -1)
        rows[-1, workload] = control
        for bit, site in enumerate(sites):
            results = read_log(directory, workload, bit)
            for cycle, row in results.items():
                if row["stock_tiles"] != control[cycle]["stock_tiles"]:
                    raise ValueError(f"Stock lanes disagree at cycle {cycle} for bit {bit}, workload {workload}")
                row.update({key: site[key] for key in (*IDENTITY, *BIT_FIELDS) if key in site})
                if cycle == CYCLES[0]:
                    force = force_cases[bit, workload]
                    row.update({f"force_{key}": value for key, value in force.items()})
                    row["force_differences"] = " ".join(key for key, value in force.items() if row[key] != value)
            rows[bit, workload] = results
    return rows


def switch_score(cases):
    return (
        max(row["correct_tiles"] for row in cases),
        -sum(row["missing_tiles"] for row in cases),
        -sum(row["known_wrong_bytes"] for row in cases),
    )


def render_report(rows, sites, screen=False):
    lines = [
        "# Individual grant locks in the inserted Gemmini RTL", "",
        "Each VCS run started from reset with one physical grant held low and the other 31 grants high. "
        "An all-granted control and a stock reference ran the same signed 16×16 matrix products. "
        "A correct tile has all 256 bytes equal to the arithmetic result. The stock lane's completed "
        "tiles define the expected count for each workload and checkpoint. Each missing tile accounts "
        "for 256 missing bytes. Known wrong bytes count mismatches with a known returned value. "
        "Unknown bytes also make a tile incorrect.", "",
        "| Workload | Operands | " + " | ".join(f"Cycle {cycle:,} expected / all granted correct" for cycle in CYCLES) + " |",
        "|---:|---|" + "---:|" * len(CYCLES),
    ]
    for workload, operands in enumerate(WORKLOADS):
        control = rows[-1, workload]
        counts = " | ".join(f"{control[cycle]['stock_tiles']} / {control[cycle]['correct_tiles']}" for cycle in CYCLES)
        lines.append(f"| {workload} | {operands} | {counts} |")

    labels = [f"Workload {workload}" for workload in range(len(WORKLOADS))]
    for cycle in CYCLES:
        lines.extend([
            "", f"## Correct tiles at cycle {cycle:,}", "",
            "| Grant bit | Rank | Path | " + " | ".join(labels)
            + " | Missing bytes | Known wrong bytes |",
            "|---:|---:|---|" + "---:|" * (len(WORKLOADS) + 2),
        ])
        scores = {}
        for bit, site in enumerate(sites):
            cases = [rows[bit, workload][cycle] for workload in range(len(WORKLOADS))]
            scores[bit] = switch_score(cases)
            counts = " | ".join(str(row["correct_tiles"]) for row in cases)
            missing = sum(row["missing_bytes"] for row in cases)
            wrong = sum(row["known_wrong_bytes"] for row in cases)
            lines.append(f"| {bit} | {site['rank']} | `{site['path']}` | {counts} | {missing:,} | {wrong:,} |")

        weakest = max(scores.values())
        tied = [bit for bit, score in scores.items() if score[0] == weakest[0]]
        winners = [bit for bit in tied if scores[bit] == weakest]
        lines.extend(["", f"The largest correct-tile count in any one workload was **{weakest[0]}**. "
                      f"{len(tied)} of the {len(sites)} locks reached that count."])
        if len(winners) > 1:
            bits = ", ".join(map(str, winners))
            lines.extend(["", f"Grant bits {bits} tied on all three measured criteria. Each lock "
                          f"had {-weakest[1]:,} missing tiles ({-weakest[1] * TILE_BYTES:,} bytes) "
                          f"and {-weakest[2]:,} known wrong returned bytes."])
        else:
            bit = winners[0]
            locked = [rows[bit, workload][cycle] for workload in range(len(WORKLOADS))]
            counts = ", ".join(f"{r['correct_tiles']}/{r['stock_tiles']}" for r in locked)
            lines.extend(["", f"**Grant bit {bit} was weakest by the full ranking (selection rank "
                          f"{sites[bit]['rank']}).** Correct tiles by workload "
                          f"(0 through {len(WORKLOADS) - 1}) were {counts}. Across those workloads, "
                          f"{-weakest[1]:,} tiles were missing and {-weakest[2]:,} returned bytes "
                          "were known wrong."])

    differences = [results[CYCLES[0]] for (bit, _), results in rows.items()
                   if bit >= 0 and results[CYCLES[0]]["force_differences"]]
    comparison = "single-bit screen" if screen else "individual cold force trials"
    method = ("The screen tested one grant at a time in the larger Verilator model. "
              if screen else "The cold trials used a simulator force before matrix traffic. ")
    source = "screen" if screen else "force"
    lines.extend([
        "", f"## Comparison with {comparison} at cycle {CYCLES[0]:,}", "",
        method + "The selected 32-switch RTL held one grant low from reset. "
        f"The two methods differed in {len(differences)} of "
        f"{SWITCHES * len(WORKLOADS)} workload and switch pairs. The counts above come from the inserted RTL.",
    ])
    if differences:
        lines.extend(["", f"| Grant bit | Workload | Differing counts ({source} → inserted RTL) |", "|---:|---:|---|"])
        for row in differences:
            changes = ", ".join(f"{key.replace('_', ' ')} {row[f'force_{key}']} → {row[key]}"
                                for key in row["force_differences"].split())
            lines.append(f"| {row['lock_bit']} | {row['workload']} | {changes} |")
    lines.extend([
        "", f"[Per-run counts and {source} comparisons](cases.csv) · [Grant bit mapping](switch-sites.csv)", "",
    ])
    return "\n".join(lines)


def report(directory, selection, force, output, screen=False):
    inserted = directory / "switch-sites.csv"
    sites = read_sites(selection, inserted)
    rows = collect(directory, sites, read_force_cases(force, sites))
    output.mkdir(parents=True, exist_ok=True)
    fields = [
        *IDENTITY, *(field for field in BIT_FIELDS if field in sites[0]), *RESULT_FIELDS, "known_wrong_bytes",
        *(f"force_{key}" for key in FORCE_FIELDS), "force_differences",
    ]
    with (output / "cases.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for case in sorted(rows):
            writer.writerows(rows[case][cycle] for cycle in CYCLES)
    (output / "switch-sites.csv").write_bytes(inserted.read_bytes())
    (output / "summary.md").write_text(render_report(rows, sites, screen))
    return sum(bool(results[CYCLES[0]].get("force_differences")) for results in rows.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True, help="Ranked selection sites.csv")
    comparison = parser.add_mutually_exclusive_group(required=True)
    comparison.add_argument("--force-cases", type=Path, help="Cold force ranking cases.csv")
    comparison.add_argument("--screen-cases", type=Path, help="Inserted single-bit screen cases.csv")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        differences = report(args.logs, args.selection, args.force_cases or args.screen_cases, args.output,
                             screen=args.screen_cases is not None)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(f"Validated {len(WORKLOADS) * (SWITCHES + 1)} runs with {differences} differences from selection trials")


if __name__ == "__main__":
    main()
