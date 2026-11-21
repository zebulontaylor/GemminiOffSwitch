// Fast two-stage exact Verilator evaluator for single-site Mesh off-switch candidates.
// Stage 1: runs unclamped full Gemmini once per workload to capture exact golden Mesh I/O
//          and accumulator output row schedule, self-verifying the data-path extractor.
// Stage 2: runs standalone VMesh per candidate on the golden Mesh input trace. If any of the
//          six Mesh control outputs diverge from golden (or VMesh flags an error), falls back
//          to full VGemmini for exact end-to-end simulation.
#include "VGemmini.h"
#include "VGemmini_Mesh.h"
#include "VGemmini__Syms.h"
#include "VMesh.h"
#include "verilated.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr int DIM = 16;
constexpr int TILE_BYTES = DIM * DIM;
constexpr int NUM_WORKLOADS = 4;
constexpr int SHORT_DEADLINE = 1767;
constexpr int A_MEM = 0x1000;
constexpr int B_MEM = 0x2000;
constexpr int C_MEM = 0x3000;
constexpr uint64_t DRAM_BASE = 0x80000000ULL;
constexpr uint32_t A_SP = 0;
constexpr uint32_t B_SP = DIM;
constexpr uint32_t C_ACC = 0x80000000U;
constexpr uint32_t GARBAGE = 0xffffffffU;
constexpr uint32_t SCALE_ONE = 0x3f800000U;

struct Candidate {
    int grant_bit;
    uint16_t mesh_sig;
    uint16_t tile;
    uint8_t pe_sig;
    uint32_t mask;
};

struct LockResult {
    int workload = 0;
    int lock_bit = -1;
    int cycles = 0;
    int stock_tiles = 0;
    int returned_tiles = 0;
    int correct_tiles = 0;
    int wrong_tiles = 0;
    int wrong_bytes = 0;
    int unknown_bytes = 0;
    int missing_tiles = 0;
    int missing_bytes = 0;
    int stock_bytes = 0;
    int failed_bytes = 0;
    int invalid_request_cycle = 0;
    int masked_active_cycles = 0;
};

struct MeshInputs {
    uint8_t a[DIM];
    uint8_t b[DIM];
    uint8_t d[DIM];
    uint8_t dataflow[DIM];
    uint8_t propagate[DIM];
    uint8_t shift[DIM];
    uint8_t id[DIM];
    uint8_t last[DIM];
    uint8_t valid[DIM];
};

struct MeshOutputs {
    uint32_t b[DIM];
    uint32_t c[DIM];
    uint8_t out_valid;
    uint8_t out_dataflow;
    uint8_t out_id;
    uint8_t out_last;
};

struct RowWrite {
    int cycle;
    int tile;
    int row;
};

struct GoldenWorkload {
    int stock_tiles_short = 0;
    int stock_tiles_max = 0;
    std::vector<MeshInputs> mesh_in;   // index 0..max_cycles
    std::vector<MeshOutputs> mesh_out; // index 1..max_cycles (0 unused)
    std::vector<std::array<uint8_t, TILE_BYTES>> expected_tiles;
    // Per cycle c (1..max_cycles), list of (tile, row, col) entries whose Mesh output is emitted at cycle c.
    struct SampleTarget {
        uint16_t tile;
        uint8_t row;
        uint8_t col;
        uint8_t dataflow;
        uint8_t expected;
    };
    std::vector<uint32_t> target_offset; // size max_cycles + 2
    std::vector<SampleTarget> targets;
};

inline uint64_t descriptor(uint32_t addr) {
    return (uint64_t(DIM) << 48) | (uint64_t(DIM) << 32) | uint64_t(addr);
}

inline uint32_t xorshift(uint32_t x) {
    x ^= x << 13;
    x ^= x >> 17;
    return x ^ (x << 5);
}

inline int8_t nonzero_operand(uint32_t sample) {
    static constexpr int8_t kTable[4] = {-2, -1, 1, 2};
    return kTable[sample & 3U];
}

inline uint8_t clip_s20_to_u8(uint32_t raw20) {
    int32_t signed_val = static_cast<int32_t>(raw20 << 12) >> 12;
    if (signed_val > 127) signed_val = 127;
    if (signed_val < -128) signed_val = -128;
    return static_cast<uint8_t>(static_cast<int8_t>(signed_val));
}

// Macros to copy all 144 Mesh inputs and 36 Mesh outputs without hand-unrolling errors.
#define COPY_IN_0(dst, src) \
    (dst).a[0] = (src).off_switch_raw_io_in_a_0_0; \
    (dst).b[0] = (src).off_switch_raw_io_in_b_0_0; \
    (dst).d[0] = (src).off_switch_raw_io_in_d_0_0; \
    (dst).valid[0] = (src).off_switch_raw_io_in_valid_0_0; \
    (dst).dataflow[0] = (src).io_in_control_0_0_dataflow; \
    (dst).propagate[0] = (src).io_in_control_0_0_propagate; \
    (dst).shift[0] = (src).io_in_control_0_0_shift; \
    (dst).id[0] = (src).io_in_id_0_0; \
    (dst).last[0] = (src).io_in_last_0_0;

#define COPY_IN_I(dst, src, i) \
    (dst).a[i] = (src).io_in_a_##i##_0; \
    (dst).b[i] = (src).io_in_b_##i##_0; \
    (dst).d[i] = (src).io_in_d_##i##_0; \
    (dst).valid[i] = (src).io_in_valid_##i##_0; \
    (dst).dataflow[i] = (src).io_in_control_##i##_0_dataflow; \
    (dst).propagate[i] = (src).io_in_control_##i##_0_propagate; \
    (dst).shift[i] = (src).io_in_control_##i##_0_shift; \
    (dst).id[i] = (src).io_in_id_##i##_0; \
    (dst).last[i] = (src).io_in_last_##i##_0;

#define APPLY_IN_0(dst, src) \
    (dst).off_switch_raw_io_in_a_0_0 = (src).a[0]; \
    (dst).off_switch_raw_io_in_b_0_0 = (src).b[0]; \
    (dst).off_switch_raw_io_in_d_0_0 = (src).d[0]; \
    (dst).off_switch_raw_io_in_valid_0_0 = (src).valid[0]; \
    (dst).io_in_control_0_0_dataflow = (src).dataflow[0]; \
    (dst).io_in_control_0_0_propagate = (src).propagate[0]; \
    (dst).io_in_control_0_0_shift = (src).shift[0]; \
    (dst).io_in_id_0_0 = (src).id[0]; \
    (dst).io_in_last_0_0 = (src).last[0];

#define APPLY_IN_I(dst, src, i) \
    (dst).io_in_a_##i##_0 = (src).a[i]; \
    (dst).io_in_b_##i##_0 = (src).b[i]; \
    (dst).io_in_d_##i##_0 = (src).d[i]; \
    (dst).io_in_valid_##i##_0 = (src).valid[i]; \
    (dst).io_in_control_##i##_0_dataflow = (src).dataflow[i]; \
    (dst).io_in_control_##i##_0_propagate = (src).propagate[i]; \
    (dst).io_in_control_##i##_0_shift = (src).shift[i]; \
    (dst).io_in_id_##i##_0 = (src).id[i]; \
    (dst).io_in_last_##i##_0 = (src).last[i];

#define COPY_OUT_I(dst, src, i) \
    (dst).b[i] = (src).io_out_b_##i##_0; \
    (dst).c[i] = (src).io_out_c_##i##_0;

template <typename MeshObj>
inline void capture_mesh_inputs(const MeshObj& m, MeshInputs& out) {
    COPY_IN_0(out, m);
    COPY_IN_I(out, m, 1);  COPY_IN_I(out, m, 2);  COPY_IN_I(out, m, 3);
    COPY_IN_I(out, m, 4);  COPY_IN_I(out, m, 5);  COPY_IN_I(out, m, 6);
    COPY_IN_I(out, m, 7);  COPY_IN_I(out, m, 8);  COPY_IN_I(out, m, 9);
    COPY_IN_I(out, m, 10); COPY_IN_I(out, m, 11); COPY_IN_I(out, m, 12);
    COPY_IN_I(out, m, 13); COPY_IN_I(out, m, 14); COPY_IN_I(out, m, 15);
}

inline void apply_mesh_inputs(VMesh& m, const MeshInputs& in) {
    APPLY_IN_0(m, in);
    APPLY_IN_I(m, in, 1);  APPLY_IN_I(m, in, 2);  APPLY_IN_I(m, in, 3);
    APPLY_IN_I(m, in, 4);  APPLY_IN_I(m, in, 5);  APPLY_IN_I(m, in, 6);
    APPLY_IN_I(m, in, 7);  APPLY_IN_I(m, in, 8);  APPLY_IN_I(m, in, 9);
    APPLY_IN_I(m, in, 10); APPLY_IN_I(m, in, 11); APPLY_IN_I(m, in, 12);
    APPLY_IN_I(m, in, 13); APPLY_IN_I(m, in, 14); APPLY_IN_I(m, in, 15);
}

template <typename MeshObj>
inline void capture_mesh_outputs(const MeshObj& m, MeshOutputs& out) {
    COPY_OUT_I(out, m, 0);  COPY_OUT_I(out, m, 1);  COPY_OUT_I(out, m, 2);  COPY_OUT_I(out, m, 3);
    COPY_OUT_I(out, m, 4);  COPY_OUT_I(out, m, 5);  COPY_OUT_I(out, m, 6);  COPY_OUT_I(out, m, 7);
    COPY_OUT_I(out, m, 8);  COPY_OUT_I(out, m, 9);  COPY_OUT_I(out, m, 10); COPY_OUT_I(out, m, 11);
    COPY_OUT_I(out, m, 12); COPY_OUT_I(out, m, 13); COPY_OUT_I(out, m, 14); COPY_OUT_I(out, m, 15);
    out.out_valid = m.io_out_valid_0_0;
    out.out_dataflow = m.io_out_control_0_0_dataflow;
    out.out_id = m.io_out_id_0_0;
    out.out_last = m.io_out_last_0_0;
}

inline uint32_t read_mesh_col_data(const VMesh& m, int col, uint8_t dataflow) {
    switch (col) {
        case 0:  return dataflow ? m.io_out_b_0_0  : m.io_out_c_0_0;
        case 1:  return dataflow ? m.io_out_b_1_0  : m.io_out_c_1_0;
        case 2:  return dataflow ? m.io_out_b_2_0  : m.io_out_c_2_0;
        case 3:  return dataflow ? m.io_out_b_3_0  : m.io_out_c_3_0;
        case 4:  return dataflow ? m.io_out_b_4_0  : m.io_out_c_4_0;
        case 5:  return dataflow ? m.io_out_b_5_0  : m.io_out_c_5_0;
        case 6:  return dataflow ? m.io_out_b_6_0  : m.io_out_c_6_0;
        case 7:  return dataflow ? m.io_out_b_7_0  : m.io_out_c_7_0;
        case 8:  return dataflow ? m.io_out_b_8_0  : m.io_out_c_8_0;
        case 9:  return dataflow ? m.io_out_b_9_0  : m.io_out_c_9_0;
        case 10: return dataflow ? m.io_out_b_10_0 : m.io_out_c_10_0;
        case 11: return dataflow ? m.io_out_b_11_0 : m.io_out_c_11_0;
        case 12: return dataflow ? m.io_out_b_12_0 : m.io_out_c_12_0;
        case 13: return dataflow ? m.io_out_b_13_0 : m.io_out_c_13_0;
        case 14: return dataflow ? m.io_out_b_14_0 : m.io_out_c_14_0;
        default: return dataflow ? m.io_out_b_15_0 : m.io_out_c_15_0;
    }
}

struct Command {
    uint8_t funct;
    uint64_t rs1;
    uint64_t rs2;
};

struct HostLane {
    uint8_t memory[65536];
    int8_t tile_a[DIM][DIM];
    int8_t tile_b[DIM][DIM];
    int8_t expected_tile[DIM][DIM];
    uint32_t host_rng = 0x81234567U;
    int workload = 0;

    int tiles_checked = 0;
    int tiles_wrong = 0;
    int wrong_elements = 0;
    int cmd_count = 0;
    int gets = 0;
    int puts = 0;
    int invalid_request_cycle = 0;

    bool d_valid = false;
    bool d_is_read = false;
    uint8_t d_size = 0;
    uint8_t d_source = 0;
    uint32_t d_addr = 0;
    int d_index = 0;
    int d_beats = 0;
    int put_index = 0;

    enum class State { IDLE_AFTER_RESET, WAIT_CMD_READY, DROP_VALID, WAIT_BUSY, ISSUE_NEXT };
    State state = State::IDLE_AFTER_RESET;
    int phase = 0;
    Command current_cmd{};
    bool cmd_valid = false;

    void init_workload(int w) {
        std::memset(memory, 0, sizeof(memory));
        workload = w;
        host_rng = 0x81234567U;
        tiles_checked = 0;
        tiles_wrong = 0;
        wrong_elements = 0;
        cmd_count = 0;
        gets = 0;
        puts = 0;
        invalid_request_cycle = 0;
        d_valid = false;
        d_is_read = false;
        d_size = 0;
        d_source = 0;
        d_addr = 0;
        d_index = 0;
        d_beats = 0;
        put_index = 0;
        state = State::IDLE_AFTER_RESET;
        phase = 0;
        cmd_valid = false;
    }

    void initialize_tile(std::vector<std::array<uint8_t, TILE_BYTES>>* record_expected = nullptr) {
        for (int i = 0; i < DIM; i++) {
            for (int j = 0; j < DIM; j++) {
                uint32_t a_sample = xorshift(host_rng);
                host_rng = xorshift(a_sample);
                switch (workload) {
                    case 0:
                        tile_a[i][j] = static_cast<int8_t>(static_cast<int>(a_sample % 5U) - 2);
                        tile_b[i][j] = static_cast<int8_t>(static_cast<int>(host_rng % 5U) - 2);
                        break;
                    case 1:
                        tile_a[i][j] = nonzero_operand(a_sample);
                        tile_b[i][j] = nonzero_operand(host_rng);
                        break;
                    case 2:
                        tile_a[i][j] = (j == (i + tiles_checked) % DIM)
                            ? ((a_sample & 1U) ? int8_t(-1) : int8_t(1))
                            : int8_t(0);
                        tile_b[i][j] = static_cast<int8_t>(host_rng & 0xffU);
                        break;
                    case 3:
                        tile_a[i][j] = static_cast<int8_t>(static_cast<int>(a_sample & 0xfU) - 8);
                        tile_b[i][j] = static_cast<int8_t>(static_cast<int>(host_rng & 0xfU) - 8);
                        break;
                }
                memory[A_MEM + i * DIM + j] = static_cast<uint8_t>(tile_a[i][j]);
                memory[B_MEM + i * DIM + j] = static_cast<uint8_t>(tile_b[i][j]);
                memory[C_MEM + i * DIM + j] = 0x80U;
            }
        }
        std::array<uint8_t, TILE_BYTES> flat{};
        for (int i = 0; i < DIM; i++) {
            for (int j = 0; j < DIM; j++) {
                int sum = 0;
                for (int k = 0; k < DIM; k++) {
                    sum += static_cast<int>(tile_a[i][k]) * static_cast<int>(tile_b[k][j]);
                }
                if (sum > 127) sum = 127;
                if (sum < -128) sum = -128;
                expected_tile[i][j] = static_cast<int8_t>(sum);
                flat[i * DIM + j] = static_cast<uint8_t>(expected_tile[i][j]);
                if (workload != 0) {
                    memory[C_MEM + i * DIM + j] = static_cast<uint8_t>(~flat[i * DIM + j]);
                }
            }
        }
        if (record_expected) {
            record_expected->push_back(flat);
        }
    }

    void check_output() {
        bool mismatch = false;
        for (int i = 0; i < DIM; i++) {
            for (int j = 0; j < DIM; j++) {
                if (memory[C_MEM + i * DIM + j] != static_cast<uint8_t>(expected_tile[i][j])) {
                    wrong_elements++;
                    mismatch = true;
                }
            }
        }
        tiles_checked++;
        if (mismatch) tiles_wrong++;
    }

    Command next_command(std::vector<std::array<uint8_t, TILE_BYTES>>* record_expected = nullptr) {
        switch (phase) {
            case 0: return {0, (uint64_t(SCALE_ONE) << 32) | (uint64_t(DIM) << 16) | (1ULL << 8) | 1ULL, uint64_t(DIM)};
            case 1: return {0, (uint64_t(SCALE_ONE) << 32) | (1ULL << 16) | 4ULL, 0x0001000000000000ULL};
            case 2: return {0, 2ULL, (uint64_t(SCALE_ONE) << 32) | uint64_t(DIM)};
            case 3:
                initialize_tile(record_expected);
                return {2, DRAM_BASE + uint64_t(A_MEM), descriptor(A_SP)};
            case 4: return {2, DRAM_BASE + uint64_t(B_MEM), descriptor(B_SP)};
            case 5: return {6, descriptor(B_SP), descriptor(C_ACC)};
            case 6: return {4, descriptor(A_SP), descriptor(GARBAGE)};
            default: return {3, DRAM_BASE + uint64_t(C_MEM), descriptor(C_ACC)};
        }
    }

    inline void drive_d_channel(VGemmini& dut) {
        dut.auto_spad_id_out_a_ready = (!d_valid && invalid_request_cycle == 0) ? 1 : 0;
        dut.auto_spad_id_out_d_valid = d_valid ? 1 : 0;
        dut.auto_spad_id_out_d_bits_opcode = d_is_read ? 1 : 0;
        dut.auto_spad_id_out_d_bits_size = d_size;
        dut.auto_spad_id_out_d_bits_source = d_source;
        if (d_valid && d_is_read) {
            uint16_t base = static_cast<uint16_t>((d_addr & 0xfff0U) + d_index * 16);
            uint32_t w[4];
            std::memcpy(w, &memory[base], 16);
            dut.auto_spad_id_out_d_bits_data[0] = w[0];
            dut.auto_spad_id_out_d_bits_data[1] = w[1];
            dut.auto_spad_id_out_d_bits_data[2] = w[2];
            dut.auto_spad_id_out_d_bits_data[3] = w[3];
        } else {
            dut.auto_spad_id_out_d_bits_data[0] = 0;
            dut.auto_spad_id_out_d_bits_data[1] = 0;
            dut.auto_spad_id_out_d_bits_data[2] = 0;
            dut.auto_spad_id_out_d_bits_data[3] = 0;
        }
    }

    inline void drive_cmd(VGemmini& dut) {
        dut.io_cmd_valid = cmd_valid ? 1 : 0;
        dut.io_cmd_bits_inst_funct = current_cmd.funct;
        dut.io_cmd_bits_rs1 = current_cmd.rs1;
        dut.io_cmd_bits_rs2 = current_cmd.rs2;
    }
};

static void set_static_ports(VGemmini& dut) {
    dut.auto_spad_id_out_d_bits_param = 0;
    dut.auto_spad_id_out_d_bits_sink = 0;
    dut.auto_spad_id_out_d_bits_denied = 0;
    dut.auto_spad_id_out_d_bits_corrupt = 0;
    dut.io_cmd_bits_inst_rs2 = 0;
    dut.io_cmd_bits_inst_rs1 = 0;
    dut.io_cmd_bits_inst_xd = 0;
    dut.io_cmd_bits_inst_xs1 = 1;
    dut.io_cmd_bits_inst_xs2 = 1;
    dut.io_cmd_bits_inst_rd = 0;
    dut.io_cmd_bits_inst_opcode = 0x7b;
    dut.io_cmd_bits_status_debug = 0;
    dut.io_cmd_bits_status_cease = 0;
    dut.io_cmd_bits_status_wfi = 0;
    dut.io_cmd_bits_status_isa = 0;
    dut.io_cmd_bits_status_dprv = 3;
    dut.io_cmd_bits_status_dv = 0;
    dut.io_cmd_bits_status_prv = 3;
    dut.io_cmd_bits_status_v = 0;
    dut.io_cmd_bits_status_sd = 0;
    dut.io_cmd_bits_status_zero2 = 0;
    dut.io_cmd_bits_status_mpv = 0;
    dut.io_cmd_bits_status_gva = 0;
    dut.io_cmd_bits_status_mbe = 0;
    dut.io_cmd_bits_status_sbe = 0;
    dut.io_cmd_bits_status_sxl = 0;
    dut.io_cmd_bits_status_uxl = 0;
    dut.io_cmd_bits_status_sd_rv32 = 0;
    dut.io_cmd_bits_status_zero1 = 0;
    dut.io_cmd_bits_status_tsr = 0;
    dut.io_cmd_bits_status_tw = 0;
    dut.io_cmd_bits_status_tvm = 0;
    dut.io_cmd_bits_status_mxr = 0;
    dut.io_cmd_bits_status_sum = 0;
    dut.io_cmd_bits_status_mprv = 0;
    dut.io_cmd_bits_status_xs = 0;
    dut.io_cmd_bits_status_fs = 0;
    dut.io_cmd_bits_status_mpp = 3;
    dut.io_cmd_bits_status_vs = 0;
    dut.io_cmd_bits_status_spp = 0;
    dut.io_cmd_bits_status_mpie = 0;
    dut.io_cmd_bits_status_ube = 0;
    dut.io_cmd_bits_status_spie = 0;
    dut.io_cmd_bits_status_upie = 0;
    dut.io_cmd_bits_status_mie = 0;
    dut.io_cmd_bits_status_hie = 0;
    dut.io_cmd_bits_status_sie = 0;
    dut.io_cmd_bits_status_uie = 0;
    dut.io_resp_ready = 1;
    dut.io_ptw_0_req_ready = 1;
    dut.io_ptw_0_resp_valid = 0;
    dut.io_ptw_0_resp_bits_ae_ptw = 0;
    dut.io_ptw_0_resp_bits_ae_final = 0;
    dut.io_ptw_0_resp_bits_pf = 0;
    dut.io_ptw_0_resp_bits_gf = 0;
    dut.io_ptw_0_resp_bits_hr = 0;
    dut.io_ptw_0_resp_bits_hw = 0;
    dut.io_ptw_0_resp_bits_hx = 0;
    dut.io_ptw_0_resp_bits_pte_ppn = 0;
    dut.io_ptw_0_resp_bits_pte_d = 0;
    dut.io_ptw_0_resp_bits_pte_a = 0;
    dut.io_ptw_0_resp_bits_pte_g = 0;
    dut.io_ptw_0_resp_bits_pte_u = 0;
    dut.io_ptw_0_resp_bits_pte_x = 0;
    dut.io_ptw_0_resp_bits_pte_w = 0;
    dut.io_ptw_0_resp_bits_pte_r = 0;
    dut.io_ptw_0_resp_bits_pte_v = 0;
    dut.io_ptw_0_resp_bits_level = 0;
    dut.io_ptw_0_resp_bits_homogeneous = 0;
    dut.io_ptw_0_ptbr_mode = 0;
    dut.io_ptw_0_pmp_0_cfg_l = 0;
    dut.io_ptw_0_pmp_0_cfg_a = 3;
    dut.io_ptw_0_pmp_0_cfg_x = 0;
    dut.io_ptw_0_pmp_0_cfg_w = 1;
    dut.io_ptw_0_pmp_0_cfg_r = 1;
    dut.io_ptw_0_pmp_0_addr = 0x20001fffU;
    dut.io_ptw_0_pmp_0_mask = 0xffffU;
    dut.io_ptw_0_pmp_1_cfg_l = 0; dut.io_ptw_0_pmp_1_cfg_a = 0; dut.io_ptw_0_pmp_1_cfg_x = 0;
    dut.io_ptw_0_pmp_1_cfg_w = 0; dut.io_ptw_0_pmp_1_cfg_r = 0; dut.io_ptw_0_pmp_1_addr = 0; dut.io_ptw_0_pmp_1_mask = 0;
    dut.io_ptw_0_pmp_2_cfg_l = 0; dut.io_ptw_0_pmp_2_cfg_a = 0; dut.io_ptw_0_pmp_2_cfg_x = 0;
    dut.io_ptw_0_pmp_2_cfg_w = 0; dut.io_ptw_0_pmp_2_cfg_r = 0; dut.io_ptw_0_pmp_2_addr = 0; dut.io_ptw_0_pmp_2_mask = 0;
    dut.io_ptw_0_pmp_3_cfg_l = 0; dut.io_ptw_0_pmp_3_cfg_a = 0; dut.io_ptw_0_pmp_3_cfg_x = 0;
    dut.io_ptw_0_pmp_3_cfg_w = 0; dut.io_ptw_0_pmp_3_cfg_r = 0; dut.io_ptw_0_pmp_3_addr = 0; dut.io_ptw_0_pmp_3_mask = 0;
    dut.io_ptw_0_pmp_4_cfg_l = 0; dut.io_ptw_0_pmp_4_cfg_a = 0; dut.io_ptw_0_pmp_4_cfg_x = 0;
    dut.io_ptw_0_pmp_4_cfg_w = 0; dut.io_ptw_0_pmp_4_cfg_r = 0; dut.io_ptw_0_pmp_4_addr = 0; dut.io_ptw_0_pmp_4_mask = 0;
    dut.io_ptw_0_pmp_5_cfg_l = 0; dut.io_ptw_0_pmp_5_cfg_a = 0; dut.io_ptw_0_pmp_5_cfg_x = 0;
    dut.io_ptw_0_pmp_5_cfg_w = 0; dut.io_ptw_0_pmp_5_cfg_r = 0; dut.io_ptw_0_pmp_5_addr = 0; dut.io_ptw_0_pmp_5_mask = 0;
    dut.io_ptw_0_pmp_6_cfg_l = 0; dut.io_ptw_0_pmp_6_cfg_a = 0; dut.io_ptw_0_pmp_6_cfg_x = 0;
    dut.io_ptw_0_pmp_6_cfg_w = 0; dut.io_ptw_0_pmp_6_cfg_r = 0; dut.io_ptw_0_pmp_6_addr = 0; dut.io_ptw_0_pmp_6_mask = 0;
    dut.io_ptw_0_pmp_7_cfg_l = 0; dut.io_ptw_0_pmp_7_cfg_a = 0; dut.io_ptw_0_pmp_7_cfg_x = 0;
    dut.io_ptw_0_pmp_7_cfg_w = 0; dut.io_ptw_0_pmp_7_cfg_r = 0; dut.io_ptw_0_pmp_7_addr = 0; dut.io_ptw_0_pmp_7_mask = 0;
}

static LockResult make_result(int workload, int lock_bit, int cycles, int stock_tiles,
                              int returned_tiles, int wrong_tiles, int wrong_bytes,
                              int invalid_request_cycle, int masked_active_cycles) {
    LockResult res;
    res.workload = workload;
    res.lock_bit = lock_bit;
    res.cycles = cycles;
    res.stock_tiles = stock_tiles;
    res.returned_tiles = returned_tiles;
    res.correct_tiles = returned_tiles - wrong_tiles;
    res.wrong_tiles = wrong_tiles;
    res.wrong_bytes = wrong_bytes;
    res.unknown_bytes = 0;
    res.missing_tiles = stock_tiles > returned_tiles ? (stock_tiles - returned_tiles) : 0;
    res.missing_bytes = res.missing_tiles * TILE_BYTES;
    res.stock_bytes = stock_tiles * TILE_BYTES;
    res.failed_bytes = wrong_bytes + res.missing_bytes;
    res.invalid_request_cycle = invalid_request_cycle;
    res.masked_active_cycles = masked_active_cycles;
    return res;
}

static bool run_full_gemmini(int workload, int max_cycles, const Candidate& cand,
                             int stock_tiles_short, int stock_tiles_max,
                             std::vector<LockResult>& out_results,
                             GoldenWorkload* capture_golden = nullptr) {
    VerilatedContext ctx;
    ctx.threads(1);
    ctx.fatalOnError(false);
    VGemmini dut(&ctx);
    HostLane lane;
    lane.init_workload(workload);

    set_static_ports(dut);
    dut.off_switch_mesh_sig = cand.mesh_sig;
    dut.off_switch_tile = cand.tile;
    dut.off_switch_pe_sig = cand.pe_sig;
    dut.off_switch_mask = cand.mask;
    dut.reset = 1;
    dut.clock = 0;
    lane.drive_d_channel(dut);
    lane.drive_cmd(dut);
    dut.eval();

    for (int r = 0; r < 8; r++) {
        dut.clock = 1;
        dut.eval();
        dut.clock = 0;
        dut.eval();
    }
    dut.reset = 0;
    dut.eval();

    const VGemmini_Mesh* mesh_ptr = &dut.rootp->vlSymsp->TOP__Gemmini__DOT__ex_controller__DOT__mesh__DOT__mesh;
    std::vector<RowWrite> row_writes;
    if (capture_golden) {
        capture_golden->mesh_in.resize(max_cycles + 1);
        capture_golden->mesh_out.resize(max_cycles + 1);
        capture_mesh_inputs(*mesh_ptr, capture_golden->mesh_in[0]);
    }

    int masked_active = 0;
    for (int cycle = 1; cycle <= max_cycles; cycle++) {
        const bool pre_cmd_ready = dut.io_cmd_ready != 0;
        const bool pre_d_ready = dut.auto_spad_id_out_d_ready != 0;
        const bool pre_a_valid = dut.auto_spad_id_out_a_valid != 0;
        const bool pre_a_ready = !lane.d_valid && lane.invalid_request_cycle == 0;
        const uint8_t pre_a_opcode = dut.auto_spad_id_out_a_bits_opcode;
        const uint8_t pre_a_size = dut.auto_spad_id_out_a_bits_size;
        const uint8_t pre_a_source = dut.auto_spad_id_out_a_bits_source;
        const uint32_t pre_a_addr = dut.auto_spad_id_out_a_bits_address;
        const uint16_t pre_a_mask = dut.auto_spad_id_out_a_bits_mask;
        uint32_t pre_a_data[4];
        if (pre_a_valid && pre_a_ready && (pre_a_opcode == 0 || pre_a_opcode == 1)) {
            pre_a_data[0] = dut.auto_spad_id_out_a_bits_data[0];
            pre_a_data[1] = dut.auto_spad_id_out_a_bits_data[1];
            pre_a_data[2] = dut.auto_spad_id_out_a_bits_data[2];
            pre_a_data[3] = dut.auto_spad_id_out_a_bits_data[3];
        }
        if (capture_golden) {
            if (dut.io_interrupt) {
                std::fprintf(stderr, "Golden Gemmini raised a TLB fault at cycle %d\n", cycle);
                return false;
            }
            capture_mesh_outputs(*mesh_ptr, capture_golden->mesh_out[cycle]);
            if (dut.off_switch_acc_row_valid) {
                row_writes.push_back({cycle, lane.tiles_checked, static_cast<int>(dut.off_switch_acc_row_addr)});
            }
        }

        dut.clock = 1;
        dut.eval();

        if (lane.cmd_valid && pre_cmd_ready) lane.cmd_count++;
        if (lane.d_valid && pre_d_ready) {
            if (lane.d_index + 1 == lane.d_beats) lane.d_valid = false;
            else lane.d_index++;
        }
        if (pre_a_valid && pre_a_ready) {
            int beats = pre_a_size > 4 ? (1 << (pre_a_size - 4)) : 1;
            if ((pre_a_addr >> 16) != 0x8000U || pre_a_size > 6 ||
                !(pre_a_opcode == 0 || pre_a_opcode == 1 || pre_a_opcode == 4)) {
                if (capture_golden) {
                    std::fprintf(stderr, "Invalid golden TL request at cycle %d\n", cycle);
                    return false;
                }
                lane.invalid_request_cycle = cycle;
            } else if (pre_a_opcode == 4) {
                lane.gets++;
                lane.d_valid = true;
                lane.d_is_read = true;
                lane.d_beats = beats;
                lane.d_size = pre_a_size;
                lane.d_source = pre_a_source;
                lane.d_addr = pre_a_addr;
                lane.d_index = 0;
            } else {
                uint16_t base = static_cast<uint16_t>((pre_a_addr & 0xfff0U) + lane.put_index * 16);
                const uint8_t* bytes = reinterpret_cast<const uint8_t*>(pre_a_data);
                for (int b = 0; b < 16; b++) {
                    if (pre_a_mask & (1U << b)) lane.memory[base + b] = bytes[b];
                }
                if (lane.put_index + 1 == beats) {
                    lane.puts++;
                    lane.put_index = 0;
                    lane.d_valid = true;
                    lane.d_is_read = false;
                    lane.d_size = pre_a_size;
                    lane.d_source = pre_a_source;
                    lane.d_beats = 1;
                    lane.d_index = 0;
                } else {
                    lane.put_index++;
                }
            }
        }

        if (lane.state == HostLane::State::WAIT_CMD_READY && pre_cmd_ready) {
            lane.state = HostLane::State::DROP_VALID;
        }

        lane.drive_d_channel(dut);
        dut.clock = 0;
        dut.eval();

        if (capture_golden) {
            capture_mesh_inputs(*mesh_ptr, capture_golden->mesh_in[cycle]);
        }
        if (cand.grant_bit >= 0 && dut.off_switch_raw_active) {
            masked_active++;
        }

        if (cycle == SHORT_DEADLINE || cycle == max_cycles) {
            if (capture_golden) {
                if (lane.tiles_checked < 2 || lane.tiles_wrong != 0) {
                    std::fprintf(stderr, "Golden workload %d failed arithmetic/progress check at cycle %d\n",
                                 workload, cycle);
                    return false;
                }
                if (cycle == SHORT_DEADLINE) capture_golden->stock_tiles_short = lane.tiles_checked;
                if (cycle == max_cycles) capture_golden->stock_tiles_max = lane.tiles_checked;
            }
            const int stock = capture_golden
                ? lane.tiles_checked
                : (cycle == SHORT_DEADLINE ? stock_tiles_short : stock_tiles_max);
            out_results.push_back(make_result(
                workload, cand.grant_bit, cycle, stock,
                lane.tiles_checked, lane.tiles_wrong, lane.wrong_elements,
                lane.invalid_request_cycle, masked_active));
        }

        if (cycle == max_cycles) break;

        bool cmd_changed = false;
        switch (lane.state) {
            case HostLane::State::IDLE_AFTER_RESET:
            case HostLane::State::ISSUE_NEXT:
                lane.current_cmd = lane.next_command(capture_golden ? &capture_golden->expected_tiles : nullptr);
                lane.cmd_valid = true;
                lane.state = HostLane::State::WAIT_CMD_READY;
                cmd_changed = true;
                break;
            case HostLane::State::WAIT_CMD_READY:
                break;
            case HostLane::State::DROP_VALID:
                lane.cmd_valid = false;
                cmd_changed = true;
                if (lane.phase == 7) {
                    lane.state = HostLane::State::WAIT_BUSY;
                } else {
                    lane.phase++;
                    lane.state = HostLane::State::ISSUE_NEXT;
                }
                break;
            case HostLane::State::WAIT_BUSY:
                if (dut.io_busy == 0) {
                    lane.check_output();
                    lane.phase = 3;
                    lane.state = HostLane::State::ISSUE_NEXT;
                }
                break;
        }
        if (cmd_changed) {
            lane.drive_cmd(dut);
            dut.eval();
        }
    }

    dut.final();
    if (ctx.gotError()) {
        std::fprintf(stderr, "VGemmini error on workload=%d lock_bit=%d\n", workload, cand.grant_bit);
        return false;
    }

    if (capture_golden) {
        // Build per-cycle Mesh output sampling schedule and self-verify against golden expected_tiles.
        std::vector<std::vector<GoldenWorkload::SampleTarget>> by_cycle(max_cycles + 1);
        int verified_tiles = 0;
        std::vector<int> row_seen(capture_golden->stock_tiles_max, 0);
        for (const auto& rw : row_writes) {
            if (rw.tile >= capture_golden->stock_tiles_max) continue;
            if (rw.row < 0 || rw.row >= DIM) {
                std::fprintf(stderr, "Invalid golden acc row %d\n", rw.row);
                return false;
            }
            row_seen[rw.tile] |= (1 << rw.row);
            for (int col = 0; col < DIM; col++) {
                const int mesh_cycle = rw.cycle - (DIM - 1 - col);
                if (mesh_cycle < 1 || mesh_cycle > max_cycles) {
                    std::fprintf(stderr, "Out-of-range mesh cycle %d for tile %d row %d col %d\n",
                                 mesh_cycle, rw.tile, rw.row, col);
                    return false;
                }
                const auto& mo = capture_golden->mesh_out[mesh_cycle];
                const uint32_t raw20 = mo.out_dataflow ? mo.b[col] : mo.c[col];
                const uint8_t extracted = clip_s20_to_u8(raw20);
                const uint8_t expected = capture_golden->expected_tiles[rw.tile][rw.row * DIM + col];
                if (extracted != expected) {
                    std::fprintf(stderr,
                                 "Golden Mesh extractor mismatch on workload %d tile %d row %d col %d: got %u expected %u\n",
                                 workload, rw.tile, rw.row, col, extracted, expected);
                    return false;
                }
                by_cycle[mesh_cycle].push_back({
                    static_cast<uint16_t>(rw.tile),
                    static_cast<uint8_t>(rw.row),
                    static_cast<uint8_t>(col),
                    mo.out_dataflow,
                    expected,
                });
            }
        }
        for (int t = 0; t < capture_golden->stock_tiles_max; t++) {
            if (row_seen[t] != ((1 << DIM) - 1)) {
                std::fprintf(stderr, "Golden workload %d tile %d missing acc rows (mask=0x%x)\n",
                             workload, t, row_seen[t]);
                return false;
            }
            verified_tiles++;
        }
        if (verified_tiles != capture_golden->stock_tiles_max) {
            std::fprintf(stderr, "Golden workload %d tile count mismatch\n", workload);
            return false;
        }
        capture_golden->target_offset.assign(max_cycles + 2, 0);
        capture_golden->targets.clear();
        for (int c = 1; c <= max_cycles; c++) {
            capture_golden->target_offset[c] = static_cast<uint32_t>(capture_golden->targets.size());
            capture_golden->targets.insert(capture_golden->targets.end(), by_cycle[c].begin(), by_cycle[c].end());
        }
        capture_golden->target_offset[max_cycles + 1] = static_cast<uint32_t>(capture_golden->targets.size());
    }
    return true;
}

// Returns true if standalone VMesh completed with zero control divergence (exact result written).
// Returns false if any of the 6 Mesh control outputs diverged (caller must fall back to VGemmini).
static bool try_fast_mesh_trial(int workload, int max_cycles, const Candidate& cand,
                                const GoldenWorkload& golden,
                                std::vector<LockResult>& out_results) {
    VerilatedContext ctx;
    ctx.threads(1);
    ctx.fatalOnError(false);
    VMesh dut(&ctx);

    dut.off_switch_mesh_sig = cand.mesh_sig;
    dut.off_switch_tile = cand.tile;
    dut.off_switch_pe_sig = cand.pe_sig;
    dut.off_switch_mask = cand.mask;
    dut.reset = 1;
    dut.clock = 0;
    dut.eval();
    for (int r = 0; r < 8; r++) {
        dut.clock = 1;
        dut.eval();
        dut.clock = 0;
        dut.eval();
    }
    dut.reset = 0;
    apply_mesh_inputs(dut, golden.mesh_in[0]);
    dut.eval();

    std::vector<uint16_t> tile_wrong_bytes(golden.stock_tiles_max, 0);
    int masked_active = 0;
    int masked_active_short = 0;

    for (int cycle = 1; cycle <= max_cycles; cycle++) {
        const auto& ref_out = golden.mesh_out[cycle];
        if (dut.io_out_valid_0_0 != ref_out.out_valid ||
            dut.io_out_control_0_0_dataflow != ref_out.out_dataflow ||
            dut.io_out_id_0_0 != ref_out.out_id ||
            dut.io_out_last_0_0 != ref_out.out_last) {
            dut.final();
            return false;
        }

        const uint32_t begin = golden.target_offset[cycle];
        const uint32_t end = golden.target_offset[cycle + 1];
        for (uint32_t i = begin; i < end; i++) {
            const auto& tgt = golden.targets[i];
            const uint32_t raw20 = read_mesh_col_data(dut, tgt.col, tgt.dataflow);
            if (clip_s20_to_u8(raw20) != tgt.expected) {
                tile_wrong_bytes[tgt.tile]++;
            }
        }

        dut.clock = 1;
        dut.eval();
        apply_mesh_inputs(dut, golden.mesh_in[cycle]);
        dut.clock = 0;
        dut.eval();

        if (dut.off_switch_raw_active) {
            masked_active++;
        }
        if (cycle == SHORT_DEADLINE) {
            masked_active_short = masked_active;
        }
    }

    dut.final();
    if (ctx.gotError()) {
        return false;
    }

    auto emit_checkpoint = [&](int checkpoint_cycles, int stock_tiles, int active_count) {
        int wrong_tiles = 0;
        int wrong_bytes = 0;
        for (int t = 0; t < stock_tiles; t++) {
            if (tile_wrong_bytes[t] != 0) {
                wrong_tiles++;
                wrong_bytes += tile_wrong_bytes[t];
            }
        }
        out_results.push_back(make_result(
            workload, cand.grant_bit, checkpoint_cycles, stock_tiles,
            stock_tiles, wrong_tiles, wrong_bytes, 0, active_count));
    };

    if (max_cycles > SHORT_DEADLINE) {
        emit_checkpoint(SHORT_DEADLINE, golden.stock_tiles_short, masked_active_short);
    }
    emit_checkpoint(max_cycles, golden.stock_tiles_max, masked_active);
    return true;
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 5) {
        std::fprintf(stderr, "Usage: mesh_screen CANDIDATES_CSV RESULTS_CSV CYCLES JOBS\n");
        return 2;
    }
    const std::string cand_path = argv[1];
    const std::string out_path = argv[2];
    const int max_cycles = std::stoi(argv[3]);
    const int jobs = std::stoi(argv[4]);
    if (max_cycles < SHORT_DEADLINE || jobs < 1) {
        std::fprintf(stderr, "CYCLES must be >= %d and JOBS >= 1\n", SHORT_DEADLINE);
        return 2;
    }

    std::vector<Candidate> candidates;
    {
        std::ifstream in(cand_path);
        if (!in) {
            std::fprintf(stderr, "Failed to open %s\n", cand_path.c_str());
            return 2;
        }
        std::string line;
        if (!std::getline(in, line)) {
            std::fprintf(stderr, "Empty candidate table %s\n", cand_path.c_str());
            return 2;
        }
        while (std::getline(in, line)) {
            if (line.empty()) continue;
            Candidate c{};
            int gb = 0, ms = 0, tl = 0, ps = 0;
            unsigned int m = 0;
            if (std::sscanf(line.c_str(), "%d,%d,%d,%d,%x", &gb, &ms, &tl, &ps, &m) != 5) {
                std::fprintf(stderr, "Malformed candidate row: %s\n", line.c_str());
                return 2;
            }
            c.grant_bit = gb;
            c.mesh_sig = static_cast<uint16_t>(ms);
            c.tile = static_cast<uint16_t>(tl);
            c.pe_sig = static_cast<uint8_t>(ps);
            c.mask = m;
            candidates.push_back(c);
        }
    }
    if (candidates.empty()) {
        std::fprintf(stderr, "No candidates in %s\n", cand_path.c_str());
        return 2;
    }

    const int checkpoints_per_case = (max_cycles > SHORT_DEADLINE) ? 2 : 1;
    std::array<GoldenWorkload, NUM_WORKLOADS> goldens;
    std::array<std::vector<LockResult>, NUM_WORKLOADS> control_results;
    const Candidate no_fault{-1, 4095, 511, 15, 0xffffffffU};

    const auto t_start = std::chrono::steady_clock::now();
    // Run the 4 golden workloads in parallel (up to 4 threads).
    std::atomic<bool> golden_ok{true};
    {
        std::vector<std::thread> gthreads;
        for (int w = 0; w < NUM_WORKLOADS; w++) {
            gthreads.emplace_back([&, w]() {
                if (!run_full_gemmini(w, max_cycles, no_fault, 0, 0, control_results[w], &goldens[w])) {
                    golden_ok.store(false);
                }
            });
        }
        for (auto& th : gthreads) th.join();
    }
    if (!golden_ok.load()) {
        return 1;
    }

    struct Task {
        int workload;
        size_t cand_index;
    };
    std::vector<Task> tasks;
    tasks.reserve(NUM_WORKLOADS * candidates.size());
    for (int w = 0; w < NUM_WORKLOADS; w++) {
        for (size_t i = 0; i < candidates.size(); i++) {
            tasks.push_back({w, i});
        }
    }

    std::vector<LockResult> results(tasks.size() * checkpoints_per_case);
    std::atomic<size_t> next_idx{0};
    std::atomic<size_t> fallback_count{0};
    std::atomic<bool> worker_ok{true};

    const int num_threads = std::min<int>(jobs, static_cast<int>(tasks.size()));
    std::vector<std::thread> workers;
    workers.reserve(num_threads);
    for (int t = 0; t < num_threads; t++) {
        workers.emplace_back([&]() {
            std::vector<LockResult> local;
            local.reserve(2);
            while (worker_ok.load(std::memory_order_relaxed)) {
                const size_t idx = next_idx.fetch_add(1, std::memory_order_relaxed);
                if (idx >= tasks.size()) break;
                const auto& task = tasks[idx];
                const auto& cand = candidates[task.cand_index];
                const auto& golden = goldens[task.workload];
                local.clear();
                if (!try_fast_mesh_trial(task.workload, max_cycles, cand, golden, local)) {
                    fallback_count.fetch_add(1, std::memory_order_relaxed);
                    local.clear();
                    if (!run_full_gemmini(task.workload, max_cycles, cand,
                                          golden.stock_tiles_short, golden.stock_tiles_max, local)) {
                        worker_ok.store(false);
                        return;
                    }
                }
                if (static_cast<int>(local.size()) != checkpoints_per_case) {
                    std::fprintf(stderr, "Unexpected checkpoint count for workload=%d lock_bit=%d\n",
                                 task.workload, cand.grant_bit);
                    worker_ok.store(false);
                    return;
                }
                for (int k = 0; k < checkpoints_per_case; k++) {
                    results[idx * checkpoints_per_case + k] = local[k];
                }
            }
        });
    }
    for (auto& th : workers) th.join();
    if (!worker_ok.load()) {
        return 1;
    }

    const double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();

    std::ofstream out(out_path);
    if (!out) {
        std::fprintf(stderr, "Failed to open %s for writing\n", out_path.c_str());
        return 2;
    }
    out << "workload,lock_bit,cycles,stock_tiles,returned_tiles,correct_tiles,wrong_tiles,"
           "wrong_bytes,unknown_bytes,missing_tiles,missing_bytes,stock_bytes,failed_bytes,"
           "invalid_request_cycle,masked_active_cycles\n";
    auto write_row = [&](const LockResult& r) {
        out << r.workload << ',' << r.lock_bit << ',' << r.cycles << ',' << r.stock_tiles << ','
            << r.returned_tiles << ',' << r.correct_tiles << ',' << r.wrong_tiles << ','
            << r.wrong_bytes << ',' << r.unknown_bytes << ',' << r.missing_tiles << ','
            << r.missing_bytes << ',' << r.stock_bytes << ',' << r.failed_bytes << ','
            << r.invalid_request_cycle << ',' << r.masked_active_cycles << '\n';
    };
    for (int w = 0; w < NUM_WORKLOADS; w++) {
        for (const auto& r : control_results[w]) write_row(r);
    }
    for (const auto& r : results) write_row(r);
    out.flush();
    if (!out) {
        std::fprintf(stderr, "Failed writing %s\n", out_path.c_str());
        return 2;
    }

    std::printf("MESH_SCREEN candidates=%zu trials=%zu fallbacks=%zu cycles=%d jobs=%d seconds=%.3f trials_per_sec=%.1f\n",
                candidates.size(), tasks.size(), fallback_count.load(), max_cycles, num_threads,
                elapsed, tasks.size() / elapsed);
    return 0;
}
