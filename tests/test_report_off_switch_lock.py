"""Verify lock report coverage, grant mapping, and cold-trial comparisons."""

import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify.report_lock import CYCLES, FORCE_FIELDS, SWITCHES, WORKLOADS, read_force_cases, read_log, read_sites, report, switch_score


def write_table(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def result(workload, bit, cycle):
    stock = 8 if cycle == CYCLES[0] else 67
    wrong = int(bit >= 0)
    returned = stock - wrong
    return dict(workload=workload, lock_bit=bit, cycles=cycle, stock_tiles=stock,
                returned_tiles=returned, correct_tiles=returned - wrong, wrong_tiles=wrong,
                wrong_bytes=2 * wrong, unknown_bytes=wrong, missing_tiles=wrong,
                missing_bytes=256 * wrong, stock_bytes=256 * stock, failed_bytes=258 * wrong,
                invalid_request_cycle=0, masked_active_cycles=0)


def write_log(root, workload, bit):
    rows = [result(workload, bit, cycle) for cycle in CYCLES]
    lines = ["LOCK_RESULT " + " ".join(f"{name}={value}" for name, value in row.items()) for row in rows]
    name = f"workload-{workload}-lock-{bit}"
    path = root / f"{name}.log"
    path.write_text("\n".join([*lines, "OFF_SWITCH_LOCK PASS", ""]))
    (root / f"{name}.exitcode").write_text("0\n")
    return path


def site_rows():
    return [dict(rank=bit + 1, width=8, path=f"ex_controller.mesh.mesh.word{bit}",
                 catalog_id=100 + bit, mesh_id=10 + bit) for bit in range(SWITCHES)]


class OffSwitchLockReportTest(unittest.TestCase):
    def test_full_report_keeps_physical_counts_when_force_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection = root / "sites.csv"
            force = root / "cold.csv"
            dest = root / "report"
            sites = site_rows()
            write_table(selection, sites)
            write_table(root / "switch-sites.csv", [dict(grant_bit=bit, **site)
                                                   for bit, site in enumerate(sites)])
            force_rows = []
            for workload in range(len(WORKLOADS)):
                for bit in range(-1, SWITCHES):
                    write_log(root, workload, bit)
                    if bit < 0:
                        continue
                    short = result(workload, bit, CYCLES[0])
                    short["known_wrong_bytes"] = short["wrong_bytes"] - short["unknown_bytes"]
                    force_rows.append(dict(**sites[bit], case=workload,
                                           **{name: short[field] for field, name in FORCE_FIELDS.items()}))
            force_rows[0]["corrupt_bytes"] += 1
            write_table(force, force_rows)
            for name in ("compile", "run"):
                (root / f"{name}.exitcode").write_text("0\n")

            self.assertEqual(report(root, selection, force, dest), 1)
            summary = (dest / "summary.md").read_text()
            self.assertIn("8 / 8 | 67 / 67", summary)
            self.assertIn("known wrong bytes 2 → 1", summary)
            self.assertIn(f"{len(WORKLOADS)} missing tiles ({len(WORKLOADS) * 256:,} bytes) and {len(WORKLOADS)} known wrong returned bytes", summary)
            with (dest / "cases.csv").open(newline="") as stream:
                cases = list(csv.DictReader(stream))
            self.assertEqual(len(cases), (SWITCHES + 1) * len(WORKLOADS) * len(CYCLES))
            differing = next(row for row in cases if row["force_differences"])
            self.assertEqual(differing["known_wrong_bytes"], "1")

    def test_rejects_wrong_mapping_and_duplicate_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sites = site_rows()
            selection = root / "sites.csv"
            inserted = root / "switch-sites.csv"
            write_table(selection, sites)
            mapped = [dict(grant_bit=bit, **site) for bit, site in enumerate(sites)]
            mapped[3]["path"] = sites[4]["path"]
            write_table(inserted, mapped)
            with self.assertRaisesRegex(ValueError, "grant bit 3"):
                read_sites(selection, inserted)

            log = write_log(root, 0, 0)
            lines = log.read_text().splitlines()
            log.write_text("\n".join([lines[0], *lines]))
            with self.assertRaisesRegex(ValueError, "duplicate result at cycle 1767"):
                read_log(root, 0, 0)

    def test_distinct_packed_bits_share_parent_and_match_cold_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = "ex_controller.mesh.mesh.bus"
            sites = [dict(site, width=1, word_width=1, bit_index="") for site in site_rows()]
            for bit in (0, 1):
                sites[bit].update(path=f"{parent}[{bit}]", catalog_id=100, word_width=8, bit_index=bit)
            selection = root / "sites.csv"
            inserted = root / "switch-sites.csv"
            force = root / "cold.csv"
            write_table(selection, sites)
            mapped = [dict(grant_bit=bit, **site) for bit, site in enumerate(sites)]
            write_table(inserted, mapped)
            validated = read_sites(selection, inserted)
            force_rows = [dict(site, case=case, **dict.fromkeys(FORCE_FIELDS.values(), 0))
                          for site in sites for case in range(len(WORKLOADS))]
            write_table(force, force_rows)
            self.assertEqual(len(read_force_cases(force, validated)), SWITCHES * len(WORKLOADS))

            force_rows[0]["bit_index"] = 1
            write_table(force, force_rows)
            with self.assertRaisesRegex(ValueError, "mismatched or duplicate cold trial"):
                read_force_cases(force, validated)

            sites[1]["path"] = mapped[1]["path"] = "ex_controller.mesh.mesh.other_bus[1]"
            write_table(selection, sites)
            write_table(inserted, mapped)
            with self.assertRaisesRegex(ValueError, "invalid packed-bit identity for grant bit 1"):
                read_sites(selection, inserted)

    def test_switch_score_ranks_worst_case_failure_then_overall_then_correct_tiles(self):
        def case(correct, missing_tiles, wrong_bytes, stock_tiles=8):
            return dict(
                correct_tiles=correct,
                stock_tiles=stock_tiles,
                missing_bytes=missing_tiles * 256,
                known_wrong_bytes=wrong_bytes,
                stock_bytes=stock_tiles * 256,
            )

        immediate_deadlock = switch_score([case(0, 8, 0), case(0, 7, 0, stock_tiles=7)])
        deadlock_after_one = switch_score([case(1, 7, 0), case(1, 6, 0, stock_tiles=7)])
        column_kill_high_tot = switch_score([case(0, 0, 128), case(0, 0, 160)])
        column_kill_low_tot = switch_score([case(0, 0, 128), case(0, 0, 128)])
        column_kill_more_corr = switch_score([case(1, 0, 128), case(0, 0, 128)])
        bypassed_on_one = switch_score([case(0, 0, 2048), case(8, 0, 0)])

        self.assertLess(immediate_deadlock, deadlock_after_one)
        self.assertLess(deadlock_after_one, column_kill_high_tot)
        self.assertLess(column_kill_high_tot, column_kill_low_tot)
        self.assertLess(column_kill_low_tot, column_kill_more_corr)
        self.assertLess(column_kill_more_corr, bypassed_on_one)


if __name__ == "__main__":
    unittest.main()
