# Individual grant locks in the inserted Gemmini RTL

Each VCS run started from reset with one physical grant held low and the other 31 grants high. An all-granted control and a stock reference ran the same signed 16×16 matrix products. A correct tile has all 256 bytes equal to the arithmetic result. The stock lane's completed tiles define the expected count for each workload and checkpoint. Each missing tile accounts for 256 missing bytes. Known wrong bytes count mismatches with a known returned value. Unknown bytes also make a tile incorrect.

| Workload | Operands | Cycle 1,767 expected / all granted correct | Cycle 14,001 expected / all granted correct |
|---:|---|---:|---:|
| 0 | Dense pseudorandom inputs from −2 through 2 | 8 / 8 | 68 / 68 |
| 1 | Dense nonzero inputs drawn from −2, −1, 1 and 2 | 8 / 8 | 68 / 68 |
| 2 | Signed permutation matrix times a full-range signed-byte matrix | 8 / 8 | 68 / 68 |
| 3 | Dense inputs from −8 through 7, exercising output saturation | 8 / 8 | 68 / 68 |

## Correct tiles at cycle 1,767

| Grant bit | Rank | Path | Workload 0 | Workload 1 | Workload 2 | Workload 3 | Missing bytes | Known wrong bytes |
|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 0 | 1 | `ex_controller.mesh.mesh.io_in_valid_0_0` | 0 | 0 | 0 | 0 | 8,192 | 0 |
| 1 | 2 | `ex_controller.mesh.mesh.r_1027_0_dataflow` | 0 | 0 | 0 | 0 | 0 | 8,000 |
| 2 | 3 | `ex_controller.mesh.mesh.io_in_a_0_0[0]` | 0 | 0 | 0 | 0 | 0 | 2,626 |
| 3 | 4 | `ex_controller.mesh.mesh.r_131_0[0]` | 0 | 0 | 0 | 0 | 0 | 2,099 |
| 4 | 5 | `ex_controller.mesh.mesh.r_151_0[0]` | 0 | 0 | 0 | 0 | 0 | 1,419 |
| 5 | 6 | `ex_controller.mesh.mesh.mesh_0_4_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 499 |
| 6 | 7 | `ex_controller.mesh.mesh.mesh_0_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 498 |
| 7 | 8 | `ex_controller.mesh.mesh.mesh_1_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 493 |
| 8 | 9 | `ex_controller.mesh.mesh.mesh_2_7_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 485 |
| 9 | 10 | `ex_controller.mesh.mesh.mesh_2_10_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 482 |
| 10 | 11 | `ex_controller.mesh.mesh.mesh_3_4_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 476 |
| 11 | 12 | `ex_controller.mesh.mesh.mesh_4_8_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 466 |
| 12 | 13 | `ex_controller.mesh.mesh.mesh_6_15_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 451 |
| 13 | 14 | `ex_controller.mesh.mesh.mesh_6_14_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 447 |
| 14 | 15 | `ex_controller.mesh.mesh.mesh_6_7_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 440 |
| 15 | 16 | `ex_controller.mesh.mesh.mesh_8_4_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 422 |
| 16 | 17 | `ex_controller.mesh.mesh.mesh_9_9_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 417 |
| 17 | 18 | `ex_controller.mesh.mesh.pipe_b_432_0[3]` | 0 | 0 | 0 | 0 | 0 | 411 |
| 18 | 19 | `ex_controller.mesh.mesh.mesh_10_12_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 409 |
| 19 | 20 | `ex_controller.mesh.mesh._mesh_1_10_io_out_c_0[3]` | 0 | 0 | 0 | 0 | 0 | 406 |
| 20 | 21 | `ex_controller.mesh.mesh.mesh_10_9_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 397 |
| 21 | 22 | `ex_controller.mesh.mesh.io_in_d_0_0[4]` | 0 | 0 | 0 | 0 | 0 | 396 |
| 22 | 23 | `ex_controller.mesh.mesh._mesh_4_10_io_out_c_0[7]` | 0 | 0 | 0 | 0 | 0 | 395 |
| 23 | 24 | `ex_controller.mesh.mesh.mesh_11_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 393 |
| 24 | 25 | `ex_controller.mesh.mesh._mesh_1_4_io_out_c_0[5]` | 0 | 0 | 0 | 0 | 0 | 393 |
| 25 | 26 | `ex_controller.mesh.mesh._mesh_2_0_io_out_c_0[5]` | 0 | 0 | 0 | 0 | 0 | 390 |
| 26 | 27 | `ex_controller.mesh.mesh._mesh_4_7_io_out_c_0[1]` | 0 | 0 | 0 | 0 | 0 | 390 |
| 27 | 28 | `ex_controller.mesh.mesh.io_in_d_0_0[6]` | 0 | 0 | 0 | 0 | 0 | 388 |
| 28 | 29 | `ex_controller.mesh.mesh.pipe_b_450_0[0]` | 0 | 0 | 0 | 0 | 0 | 388 |
| 29 | 30 | `ex_controller.mesh.mesh._mesh_2_5_io_out_c_0[4]` | 0 | 0 | 0 | 0 | 0 | 388 |
| 30 | 31 | `ex_controller.mesh.mesh.pipe_b_386_0[5]` | 0 | 0 | 0 | 0 | 0 | 385 |
| 31 | 32 | `ex_controller.mesh.mesh._mesh_4_3_io_out_c_0[7]` | 0 | 0 | 0 | 0 | 0 | 382 |

The largest correct-tile count in any one workload was **0**. 32 of the 32 locks reached that count.

**Grant bit 31 was weakest by the full ranking (selection rank 32).** Correct tiles by workload (0 through 3) were 0/8, 0/8, 0/8, 0/8. Across those workloads, 0 tiles were missing and 382 returned bytes were known wrong.

## Correct tiles at cycle 14,001

| Grant bit | Rank | Path | Workload 0 | Workload 1 | Workload 2 | Workload 3 | Missing bytes | Known wrong bytes |
|---:|---:|---|---:|---:|---:|---:|---:|---:|
| 0 | 1 | `ex_controller.mesh.mesh.io_in_valid_0_0` | 0 | 0 | 0 | 0 | 69,632 | 0 |
| 1 | 2 | `ex_controller.mesh.mesh.r_1027_0_dataflow` | 0 | 0 | 0 | 0 | 0 | 67,940 |
| 2 | 3 | `ex_controller.mesh.mesh.io_in_a_0_0[0]` | 0 | 0 | 0 | 0 | 0 | 22,827 |
| 3 | 4 | `ex_controller.mesh.mesh.r_131_0[0]` | 0 | 0 | 0 | 0 | 0 | 18,731 |
| 4 | 5 | `ex_controller.mesh.mesh.r_151_0[0]` | 0 | 0 | 0 | 0 | 0 | 12,407 |
| 5 | 6 | `ex_controller.mesh.mesh.mesh_0_4_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,245 |
| 6 | 7 | `ex_controller.mesh.mesh.mesh_0_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,258 |
| 7 | 8 | `ex_controller.mesh.mesh.mesh_1_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,180 |
| 8 | 9 | `ex_controller.mesh.mesh.mesh_2_7_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,115 |
| 9 | 10 | `ex_controller.mesh.mesh.mesh_2_10_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,104 |
| 10 | 11 | `ex_controller.mesh.mesh.mesh_3_4_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 4,044 |
| 11 | 12 | `ex_controller.mesh.mesh.mesh_4_8_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,962 |
| 12 | 13 | `ex_controller.mesh.mesh.mesh_6_15_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,794 |
| 13 | 14 | `ex_controller.mesh.mesh.mesh_6_14_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,820 |
| 14 | 15 | `ex_controller.mesh.mesh.mesh_6_7_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,799 |
| 15 | 16 | `ex_controller.mesh.mesh.mesh_8_4_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,612 |
| 16 | 17 | `ex_controller.mesh.mesh.mesh_9_9_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,519 |
| 17 | 18 | `ex_controller.mesh.mesh.pipe_b_432_0[3]` | 0 | 0 | 0 | 0 | 0 | 3,443 |
| 18 | 19 | `ex_controller.mesh.mesh.mesh_10_12_io_in_control_0_propagate_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,440 |
| 19 | 20 | `ex_controller.mesh.mesh._mesh_1_10_io_out_c_0[3]` | 0 | 0 | 0 | 0 | 0 | 3,369 |
| 20 | 21 | `ex_controller.mesh.mesh.mesh_10_9_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,426 |
| 21 | 22 | `ex_controller.mesh.mesh.io_in_d_0_0[4]` | 0 | 0 | 0 | 0 | 0 | 3,400 |
| 22 | 23 | `ex_controller.mesh.mesh._mesh_4_10_io_out_c_0[7]` | 0 | 0 | 0 | 0 | 0 | 3,326 |
| 23 | 24 | `ex_controller.mesh.mesh.mesh_11_12_io_in_control_0_dataflow_pipe_b` | 0 | 0 | 0 | 0 | 0 | 3,344 |
| 24 | 25 | `ex_controller.mesh.mesh._mesh_1_4_io_out_c_0[5]` | 0 | 0 | 0 | 0 | 0 | 3,311 |
| 25 | 26 | `ex_controller.mesh.mesh._mesh_2_0_io_out_c_0[5]` | 0 | 0 | 0 | 0 | 0 | 3,289 |
| 26 | 27 | `ex_controller.mesh.mesh._mesh_4_7_io_out_c_0[1]` | 0 | 0 | 0 | 0 | 0 | 3,276 |
| 27 | 28 | `ex_controller.mesh.mesh.io_in_d_0_0[6]` | 0 | 0 | 0 | 0 | 0 | 3,405 |
| 28 | 29 | `ex_controller.mesh.mesh.pipe_b_450_0[0]` | 0 | 0 | 0 | 0 | 0 | 3,311 |
| 29 | 30 | `ex_controller.mesh.mesh._mesh_2_5_io_out_c_0[4]` | 0 | 0 | 0 | 0 | 0 | 3,328 |
| 30 | 31 | `ex_controller.mesh.mesh.pipe_b_386_0[5]` | 0 | 0 | 0 | 0 | 0 | 3,320 |
| 31 | 32 | `ex_controller.mesh.mesh._mesh_4_3_io_out_c_0[7]` | 0 | 0 | 0 | 0 | 0 | 3,289 |

The largest correct-tile count in any one workload was **0**. 32 of the 32 locks reached that count.

**Grant bit 26 was weakest by the full ranking (selection rank 27).** Correct tiles by workload (0 through 3) were 0/68, 0/68, 0/68, 0/68. Across those workloads, 0 tiles were missing and 3,276 returned bytes were known wrong.

## Comparison with single-bit screen at cycle 1,767

The screen tested one grant at a time in the larger Verilator model. The selected 32-switch RTL held one grant low from reset. The two methods differed in 0 of 128 workload and switch pairs. The counts above come from the inserted RTL.

[Per-run counts and screen comparisons](cases.csv) · [Grant bit mapping](switch-sites.csv)
