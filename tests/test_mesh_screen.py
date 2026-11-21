"""Test universal Mesh screen preparation, bit-mask encoding, and validation."""

import csv
import gzip
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rtl.insert_off_switches import ARRAY
from screen.prepare import expand_single_bits, load_mesh_words, prepare_sites


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_words_gz(path, rows):
    with gzip.open(path, "wt", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class MeshScreenPreparationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.words = self.root / "words.csv.gz"
        self.sig_map = self.root / "signal_map.csv"
        write_words_gz(self.words, [
            dict(id="10", path=ARRAY + "io_in_valid_0_0", width="1", region=ARRAY[:-1],
                 kind="comb", candidate="1", module="Mesh", block="ex_controller", cell_type="$not", n_aliases="1"),
            dict(id="11", path=ARRAY + "io_in_a_0_0", width="8", region=ARRAY[:-1],
                 kind="comb", candidate="1", module="Mesh", block="ex_controller", cell_type="$mux", n_aliases="1"),
            dict(id="12", path=ARRAY + "mesh_0_0.tile_0_0.c1", width="32", region=ARRAY[:-1],
                 kind="state", candidate="1", module="PE_256", block="ex_controller", cell_type="$dffe", n_aliases="1"),
            dict(id="13", path=ARRAY + "not_a_candidate", width="1", region=ARRAY[:-1],
                 kind="comb", candidate="0", module="Mesh", block="ex_controller", cell_type="$and", n_aliases="1"),
        ])
        write_csv(self.sig_map, ["path", "module", "width", "packed_range", "mesh_sig", "tile", "pe_sig"], [
            dict(path=ARRAY + "io_in_valid_0_0", module="Mesh", width="1", packed_range="", mesh_sig="0", tile="511", pe_sig="15"),
            dict(path=ARRAY + "io_in_a_0_0", module="Mesh", width="8", packed_range="7:0", mesh_sig="1", tile="511", pe_sig="15"),
            dict(path=ARRAY + "mesh_0_0.tile_0_0.c1", module="PE_256", width="32", packed_range="31:0", mesh_sig="4095", tile="0", pe_sig="10"),
        ])

    def test_all_bits_expands_every_eligible_bit_with_exact_masks(self):
        out = self.root / "all"
        count = prepare_sites(self.sig_map, self.words, out, all_bits=True)
        self.assertEqual(count, 1 + 8 + 32)
        with (out / "candidates_compiled.csv").open(newline="") as stream:
            compiled = list(csv.DictReader(stream))
        self.assertEqual(len(compiled), 41)
        # Scalar site: mask clears all bits (width=1 uses mask[0])
        self.assertEqual((compiled[0]["mesh_sig"], compiled[0]["tile"], compiled[0]["pe_sig"], compiled[0]["mask"]),
                         ("0", "511", "15", "00000000"))
        # Vector bit [3] of io_in_a_0_0: ~(1 << 3)
        self.assertEqual((compiled[4]["mesh_sig"], compiled[4]["tile"], compiled[4]["pe_sig"], compiled[4]["mask"]),
                         ("1", "511", "15", "fffffff7"))
        # Vector bit [31] of PE_256 c1: ~(1 << 31)
        self.assertEqual((compiled[40]["mesh_sig"], compiled[40]["tile"], compiled[40]["pe_sig"], compiled[40]["mask"]),
                         ("4095", "0", "10", "7fffffff"))

    def test_seeded_count_matches_single_bit_expansion(self):
        out = self.root / "sampled"
        count = prepare_sites(self.sig_map, self.words, out, count=5, seed=20261119)
        self.assertEqual(count, 5)
        with (out / "switch-sites.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([int(r["grant_bit"]) for r in rows], list(range(5)))
        self.assertEqual(len({int(r["id"]) for r in rows}), 5)

    def test_rejects_invalid_sites_loudly(self):
        bad_cases = [
            ("duplicate", [ARRAY + "io_in_valid_0_0", ARRAY + "io_in_valid_0_0"], "Duplicate site path"),
            ("outside_array", ["ex_controller.foo"], "Expected a signal path"),
            ("missing_signal", [ARRAY + "not_a_candidate"], "not an eligible mesh candidate"),
            ("scalar_index", [ARRAY + "io_in_valid_0_0[0]"], "scalar"),
            ("bit_oob", [ARRAY + "io_in_a_0_0[8]"], "outside"),
        ]
        for label, paths, pattern in bad_cases:
            with self.subTest(label=label):
                csv_path = self.root / f"{label}.csv"
                write_csv(csv_path, ["path"], [dict(path=p) for p in paths])
                with self.assertRaisesRegex(ValueError, pattern):
                    prepare_sites(self.sig_map, self.words, self.root / f"out-{label}", sites_path=csv_path)


if __name__ == "__main__":
    unittest.main()
