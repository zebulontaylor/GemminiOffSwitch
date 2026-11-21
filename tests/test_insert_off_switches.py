"""Exercise site ordering and specialization on a small split firtool hierarchy."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rtl.insert_off_switches import ARRAY, VARIANTS, insert


class ArrayInsertionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.rtl = self.root / "rtl"
        self.rtl.mkdir()
        self.output = self.root / "switched"
        for module, child, instance in (
            ("Gemmini", "Execute", "ex_controller"),
            ("Execute", "Wrapper", "mesh"),
            ("Wrapper", "Mesh", "mesh"),
        ):
            self.module(module, f"  {child} {instance} (\n    .din (din),\n    .dout (dout)\n  );")
        self.module("Mesh", """  wire [7:0] intermediate;
  Lane lane0 (
    .din (din),
    .dout (intermediate)
  );
  Lane lane1 (
    .din (intermediate),
    .dout (dout)
  );""")
        self.module("Lane", """  wire [7:0] copied = din;
  assign dout = copied;""")
        (self.rtl / "filelist.f").write_text("Gemmini.sv\nExecute.sv\nWrapper.sv\nMesh.sv\nLane.sv\n")

    def module(self, name, body):
        (self.rtl / f"{name}.sv").write_text(
            f"module {name}(\n  input [7:0] din,\n  output [7:0] dout\n);\n{body}\nendmodule\n"
        )

    def insert_paths(self, *paths):
        sites = self.root / "sites.csv"
        sites.write_text("path\n" + "\n".join(paths) + "\n")
        insert(self.rtl, self.output, sites)

    def test_specializes_only_selected_instance(self):
        paths = [ARRAY + "lane1.din", ARRAY + "intermediate"]
        self.insert_paths(*paths)
        with (self.output / "switch-sites.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r["path"], r["grant_bit"], r["width"]) for r in rows],
                         [(paths[0], "0", "8"), (paths[1], "1", "8")])
        variants = (self.output / VARIANTS).read_text()
        self.assertIn("  Lane lane0 (", variants)
        self.assertNotIn("  Lane lane1 (", variants)
        self.assertRegex(variants, r"\.dout\s*\(off_switch_raw_intermediate\)")
        self.assertRegex(variants, r"\.off_switch_raw_din\s*\(intermediate\)")
        self.assertEqual((self.output / "Lane.sv").read_bytes(), (self.rtl / "Lane.sv").read_bytes())
        self.assertIn(VARIANTS, (self.output / "filelist.f").read_text().splitlines())
        self.assertNotIn("off_switch_grant", (self.rtl / "Gemmini.sv").read_text())

    def test_rejects_partially_driven_word(self):
        self.module("Lane", """  wire [7:0] copied;
  assign copied[3:0] = din[3:0];
  assign copied[7:4] = din[7:4];
  assign dout = copied;""")
        with self.assertRaisesRegex(ValueError, "lvalue"):
            self.insert_paths(ARRAY + "lane0.copied")
        self.assertFalse(self.output.exists())

    def test_selects_two_packed_bits_without_clamping_siblings(self):
        paths = [ARRAY + "intermediate[3]", ARRAY + "lane1.din", ARRAY + "intermediate[0]"]
        self.insert_paths(*paths)
        with (self.output / "switch-sites.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r["path"], r["grant_bit"], r["width"], r["packed_range"]) for r in rows],
                         [(paths[0], "0", "1", "7:0"), (paths[1], "1", "8", ""),
                          (paths[2], "2", "1", "7:0")])
        variants = (self.output / VARIANTS).read_text()
        self.assertEqual(variants.count("wire [7:0] off_switch_raw_intermediate;"), 1)
        self.assertIn("assign intermediate[3] = off_switch_grant[0] ? off_switch_raw_intermediate[3] : 1'b0;",
                      variants)
        self.assertIn("assign intermediate[0] = off_switch_grant[2] ? off_switch_raw_intermediate[0] : 1'b0;",
                      variants)
        self.assertIn("assign intermediate[7:4] = off_switch_raw_intermediate[7:4];", variants)
        self.assertIn("assign intermediate[2:1] = off_switch_raw_intermediate[2:1];", variants)

    def test_preserves_ascending_packed_indices(self):
        self.module("Lane", "  wire [3:10] copied = din;\n  assign dout = copied;")
        self.insert_paths(ARRAY + "lane0.copied[6]")
        variants = (self.output / VARIANTS).read_text()
        self.assertIn("assign copied[3:5] = off_switch_raw_copied[3:5];", variants)
        self.assertIn("assign copied[7:10] = off_switch_raw_copied[7:10];", variants)

    def test_rejects_invalid_sites_before_writing(self):
        for paths in (["ex_controller.clock"], [ARRAY + "clock"], [ARRAY + "missing"],
                      [ARRAY + "lane0.copied"] * 2, [ARRAY + "lane0.copied[3]"] * 2,
                      [ARRAY + "lane0.copied", ARRAY + "lane0.copied[3]"],
                      [ARRAY + "lane0.copied[8]"], [ARRAY + "lane0.copied[3:0]"]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.insert_paths(*paths)
            self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
