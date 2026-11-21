#!/usr/bin/env python3
"""Rank single-bit mesh locks from the local Verilator screen at cycle 1767."""

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtl.insert_off_switches import load_sites
from verify.report_lock import (
    BIT_FIELDS, CYCLES, FORCE_FIELDS, RESULT_FIELDS, SWITCHES, WORKLOADS,
    read_log, require_success, switch_score, validate_bit_sites, validate_result,
)


DEADLINE = CYCLES[0]
SITE_FIELDS = ("catalog_id", "path", "module", "kind", "width", *BIT_FIELDS)
TOTAL_COUNTS = ("golden_tiles_after_inject", "faulty_tiles_after_inject", "successful_tiles",
                "wrong_tiles", "missing_tiles", "corrupt_bytes", "unknown_elements", "expected_bytes")


def write_table(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_screen_sites(path):
    _, sites = load_sites(path)
    sites.sort(key=lambda site: int(site["grant_bit"]))
    if len(sites) < SWITCHES:
        raise ValueError(f"{path}: expected at least {SWITCHES} screen sites")
    if [int(site["grant_bit"]) for site in sites] != list(range(len(sites))):
        raise ValueError(f"{path}: expected contiguous screen grant bits starting at zero")
    mesh_ids = [int(site["id"]) for site in sites]
    if min(mesh_ids) < 0 or len(set(mesh_ids)) != len(sites):
        raise ValueError(f"{path}: expected distinct nonnegative mesh IDs")
    validate_bit_sites(path, sites)
    return sites


def read_results_table(path, site_count):
    rows = {}
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(RESULT_FIELDS):
            raise ValueError(f"{path}: unexpected result columns {reader.fieldnames}")
        for raw in reader:
            row = {key: int(raw[key]) for key in RESULT_FIELDS}
            if row["cycles"] != DEADLINE:
                continue
            workload = row["workload"]
            bit = row["lock_bit"]
            if not 0 <= workload < len(WORKLOADS) or not -1 <= bit < site_count:
                raise ValueError(f"{path}: out-of-range workload {workload} or lock bit {bit}")
            key = (workload, bit)
            if key in rows:
                raise ValueError(f"{path}: duplicate result for workload {workload}, lock bit {bit}")
            validate_result(path, row, DEADLINE, bit)
            row["known_wrong_bytes"] = row["wrong_bytes"] - row["unknown_bytes"]
            rows[key] = row
    expected = len(WORKLOADS) * (site_count + 1)
    if len(rows) != expected:
        raise ValueError(f"{path}: expected {expected} results at cycle {DEADLINE}, found {len(rows)}")
    return rows


def rank(logs, output):
    sites = read_screen_sites(logs / "switch-sites.csv")
    if not (logs / "model.txt").is_file() or (logs / "compile.exitcode").exists():
        require_success(logs, "compile")
    require_success(logs, "run")

    table_path = logs / "results.csv"
    table = read_results_table(table_path, len(sites)) if table_path.is_file() else None
    controls = {
        workload: (table[workload, -1] if table is not None else read_log(logs, workload, -1, (DEADLINE,))[DEADLINE])
        for workload in range(len(WORKLOADS))
    }
    scores = {}
    rankings = []
    trials = {}
    for site in sites:
        bit = int(site["grant_bit"])
        mesh_id = int(site["id"])
        identity = dict(mesh_id=mesh_id, **{field: site[field] for field in SITE_FIELDS})
        results = []
        cases = []
        for workload, control in controls.items():
            row = table[workload, bit] if table is not None else read_log(logs, workload, bit, (DEADLINE,))[DEADLINE]
            if row["stock_tiles"] != control["stock_tiles"]:
                raise ValueError(f"Stock lanes disagree at cycle {DEADLINE} for bit {bit}, workload {workload}")
            results.append(row)
            counts = {name: row[field] for field, name in FORCE_FIELDS.items()}
            cases.append(dict(**identity, case=workload, screen_grant_bit=bit, cycles=DEADLINE,
                              **counts, wrong_bytes=row["wrong_bytes"], failed_bytes=row["failed_bytes"],
                              invalid_request_cycle=row["invalid_request_cycle"],
                              masked_active_cycles=row["masked_active_cycles"]))
        score = switch_score(results)
        scores[mesh_id] = score
        trials[mesh_id] = cases
        totals = {f"total_{key}": sum(row[key] for row in cases) for key in TOTAL_COUNTS}
        rankings.append(dict(
            **identity,
            min_known_failed_pct=round(float(-score[0]) * 100, 4),
            total_known_failed_pct=round(float(-score[1]) * 100, 4),
            max_successful_tiles=max(row["correct_tiles"] for row in results),
            **totals,
        ))
    rankings.sort(key=lambda row: (*scores[row["mesh_id"]], row["mesh_id"]))
    ordered = [dict(rank=position, **row) for position, row in enumerate(rankings, 1)]
    selected = ordered[:SWITCHES]
    cases = [dict(rank=row["rank"], **trial) for row in ordered for trial in trials[row["mesh_id"]]]
    cutoff = selected[-1]
    cutoff_primary = [row for row in ordered if scores[row["mesh_id"]][0] == scores[cutoff["mesh_id"]][0]]

    summary = dict(
        cycles=DEADLINE,
        expected_tiles_by_workload={workload: row["stock_tiles"] for workload, row in controls.items()},
        tested_sites=len(ordered), selected_sites=len(selected), strongest=ordered[0], cutoff=cutoff,
        cutoff_primary_tied_mesh_ids=[row["mesh_id"] for row in cutoff_primary],
        cutoff_primary_selected=sum(row["rank"] <= SWITCHES for row in cutoff_primary),
        cutoff_tied_mesh_ids=[row["mesh_id"] for row in ordered if scores[row["mesh_id"]] == scores[cutoff["mesh_id"]]],
    )
    output.mkdir(parents=True, exist_ok=True)
    write_table(output / "sites.csv", selected)
    write_table(output / "ranking.csv", ordered)
    write_table(output / "cases.csv", cases)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "summary.md").write_text(render_summary(summary))
    return summary


def render_summary(summary):
    lines = [
        "# Single-bit mesh lock screen", "",
        f"Verilator tested {summary['tested_sites']} sites with one grant held low from reset in each trial. "
        f"The ranking uses output counts at cycle {summary['cycles']:,} and selects the first "
        f"{summary['selected_sites']} sites for insertion.", "",
        "Sites with the largest worst-case workload known failure fraction (missing plus known wrong bytes "
        "divided by expected bytes) rank first. Ties favor higher overall known failure fraction across "
        "workloads, then lower worst-case correct-tile fraction, then lower dense mesh ID. "
        "Unknown bytes make a tile incorrect and are excluded from known failed bytes.", "",
        "| Workload | All-granted correct tiles |", "|---:|---:|",
    ]
    for workload, tiles in summary["expected_tiles_by_workload"].items():
        lines.append(f"| {workload} | {tiles} |")
    lines.extend([
        "", "| Rank | Mesh ID | Path | Min failed % | Total failed % | Max correct tiles | Total missing tiles | Total known wrong bytes |",
        "|---:|---:|---|---:|---:|---:|---:|---:|",
    ])
    for row in (summary["strongest"], summary["cutoff"]):
        lines.append(
            f"| {row['rank']} | {row['mesh_id']} | `{row['path']}` | {row['min_known_failed_pct']:.2f}% | "
            f"{row['total_known_failed_pct']:.2f}% | {row['max_successful_tiles']} | "
            f"{row['total_missing_tiles']} | {row['total_corrupt_bytes']} |"
        )
    tied = len(summary["cutoff_primary_tied_mesh_ids"])
    selected = summary["cutoff_primary_selected"]
    min_failed = summary["cutoff"]["min_known_failed_pct"]
    lines.extend([
        "", f"At the cutoff, {tied} sites had a worst-case workload known failure rate of {min_failed:.2f}%. "
        f"The remaining criteria placed {selected} of those sites in the selection.", "",
        "[Selected sites](sites.csv) · [All ranks](ranking.csv) · [Individual workload counts](cases.csv)", "",
    ])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, required=True, help="Completed local Verilator screen directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = rank(args.logs, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(f"Selected {summary['selected_sites']} of {summary['tested_sites']} tested mesh sites")


if __name__ == "__main__":
    main()
