"""Check the screen ranking and validation of its completed trials."""

from pathlib import Path
import tempfile
import unittest

from test_report_off_switch_lock import result, write_table
from rtl.insert_off_switches import load_sites
from screen.rank import DEADLINE, rank
from verify.report_lock import RESULT_FIELDS, SWITCHES, WORKLOADS, read_table


def write_trial(logs, workload, bit, **changes):
    row = result(workload, bit, DEADLINE)
    row.update(changes)
    line = "LOCK_RESULT " + " ".join(f"{key}={value}" for key, value in row.items())
    name = f"workload-{workload}-lock-{bit}"
    (logs / f"{name}.log").write_text(f"{line}\nOFF_SWITCH_LOCK PASS\n")
    (logs / f"{name}.exitcode").write_text("0\n")


def write_screen(logs):
    logs.mkdir()
    sites = [
        dict(grant_bit=bit, id=500 - bit, catalog_id=1000 + bit, width=1, word_width=1,
             bit_index="", path=f"ex_controller.mesh.mesh.signal{bit}", kind="comb", module="Mesh")
        for bit in range(SWITCHES + 1)
    ]
    write_table(logs / "switch-sites.csv", list(reversed(sites)))
    for step in ("compile", "run"):
        (logs / f"{step}.exitcode").write_text("0\n")
    for workload in range(len(WORKLOADS)):
        for bit in range(-1, len(sites)):
            write_trial(logs, workload, bit)


class RankMeshLockScreenTest(unittest.TestCase):
    def test_rank_uses_known_wrong_and_dense_mesh_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            output = root / "selection"
            write_screen(logs)
            write_trial(logs, 0, 0, unknown_bytes=0)
            write_trial(logs, 0, 1, wrong_bytes=3, unknown_bytes=2, failed_bytes=259)
            write_trial(logs, 0, 2, returned_tiles=6, correct_tiles=5, missing_tiles=2,
                        missing_bytes=512, failed_bytes=514)
            write_trial(logs, 0, 3, returned_tiles=5, correct_tiles=4, missing_tiles=3,
                        missing_bytes=768, failed_bytes=770)
            for workload in range(1, len(WORKLOADS)):
                write_trial(logs, workload, 3, returned_tiles=6, correct_tiles=5, missing_tiles=2,
                            missing_bytes=512, failed_bytes=514)

            summary = rank(logs, output)
            self.assertEqual(summary["tested_sites"], SWITCHES + 1)
            self.assertEqual(summary["expected_tiles_by_workload"], dict.fromkeys(range(len(WORKLOADS)), 8))
            self.assertEqual(summary["strongest"]["mesh_id"], 497)
            self.assertEqual(summary["cutoff"]["rank"], SWITCHES)
            _, sites = load_sites(output / "sites.csv")
            self.assertEqual(len(sites), SWITCHES)
            self.assertNotIn("grant_bit", sites[0])
            self.assertNotIn("screen_grant_bit", sites[0])
            ranked = read_table(output / "ranking.csv")
            self.assertEqual([row["mesh_id"] for row in ranked[:4]], ["497", "498", "500", "468"])
            self.assertEqual(ranked[-1]["mesh_id"], "499")
            cases = read_table(output / "cases.csv")
            self.assertEqual(len(cases), (SWITCHES + 1) * len(WORKLOADS))
            self.assertEqual((cases[0]["mesh_id"], cases[0]["screen_grant_bit"]), ("497", "3"))

    def test_rejects_missing_failed_and_uncontrolled_trials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            output = root / "selection"
            write_screen(logs)
            trial = logs / "workload-0-lock-0.exitcode"
            trial.write_text("1\n")
            with self.assertRaisesRegex(ValueError, "workload-0-lock-0.exitcode: exit code 1"):
                rank(logs, output)
            trial.unlink()
            with self.assertRaisesRegex(FileNotFoundError, "workload-0-lock-0.exitcode"):
                rank(logs, output)
            write_trial(logs, 0, 0, stock_tiles=9, stock_bytes=2304, missing_tiles=2,
                        missing_bytes=512, failed_bytes=514)
            with self.assertRaisesRegex(ValueError, "Stock lanes disagree"):
                rank(logs, output)
            write_trial(logs, 0, -1, invalid_request_cycle=100)
            with self.assertRaisesRegex(ValueError, "all-granted control failed"):
                rank(logs, output)
            self.assertFalse(output.exists())

    def test_rank_reads_compact_results_csv_and_validates_strictly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_files = root / "logs_files"
            logs_csv = root / "logs_csv"
            out_files = root / "out_files"
            out_csv = root / "out_csv"
            write_screen(logs_files)
            logs_csv.mkdir()
            (logs_csv / "switch-sites.csv").write_bytes((logs_files / "switch-sites.csv").read_bytes())
            for step in ("compile", "run"):
                (logs_csv / f"{step}.exitcode").write_text("0\n")

            overrides = {
                (0, 0): dict(unknown_bytes=0),
                (0, 1): dict(wrong_bytes=3, unknown_bytes=2, failed_bytes=259),
                (0, 2): dict(returned_tiles=6, correct_tiles=5, missing_tiles=2, missing_bytes=512, failed_bytes=514),
            }
            rows = []
            for workload in range(len(WORKLOADS)):
                for bit in range(-1, SWITCHES + 1):
                    changes = overrides.get((workload, bit), {})
                    if changes:
                        write_trial(logs_files, workload, bit, **changes)
                    row = result(workload, bit, DEADLINE)
                    row.update(changes)
                    rows.append({key: row[key] for key in RESULT_FIELDS})
            write_table(logs_csv / "results.csv", rows)

            rank(logs_files, out_files)
            rank(logs_csv, out_csv)
            for name in ("sites.csv", "ranking.csv", "cases.csv", "summary.json", "summary.md"):
                self.assertEqual((out_csv / name).read_bytes(), (out_files / name).read_bytes(), name)

            # Duplicate row rejected loudly
            write_table(logs_csv / "results.csv", [*rows, rows[0]])
            with self.assertRaisesRegex(ValueError, "duplicate result"):
                rank(logs_csv, root / "out_dup")
            # Missing row rejected loudly
            write_table(logs_csv / "results.csv", rows[:-1])
            with self.assertRaisesRegex(ValueError, "expected"):
                rank(logs_csv, root / "out_missing")


if __name__ == "__main__":
    unittest.main()
