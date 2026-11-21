# Gemmini off-switch

Screen single-bit zero-clamp locations inside Gemmini's 16×16 systolic array, insert independently gated clamps into the split RTL, integrate signed-license secp256k1 ECDSA verifiers over Rocket's TileLink MMIO bus, and verify lock behavior and mapped standard-cell area.

[Single-bit 1,024-site screen](results/mesh-profile/single-bit-1024/selection/summary.md) · [Inserted-RTL lock verification](results/mesh-profile/single-bit-1024/verification/summary.md) · [Selected wire roles](results/mesh-profile/single-bit-1024/wire-roles.md) · [Authentication regressions](results/off-switch/README.md) · [Mapped area comparison](results/scaled/area/README.md)

## Build the processor-controlled array switches

From the repository root on Linux x86-64 (requires Git, JDK 21, `curl`, `unzip`, and Python 3.10+), initialize the submodules and build the signed-license version of `ExampleRocketSystem`:

```sh
git submodule update --init
python3 scripts/rtl/elaborate_off_switch.py build/elab/gemmini-off-switch
```

The [default site list](infra/gemmini/array-switch-sites.csv) defines 32 whole-word array sites (343 bits) under `Gemmini.ex_controller.mesh.mesh`, which match the archived [area comparison](results/scaled/area/README.md) and [authentication regressions](results/off-switch/README.md). Pass `--sites PATH.csv` to choose 1–32 sites (for example, the newer 32-bit [single-bit screened selection](results/mesh-profile/single-bit-1024/selection/sites.csv)). Use a `path` column with names relative to `Gemmini`; row zero is switch zero.

Each site gets a secp256k1 ECDSA verifier in the MMIO peripheral with its own challenge, signer state, and 64-bit allowance. Its enable reaches only the matching clamp inside the array. While its allowance is zero, the switch drives the selected word or bit to zero. The CPU, caches, and TileLink path to the license registers remain available while every switch is locked. A clamp can corrupt a matrix result while Gemmini still reports completion. Authorize all selected sites before launching work that needs a correct result.

The output directory contains the split SoC RTL and `switch-sites.csv` with the resolved widths and grant-bit assignments. To generate just the clamps for accelerator-level synthesis or testing, elaborate stock Gemmini and run the inserter directly:

```sh
scripts/rtl/elaborate_gemmini.sh
python3 scripts/rtl/insert_off_switches.py build/elab/gemmini build/elab/clamped-array \
  --sites results/mesh-profile/single-bit-1024/selection/sites.csv
```

That version exposes `off_switch_grant` as a `Gemmini` input. Use `scripts/rtl/elaborate_off_switch.py` above for processor authentication.

### Deliver a signed license

For switch `i`, use MMIO base `0x10010000 + 0x80 * i`. Integers are little-endian. The signature occupies two 256-bit integers, with `s` at the lower address and `r` at the higher address.

| Offset | Access | Contents |
|---|---|---|
| `0x00` | Read | 32-bit status. Bit 0 is ready, 1 is busy, 2 identifies the next signer, 3 is enabled, 4 is done, and 5 records whether the last submission passed. |
| `0x08` | Write | Write 1 to submit a snapshot of the staged signature when ready. |
| `0x20–0x3f` | Read | Current 256-bit challenge. Read while ready. |
| `0x40–0x5f` | Read/write | Signature scalar `s`, least-significant byte first. |
| `0x60–0x7f` | Read/write | Signature scalar `r`, least-significant byte first. |

1. Poll ready and read the challenge. Send that exact 256-bit value to the indicated signer as the ECDSA message digest.
2. Write `s` and `r` using aligned stores. Order the staging writes before the submit store and the submit before polling status (on Rocket, `fence iorw, iorw` orders MMIO accesses).
3. Poll done and check bit 5. A rejected signature leaves the same signer and challenge available for retry. A successful signature from signer 0 advances to signer 1. Both sign the same challenge.
4. After signer 1 succeeds, hardware adds 10¹² clock cycles to this switch’s allowance, saturating at its 64-bit maximum, and issues a new challenge. The allowance counts down while the system clock runs. An expired switch clamps its word again.

The prototype uses the [upstream verifier’s published test keys and deterministic nonce generator](https://github.com/JamesPetrie/off-switch/blob/774a596607cfd2749616c054af151a11f00de004/README.md#prototype-limitations). Each challenge combines a fixed 32-bit switch prefix with a 224-bit LFSR sample taken when that switch requests a nonce. The LFSRs start from the same seed, so simultaneously sampled challenges share the low bits and resetting the system repeats the initial challenges. The mailbox checks that both signature scalars are in range. The upstream verifier omits the final reduction of the affine x-coordinate modulo the curve order, so it can reject a rare valid signature. These keys and challenges support functional testing and area estimation. A deployed licensing system needs provisioned public keys and fresh challenges across resets.

## Screen and rank single-bit mesh switches

Single-bit candidate screening builds a universal uncloned Verilator model of `Mesh`, `Tile`, `PE_256`, and `MacUnit` once, records golden `Mesh` I/O and accumulator schedules from one full `Gemmini` run per workload, evaluates candidates on standalone `VMesh`, and falls back to full `VGemmini` whenever any `Mesh` control output diverges from the golden trace.

Four weight-stationary 16×16 signed matrix workloads are evaluated per site:

| Workload | Matrix operands |
|---:|---|
| 0 | Dense pseudorandom inputs from −2 through 2, including zeros |
| 1 | Dense nonzero inputs drawn from −2, −1, 1, and 2 |
| 2 | Signed permutation matrix with rotating diagonal times a full-range signed-byte matrix |
| 3 | Dense inputs from −8 through 7, exercising signed-byte output saturation |

With stock Gemmini elaborated at `build/elab/gemmini` and Verilator installed, screen a stratified sample of `N` single-bit sites (or `--all` for all 110,900 eligible bits, or `--sites PATH.csv`) and rank the top 32 at cycle 1,767:

```sh
scripts/screen/run.sh build/mesh-screen-1024 --count 1024
python3 scripts/screen/rank.py --logs build/mesh-screen-1024 --output build/mesh-screen-1024/selection
```

Reuse the compiled universal model for subsequent sweeps with `--model`:

```sh
scripts/screen/run.sh --model build/mesh-screen-1024 build/mesh-screen-all --all
```

The screener uses the canonical Gemmini word catalog at `results/scaled/catalog/words.csv.gz`. To regenerate that catalog from elaborated RTL (requires Yosys with Slang on `PATH`):

```sh
python3 scripts/screen/catalog_words.py netlist --top Gemmini --out build/netlist/Gemmini.json build/elab/gemmini/*.sv build/elab/gemmini/*.v
python3 scripts/screen/catalog_words.py catalog --publish results/scaled/catalog
```

Sites with the fewest correct tiles in their best workload rank first; ties favor more missing tiles across workloads, then more known wrong returned bytes, then ascending dense mesh ID.

## Verify inserted switches and measure area

Insert the ranked sites into stock Gemmini and verify each individual grant lock (all-granted control plus each grant bit 0–31 held low from reset) across all four workloads locally with Verilator or on CAE with VCS (to check four-state unknowns):

```sh
python3 scripts/rtl/insert_off_switches.py build/elab/gemmini build/switched-rtl \
  --sites build/mesh-screen-1024/selection/sites.csv

# Local Verilator replay (set JOBS=4 to parallelize cases; reuse model with --model)
CYCLES=14001 scripts/verify/lock_verilator.sh build/switched-rtl build/lock-verilator

# CAE VCS verification (set CAE_HOST=user@host)
CAE_HOST=user@host CYCLES=14001 JOBS=4 scripts/verify/lock_cae.sh build/switched-rtl build/lock-vcs

python3 scripts/verify/report_lock.py \
  --logs build/lock-vcs \
  --selection build/mesh-screen-1024/selection/sites.csv \
  --force-cases build/mesh-screen-1024/selection/cases.csv \
  --output build/lock-report
```

See [results/off-switch/README.md](results/off-switch/README.md) for the CAE direct-grant, signed-license, Rocket firmware boot, and allowance-expiry regressions (`scripts/verify/{test,rocket,expiry}_cae.sh`), and [results/scaled/area/README.md](results/scaled/area/README.md) for Nangate45 standard-cell area mapping (`scripts/area/run.sh`).

## Hardware build memory limits

Local synthesis, elaboration, and Verilator build scripts share one systemd user scope via `scripts/hardware_guard.py`. A second build on the same account and host exits before launching tools. The kernel caps the combined memory of the build and its children, including ABC, at the smallest of 6 GiB, one quarter of RAM, and half the available memory above the host reserve, with zero swap allowance. A watchdog stops the scope if available memory falls below the reserve (one quarter of RAM, at least 1 GiB).

For a smaller ceiling, set `GEMMINI_BUILD_MEMORY_MIB` (64–6144). Run one-off synthesis or elaboration commands through `scripts/hardware_guard.py COMMAND ...`. The guard requires Linux cgroup v2 and a systemd user manager with the memory controller; if the limit cannot be enforced, the command exits before starting the tool.

## Tests

Run the Python unit test suite from the repository root:

```sh
python3 -m unittest discover -s tests
```
