# Array switch and authentication regressions

The CAE VCS tests exercise the default 32-switch netlist and a two-switch build made from the first two rows of the same site list. The smaller system runs the complete two-signer protocol through the same MMIO and ECDSA RTL as the default.

| Test | Evidence |
|---|---|
| 32-site array | Eight signed 16×16 tiles match the original Gemmini cycle for cycle. Each grant bit independently clamps its selected word. Denying all 32 produces incorrect results. [Run summary](array-32/summary.txt) |
| 32-site Rocket boot | RV64 firmware reads every switch’s challenge, checks the site IDs, checks rejection of a malformed license, submits a signer-0 signature, and completes arithmetic and memory accesses while verification is busy and all grants are clear. [Run summary](rocket-32/summary.txt) |
| Two-switch signed matrix workload | The TileLink test rejects malformed and cross-switch signatures. Each switch enables only after both signers pass. Eight tiles then match the reference. Reset clears both grants and two following tiles fail the matrix oracle. [Run summary](signed-2/summary.txt) |
| Two-switch Rocket firmware | Rocket submits both signatures for switch 0 and observes its grant and renewed challenge after 11.62 million cycles. Switch 1 stays locked with its original challenge. [Run summary](rocket-2/summary.txt) |
| Independent expiry | A nine-cycle test allowance reaches zero and disables its switch on the ninth clock. The second verifier stays enabled and continues its own countdown. [Cycle trace](expiry/summary.txt) |

## Run on CAE

From the repository root, build the [integrated RTL](../../README.md#build-the-processor-controlled-array-switches) and set `CAE_HOST=user@host`. The following commands test the default site list. The direct-grant mode drives the accelerator testbench inputs so that every routed bit can be toggled independently.

```sh
scripts/rtl/elaborate_gemmini.sh
DIRECT_GRANTS=1 scripts/verify/test_cae.sh build/elab/gemmini-off-switch
ROCKET_CHECK=boot scripts/verify/rocket_cae.sh
scripts/verify/expiry_cae.sh
```

For the signed tests, elaborate the first two switches into a fresh directory. The first testbench drives TileLink transactions and checks Gemmini’s computed tile bytes. The second boots RV64 firmware on Rocket and submits the signatures with ordinary MMIO stores. Building the boot firmware uses Clang’s RISC-V backend, LLD and llvm-objcopy.

```sh
head -n 3 infra/gemmini/array-switch-sites.csv > build/two-array-sites.csv
python3 scripts/rtl/elaborate_off_switch.py build/elab/license-test-2 --sites build/two-array-sites.csv
scripts/verify/test_cae.sh build/elab/license-test-2
RTL_DIR=build/elab/license-test-2 ROCKET_CHECK=full scripts/verify/rocket_cae.sh
```

Each CAE runner stages, compiles and simulates on one host through a single SSH ControlMaster. The result directories include the VCS compiler version, simulation output and exit codes.
