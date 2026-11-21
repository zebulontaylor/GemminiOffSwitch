"""Check build admission, limits shared by descendants, and fail-closed startup."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from hardware_guard import MIB, budget


class GuardTest(unittest.TestCase):
    def test_reserve_and_cap(self):
        self.assertEqual(budget(40000, 30000, 6144), (6144, 10000))
        self.assertEqual(budget(40000, 11000, 6144), (500, 10000))
        with self.assertRaisesRegex(ValueError, "Insufficient RAM"):
            budget(40000, 10020, 6144)

    def test_missing_limiter_does_not_run_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "launched"
            result = subprocess.run(
                [sys.executable, SCRIPTS / "hardware_guard.py", "/usr/bin/touch", marker],
                env={**os.environ, "PATH": tmp}, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.environ.get("TEST_GEMMINI_CGROUP") == "1",
                         "set TEST_GEMMINI_CGROUP=1 to exercise a 64 MiB user scope")
    def test_kernel_limit_and_single_build(self):
        guard = [sys.executable, str(SCRIPTS / "hardware_guard.py")]
        env = {**os.environ, "GEMMINI_BUILD_MEMORY_MIB": "64"}
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ready"
            release = Path(tmp) / "allocate"
            child = f"""
from pathlib import Path
import time
group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1][1:]
assert (group / 'memory.max').read_text().strip() == '{64 * MIB}'
assert (group / 'memory.swap.max').read_text().strip() == '0'
Path({str(marker)!r}).touch()
for _ in range(100):
    if Path({str(release)!r}).exists():
        break
    time.sleep(0.1)
assert Path({str(release)!r}).exists()
memory = bytearray(96 * 1024 * 1024)
"""
            # Exercise a grandchild, like ABC started by Yosys.
            outer = "import subprocess,sys; sys.exit(subprocess.call([sys.executable, '-c', sys.argv[1]]))"
            with subprocess.Popen([*guard, sys.executable, "-c", outer, child], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                for _ in range(100):
                    if marker.exists() or process.poll() is not None:
                        break
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), "the guarded payload did not start")
                denied = subprocess.run([*guard, shutil.which("touch"), Path(tmp) / "second"],
                                        env=env, capture_output=True)
                self.assertNotEqual(denied.returncode, 0)
                self.assertFalse((Path(tmp) / "second").exists())
                release.touch()
                process.communicate(timeout=20)
                self.assertIn(process.returncode, (-9, 137))


if __name__ == "__main__":
    unittest.main()
