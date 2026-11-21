# Selected wire roles

Coordinates are zero-based (row, column) in the 16 × 16 tile mesh. Each tile contains a single PE. WS and OS mean weight-stationary and output-stationary.

| Rank | Path | Meaning and RTL evidence | Group |
| ---: | --- | --- | --- |
| 1 | `ex_controller.mesh.mesh.io_in_valid_0_0` | Mesh valid input driven by the inverse of the wrapper pause flag (top edge, column 0). `MeshWithDelays.sv:3185` | Mesh control |
| 2 | `ex_controller.mesh.mesh.r_1027_0_dataflow` | Pipeline register for the WS/OS mode selector from tile (15, 0) to mesh output `io_out_control_0_0_dataflow`. `Mesh.sv:7991`, `Mesh.sv:13690` The wrapper uses it to select B for WS or C for OS across the response. `MeshWithDelays.sv:3336` | Mesh control |
| 3 | `ex_controller.mesh.mesh.io_in_a_0_0[0]` | Bit 0 of horizontal A input selected from the transposer output or A buffer (left edge, row 0). `MeshWithDelays.sv:3022` | A datapath |
| 4 | `ex_controller.mesh.mesh.r_131_0[0]` | Bit 0 of the horizontal A pipeline register from tile (8, 2) into tile (8, 3). `Mesh.sv:5267`, `Mesh.sv:10909` | A datapath |
| 5 | `ex_controller.mesh.mesh.r_151_0[0]` | Bit 0 of the horizontal A pipeline register from tile (9, 6) into tile (9, 7). `Mesh.sv:5287`, `Mesh.sv:11349` | A datapath |
| 6 | `ex_controller.mesh.mesh.mesh_0_4_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from the mesh input into tile (0, 4). `Mesh.sv:5845`, `Mesh.sv:8119` | Mesh control |
| 7 | `ex_controller.mesh.mesh.mesh_0_12_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from the mesh input into tile (0, 12). `Mesh.sv:6752`, `Mesh.sv:8294` | Mesh control |
| 8 | `ex_controller.mesh.mesh.mesh_1_12_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (0, 12) into tile (1, 12). `Mesh.sv:6759`, `Mesh.sv:8646` | Mesh control |
| 9 | `ex_controller.mesh.mesh.mesh_2_7_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (1, 7) into tile (2, 7). `Mesh.sv:6195`, `Mesh.sv:8889` | Mesh control |
| 10 | `ex_controller.mesh.mesh.mesh_2_10_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (1, 10) into tile (2, 10). `Mesh.sv:6531`, `Mesh.sv:8955` | Mesh control |
| 11 | `ex_controller.mesh.mesh.mesh_3_4_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (2, 4) into tile (3, 4). `Mesh.sv:5865`, `Mesh.sv:9174` | Mesh control |
| 12 | `ex_controller.mesh.mesh.mesh_4_8_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (3, 8) into tile (4, 8). `Mesh.sv:6320`, `Mesh.sv:9614` | Mesh control |
| 13 | `ex_controller.mesh.mesh.mesh_6_15_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (5, 15) into tile (6, 15). `Mesh.sv:7148`, `Mesh.sv:10472` | Mesh control |
| 14 | `ex_controller.mesh.mesh.mesh_6_14_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (5, 14) into tile (6, 14). `Mesh.sv:7031`, `Mesh.sv:10451` | Mesh control |
| 15 | `ex_controller.mesh.mesh.mesh_6_7_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (5, 7) into tile (6, 7). `Mesh.sv:6222`, `Mesh.sv:10296` | Mesh control |
| 16 | `ex_controller.mesh.mesh.mesh_8_4_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (7, 4) into tile (8, 4). `Mesh.sv:5901`, `Mesh.sv:10935` | Mesh control |
| 17 | `ex_controller.mesh.mesh.mesh_9_9_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (8, 9) into tile (9, 9). `Mesh.sv:6468`, `Mesh.sv:11397` | Mesh control |
| 18 | `ex_controller.mesh.mesh.pipe_b_432_0[3]` | Bit 3 of the vertical D preload pipeline register carrying WS weights or OS preload values from the mesh input (top edge, column 11) into tile (0, 11). `Mesh.sv:6632`, `Mesh.sv:8271` | D/C datapath |
| 19 | `ex_controller.mesh.mesh.mesh_10_12_io_in_control_0_propagate_pipe_b` | Pipeline register for the PE bank selector from tile (9, 12) into tile (10, 12). `Mesh.sv:6823`, `Mesh.sv:11815` | Mesh control |
| 20 | `ex_controller.mesh.mesh._mesh_1_10_io_out_c_0[3]` | Bit 3 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (1, 10). `Mesh.sv:8608` | D/C datapath |
| 21 | `ex_controller.mesh.mesh.mesh_10_9_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (9, 9) into tile (10, 9). `Mesh.sv:6474`, `Mesh.sv:11748` | Mesh control |
| 22 | `ex_controller.mesh.mesh.io_in_d_0_0[4]` | Bit 4 of vertical D preload input selected from the transposer output or D buffer (top edge, column 0). `MeshWithDelays.sv:3056` | D/C datapath |
| 23 | `ex_controller.mesh.mesh._mesh_4_10_io_out_c_0[7]` | Bit 7 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (4, 10). `Mesh.sv:9664` | D/C datapath |
| 24 | `ex_controller.mesh.mesh.mesh_11_12_io_in_control_0_dataflow_pipe_b` | Pipeline register for the WS/OS mode selector from tile (10, 12) into tile (11, 12). `Mesh.sv:6830`, `Mesh.sv:12166` | Mesh control |
| 25 | `ex_controller.mesh.mesh._mesh_1_4_io_out_c_0[5]` | Bit 5 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (1, 4). `Mesh.sv:8476` | D/C datapath |
| 26 | `ex_controller.mesh.mesh._mesh_2_0_io_out_c_0[5]` | Bit 5 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (2, 0). `Mesh.sv:8740` | D/C datapath |
| 27 | `ex_controller.mesh.mesh._mesh_4_7_io_out_c_0[1]` | Bit 1 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (4, 7). `Mesh.sv:9598` | D/C datapath |
| 28 | `ex_controller.mesh.mesh.io_in_d_0_0[6]` | Bit 6 of vertical D preload input selected from the transposer output or D buffer (top edge, column 0). `MeshWithDelays.sv:3056` | D/C datapath |
| 29 | `ex_controller.mesh.mesh.pipe_b_450_0[0]` | Bit 0 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS, registered from tile (1, 12) into tile (2, 12). `Mesh.sv:6764`, `Mesh.sv:8997` | D/C datapath |
| 30 | `ex_controller.mesh.mesh._mesh_2_5_io_out_c_0[4]` | Bit 4 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (2, 5). `Mesh.sv:8850` | D/C datapath |
| 31 | `ex_controller.mesh.mesh.pipe_b_386_0[5]` | Bit 5 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS, registered from tile (1, 8) into tile (2, 8). `Mesh.sv:6304`, `Mesh.sv:8909` | D/C datapath |
| 32 | `ex_controller.mesh.mesh._mesh_4_3_io_out_c_0[7]` | Bit 7 of vertical C stream carrying propagated weights in WS or shifted and saturated results in OS leaving tile (4, 3). `Mesh.sv:9510` | D/C datapath |

## Site counts by role

| Group | Sites |
| --- | ---: |
| Mesh control | 17 |
| D/C datapath | 12 |
| A datapath | 3 |
| Total | 32 |

Mesh control covers nine dataflow selectors, seven propagate selectors, and one validity input. The D/C datapath has three D preload input sites and nine sites on the vertical C stream.

The Scala implementations define [mesh chaining and output capture](https://github.com/ucb-bar/gemmini/blob/b82d49cca798a59f010488a6f6de7243f0616536/src/main/scala/gemmini/Mesh.scala), [tile-to-PE wiring](https://github.com/ucb-bar/gemmini/blob/b82d49cca798a59f010488a6f6de7243f0616536/src/main/scala/gemmini/Tile.scala), [PE dataflow and bank selection](https://github.com/ucb-bar/gemmini/blob/b82d49cca798a59f010488a6f6de7243f0616536/src/main/scala/gemmini/PE.scala), [wrapper input skew and output selection](https://github.com/ucb-bar/gemmini/blob/b82d49cca798a59f010488a6f6de7243f0616536/src/main/scala/gemmini/MeshWithDelays.scala), and [signed shift rounding and saturation](https://github.com/ucb-bar/gemmini/blob/b82d49cca798a59f010488a6f6de7243f0616536/src/main/scala/gemmini/Arithmetic.scala).
