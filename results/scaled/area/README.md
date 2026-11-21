# Standard-cell area of the signed off-switches

Gemmini uses 32 independently controlled zero clamps covering 343 RTL bits in the systolic array. The Rocket system adds a TileLink MMIO peripheral, a signed-license mailbox for each site, and an ECDSA verifier for each mailbox. The standalone Gemmini comparison measures the clamp logic with grants exposed as inputs. The Rocket comparison includes the mailbox staging registers, nonce and allowance logic, grant routing, and TileLink integration. [Switch sites](../../../infra/gemmini/array-switch-sites.csv)

## Results

| Mapped standard-cell area | Baseline (µm²) | Instrumented (µm²) | Increase (µm²) |
|---|---:|---:|---:|
| Standalone Gemmini with 32 clamps | 1,476,836.788 | 1,477,217.434 | 380.646 (0.026%) |
| Rocket partition outside Gemmini and ECDSA | 481,653.648 | 936,656.756 | 455,003.108 |
| Complete Rocket system including Gemmini and all 32 ECDSA engines | 1,958,490.436 | 5,379,221.176 | 3,420,730.740 (174.662%) |

One ECDSA engine occupies 92,666.952 µm². The 32 instances contribute 2,965,342.464 µm². The integrated Rocket total also includes a 4.522 µm² correction for the MMIO address decode in Gemmini's TLB. [Area calculation](comparison.txt) · [ECDSA cell inventory](ecdsa.stat.json) · [ECDSA source hash](ecdsa-source.sha256) · [Rocket cell inventory](rocket-switch.stat.json)

## Measurement scope

Yosys 0.59+110 (`8ea51e147`) with Slang lowers and flattens each partition. `dfflibmap` and ABC (`strash; &get -n; &nf; &put`) map the logic to the Nangate45 typical corner at 1.10 V and 25 °C. The area is the sum of mapped standard-cell areas in µm² before placement and routing. It excludes physical overhead such as clock trees, routing, and timing-driven signal buffering. [Nangate45 Liberty file](https://github.com/The-OpenROAD-Project/OpenROAD/blob/e06ca0f5f2e641cd7b138a4d9b4fdd290359eca8/test/Nangate45/Nangate45_typ.lib)

The Gemmini and Rocket comparisons both exclude four scratchpad SRAMs and two accumulator SRAMs. The Rocket comparison also excludes eight cache SRAMs. Queue memories map to standard cells. Both Rocket variants preserve the Gemmini boundary. The instrumented variant also preserves each ECDSA boundary. The Rocket maps check the instance counts after mapping. Each total adds the corresponding Gemmini partition once and the ECDSA partition once per engine. The hierarchy keeps constant propagation and logic optimization inside the partitions.

The MMIO address aperture changes the physical memory attribute decoder in Gemmini's `DTLB_2`. The Rocket estimate adds the separately mapped `DTLB_2` difference to the clamp-only Gemmini area so the standalone clamp comparison isolates the 32 switches.

The standalone Gemmini inventories sum the mapped ABC combinational gates and the flip-flops from `dfflibmap`. The two designs each map 79,305 flip-flops. [Baseline inventory](gemmini-baseline.summary.json) · [Clamp inventory](gemmini-switch.summary.json)

## Reproduce

From the repository root, elaborate the baseline and instrumented systems and pass the Liberty file to the area script:

```sh
scripts/rtl/elaborate_gemmini.sh
python3 scripts/rtl/elaborate_off_switch.py
scripts/area/run.sh /path/to/Nangate45_typ.lib build/elab/gemmini build/elab/gemmini-off-switch
```

The area script prints its run directory under `build/campaign-area/`. Each run includes the source list, Yosys script, synthesis log, and mapped-cell statistics. The `comparison.txt` file reports both deltas and the total area with all engines. The Liberty SHA-256 is `2efd0b32eb580e4e60e72fc0575bb3bc69aac907c91d908442e4ae6d7fe55895`.
