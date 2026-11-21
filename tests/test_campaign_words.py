"""Verify how the observed-word catalog handles aliases, constants, and RTL widths."""

import csv
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from screen.catalog_words import collect


class CampaignWordsTest(unittest.TestCase):
    def test_aliases_ports_and_omitted_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Top.sv").write_text("""module Top(
  input clock,
  input [1:0] data,
  output y
);
  wire [1:0] bus;
  Unit u (
    .q(bus)
  );
endmodule
""")
            (root / "Unit.sv").write_text("""module Unit(
  output [1:0] q
);
  reg [1:0] held;
  wire [1:0] constant_value;
  wire [1:0][1:0] matrix;
endmodule
""")
            bits = {"clock": [2], "data": [3, 4], "y": [5], "bus": [6, 7],
                    "u.q": [6, 7], "u.held": [6, 7], "u.constant_value": ["0", "1"],
                    "u.matrix": [3, 4, 6, 7]}
            ports = {k: {"direction": d, "bits": bits[k]}
                     for k, d in (("clock", "input"), ("data", "input"), ("y", "output"))}
            module = {
                "netnames": {k: {"hide_name": 0, "bits": v} for k, v in bits.items()},
                "ports": ports,
                "cells": {"ff": {"type": "$dff", "port_directions": {"CLK": "input", "Q": "output"},
                                 "connections": {"CLK": [2], "Q": [6, 7]}}},
            }
            netlist = root / "netlist.json"
            netlist.write_text(json.dumps({"modules": {"Top": module}}))
            summary = collect(netlist, root, "Top", root)
            self.assertEqual(summary["words"], 4)
            self.assertEqual(summary["candidates"], 1)
            self.assertEqual(summary["omitted_names"], {"constant": 1, "multidimensional_packed": 1})
            with (root / "words.csv").open() as stream:
                rows = {r["path"]: r for r in csv.DictReader(stream)}
            self.assertEqual(set(rows), {"clock", "data", "y", "u.held"})
            self.assertEqual(rows["u.held"]["candidate"], "1")
            self.assertEqual(rows["u.held"]["kind"], "state")
            with gzip.open(root / "aliases.csv.gz", "rt") as stream:
                aliases = {r["alias"]: r["canonical"] for r in csv.DictReader(stream)}
            self.assertEqual(aliases, {"bus": "u.held", "u.q": "u.held"})


if __name__ == "__main__":
    unittest.main()
