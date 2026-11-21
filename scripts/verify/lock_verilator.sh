#!/usr/bin/env bash
# Build the local replay model once and run each grant configuration from reset.
set -euo pipefail
cd "$(dirname "$0")/../.."
root=$PWD
model_source=
if [[ ${1:-} == --model ]]; then
    model_source=$(realpath "${2:?Specify the result directory of a completed build}")
    shift 2
    sites="$model_source/switch-sites.csv"
    model="$model_source/model/Vcycle_tb"
    if [[ ! -x $model ]]; then
        echo "No Verilator model executable at $model" >&2
        exit 2
    fi
else
    switched=$(realpath "${1:?Usage: scripts/verify/lock_verilator.sh SWITCHED_RTL_DIR RESULT_DIR [WORKLOAD LOCK_BIT ...]}")
    shift
    original=$(realpath "${RTL_DIR:-build/elab/gemmini}")
    sites="$switched/switch-sites.csv"
fi
out=$(realpath -m "${1:?Specify a new result directory}")
shift
cycles=${CYCLES:-1767}
jobs=${JOBS:-1}
if [[ ! $cycles =~ ^[1-9][0-9]{0,8}$ ]] || (( cycles < 1767 )); then
    echo "Set CYCLES to at least 1767." >&2
    exit 2
fi
if [[ ! $jobs =~ ^[1-9][0-9]?$ ]]; then
    echo "Set JOBS to a positive number up to 99." >&2
    exit 2
fi
switch_count=$(python3 - "$sites" <<'PY'
import csv
import sys

with open(sys.argv[1], newline="") as manifest:
    bits = sorted(int(row["grant_bit"]) for row in csv.DictReader(manifest))
if not bits or bits != list(range(len(bits))):
    raise SystemExit("switch-sites.csv must contain one row per contiguous grant bit starting at 0")
print(len(bits))
PY
)
if (( $# % 2 != 0 )); then
    echo "Specify pairs of WORKLOAD and LOCK_BIT." >&2
    exit 2
fi
cases=("$@")
if (( $# == 0 )); then
    for ((workload = 0; workload < 4; workload++)); do
        for ((bit = -1; bit < switch_count; bit++)); do
            cases+=("$workload" "$bit")
        done
    done
fi
declare -A seen
for ((i = 0; i < ${#cases[@]}; i += 2)); do
    workload=${cases[i]}
    bit=${cases[i+1]}
    if [[ ! $workload =~ ^[0-3]$ || ! $bit =~ ^(-1|0|[1-9][0-9]{0,8})$ ]] ||
       (( bit >= switch_count )) || [[ ${seen[$workload,$bit]+present} ]]; then
        echo "Invalid or repeated workload and lock bit: $workload $bit" >&2
        exit 2
    fi
    seen[$workload,$bit]=1
done

mkdir -p "$(dirname "$out")"
mkdir "$out"
cp "$sites" "$out/"
rc=0
TIMEFORMAT='%R'
if [[ $model_source ]]; then
    cp "$model_source/version.txt" "$out/"
    printf '%s\n' "$model" > "$out/model.txt"
else
    verilator --version > "$out/version.txt"
    python3 scripts/verify/prepare_regression.py "$original" "$switched" "$out"
    { time python3 scripts/hardware_guard.py verilator --cc --exe --build -j 4 -O3 \
        -MAKEFLAGS "OPT_FAST=-O3 OPT_SLOW=-O0" --timing --assert \
        --timescale-override 1ns/1ps -Wno-fatal --top-module cycle_tb --Mdir "$out/model" \
        -I"$out" -Itests/rtl +define+MESH_PROFILE +define+OFF_SWITCH_REGRESSION \
        +define+OFF_SWITCH_DIRECT +define+OFF_SWITCH_LOCK +define+SYNTHESIS \
        -y "$switched" +libext+.sv+.v tests/rtl/cycle_tb.sv "$out/GemminiReference.sv" \
        "$switched/OffSwitchVariants.sv" "$root/tests/rtl/off_switch_lock_verilator.cpp" \
        >"$out/compile.log" 2>&1; } 2>"$out/compile.seconds" || rc=$?
    printf '%s\n' "$rc" > "$out/compile.exitcode"
    if (( rc != 0 )); then
        tail -20 "$out/compile.log" >&2
        exit "$rc"
    fi
    model="$out/model/Vcycle_tb"
fi
run_cases() (
    cd "$out" || exit 1
    if (( jobs == 1 )); then
        exec "$model" "$cycles" "${cases[@]}"
    fi
    case_count=$((${#cases[@]} / 2))
    if (( jobs > case_count )); then jobs=$case_count; fi
    pids=()
    for ((group = 0; group < jobs; group++)); do
        worker_cases=()
        for ((i = 2 * group; i < ${#cases[@]}; i += 2 * jobs)); do
            worker_cases+=("${cases[i]}" "${cases[i+1]}")
        done
        "$model" "$cycles" "${worker_cases[@]}" >"group-$group.log" 2>&1 &
        pids+=("$!")
    done
    status=0
    for group in "${!pids[@]}"; do
        group_rc=0
        wait "${pids[group]}" || group_rc=$?
        printf '%s\n' "$group_rc" > "group-$group.exitcode"
        if (( status == 0 )); then status=$group_rc; fi
    done
    exit "$status"
)
{ time run_cases >"$out/run.log" 2>&1; } \
    2>"$out/run.seconds" || rc=$?
printf '%s\n' "$rc" > "$out/run.exitcode"
exit "$rc"
