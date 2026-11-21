# Single-bit mesh lock screen

Verilator tested 1024 sites with one grant held low from reset in each trial. The ranking uses output counts at cycle 1,767 and selects the first 32 sites for insertion.

Sites with the fewest correct tiles in their best workload rank first. Ties favor more missing tiles across workloads, then more known wrong returned bytes, then lower dense mesh ID. Unknown bytes make a tile incorrect and are excluded from the known wrong byte count.

| Workload | All-granted correct tiles |
|---:|---:|
| 0 | 8 |
| 1 | 8 |
| 2 | 8 |
| 3 | 8 |

| Rank | Mesh ID | Path | Max correct tiles | Total missing tiles | Total known wrong bytes |
|---:|---:|---|---:|---:|---:|
| 1 | 515 | `ex_controller.mesh.mesh.io_in_valid_0_0` | 0 | 32 | 0 |
| 32 | 6239 | `ex_controller.mesh.mesh._mesh_4_3_io_out_c_0[7]` | 0 | 0 | 382 |

At the cutoff, 205 sites had a maximum of 0 correct tiles. The remaining criteria placed 32 of those sites in the selection.

[Selected sites](sites.csv) · [All ranks](ranking.csv) · [Individual workload counts](cases.csv)
