"""Check role, geometry, and lane coverage in the mesh bit sample."""

import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from screen.sample_bits import mesh_location, sample_mesh_bits


PREFIX = "ex_controller.mesh.mesh."


class MeshBitSampleTests(unittest.TestCase):
    def setUp(self):
        names = [
            f"mesh_{row}_{column}.tile_0_0.mac_unit.io_in_b[{bit}]"
            for row in range(4)
            for column in range(4)
            for bit in range(4)
        ]
        names += [
            f"mesh_{row}_{column}_io_in_control_0_dataflow_pipe_b"
            for row in range(4)
            for column in range(4)
        ]
        names += [f"mesh_3_{column}.tile_0_0.last_s" for column in range(4)]
        self.candidates = [{"id": index, "path": PREFIX + name} for index, name in enumerate(names)]

    def test_prefix_covers_roles_axes_and_lanes(self):
        selected = sample_mesh_bits(self.candidates, 16, seed=11)
        locations = [mesh_location(row["path"].split("[")[0]) for row in selected]
        self.assertEqual(len({role for role, _, _ in locations[:3]}), 3)
        self.assertEqual({row for _, row, _ in locations[:4]}, set(range(4)))
        self.assertEqual({column for _, _, column in locations[:4]}, set(range(4)))
        lanes = Counter(row["path"].rsplit("[", 1)[1] for row in selected if "[" in row["path"])
        self.assertEqual(lanes.keys(), {"0]", "1]", "2]", "3]"})
        self.assertLessEqual(max(lanes.values()) - min(lanes.values()), 1)

    def test_prefix_is_stable_across_order_and_budget(self):
        full = sample_mesh_bits(self.candidates, len(self.candidates), seed=11)
        self.assertCountEqual(full, self.candidates)
        self.assertEqual(full[:16], sample_mesh_bits(reversed(self.candidates), 16, seed=11))
        self.assertNotEqual(full[:16], sample_mesh_bits(self.candidates, 16, seed=12))

    def test_geometry_distinguishes_tiles_and_mesh_ports(self):
        cases = [
            ("_mesh_12_15_io_out_b_0", (12, 15)),
            ("mesh_12_15_io_in_control_0_shift_pipe_b", (12, 15)),
            ("mesh_12_15.tile_0_0.last_s", (12, 15)),
            ("io_in_a_0_0", (0, None)),
            ("io_in_valid_0_0", (None, 0)),
            ("r_1027_0_dataflow", (None, None)),
        ]
        for name, position in cases:
            with self.subTest(name=name):
                self.assertEqual(mesh_location(PREFIX + name)[1:], position)

    def test_rejects_invalid_budget_and_duplicate_paths(self):
        with self.assertRaisesRegex(ValueError, "Sample size"):
            sample_mesh_bits(self.candidates, len(self.candidates) + 1, seed=11)
        with self.assertRaisesRegex(ValueError, "Duplicate mesh bit path"):
            sample_mesh_bits(self.candidates + self.candidates[:1], 1, seed=11)


if __name__ == "__main__":
    unittest.main()
