#!/usr/bin/env bash
# Build the universal single-site Mesh screening model once and evaluate any candidate set.
set -euo pipefail
cd "$(dirname "$0")/../.."
root=$PWD
model_source=
if [[ ${1:-} == --model ]]; then
    model_source=$(realpath "${2:?Specify the result directory of a completed mesh screen build}")
    shift 2
    model="$model_source/model/mesh_screen"
    signal_map="$model_source/universal-rtl/signal_map.csv"
    if [[ ! -x $model || ! -f $signal_map ]]; then
        echo "No universal screen model at $model_source" >&2
        exit 2
    fi
fi
out=$(realpath -m "${1:?Usage: scripts/screen/run.sh [--model MODEL_DIR] RESULT_DIR (--sites PATH.csv | --count N [--seed S] | --all)}")
shift
if (( $# == 0 )); then
    echo "Specify one of --sites PATH.csv, --count N [--seed S], or --all." >&2
    exit 2
fi
original=$(realpath "${RTL_DIR:-build/elab/gemmini}")
words=$(realpath "${WORDS_CATALOG:-results/scaled/catalog/words.csv.gz}")
cycles=${CYCLES:-1767}
build_jobs=${BUILD_JOBS:-6}
default_jobs=$(lscpu -p=CORE,SOCKET 2>/dev/null | grep -v '^#' | sort -u | wc -l || nproc)
jobs=${JOBS:-$default_jobs}
if [[ ! $cycles =~ ^[1-9][0-9]{0,8}$ ]] || (( cycles < 1767 )); then
    echo "Set CYCLES to at least 1767." >&2
    exit 2
fi
if [[ ! $jobs =~ ^[1-9][0-9]?$ || ! $build_jobs =~ ^[1-9][0-9]?$ ]]; then
    echo "Set JOBS and BUILD_JOBS to positive numbers up to 99." >&2
    exit 2
fi

mkdir -p "$(dirname "$out")"
mkdir "$out"
rc=0
TIMEFORMAT='%R'
if [[ $model_source ]]; then
    cp "$model_source/version.txt" "$out/"
    printf '%s\n' "$model" > "$out/model.txt"
else
    verilator --version > "$out/version.txt"
    python3 scripts/screen/prepare.py rtl "$out/universal-rtl" --rtl "$original" --words "$words"
    signal_map="$out/universal-rtl/signal_map.csv"
    verilator_inc=$(verilator -getenv VERILATOR_ROOT)/include
    mkdir -p "$out/model"
    build_all() {
        python3 scripts/hardware_guard.py verilator --cc --build -j "$build_jobs" -O3 \
            --x-assign 0 --x-initial 0 --assert -MAKEFLAGS "OPT_FAST=-O3 OPT_SLOW=-O0" \
            -Wno-fatal --top-module Gemmini --Mdir "$out/model/gemmini" \
            +define+SYNTHESIS -y "$out/universal-rtl" +libext+.sv+.v \
            "$out/universal-rtl/mesh_io_tap.vlt" "$out/universal-rtl/Gemmini.sv" &&
        python3 scripts/hardware_guard.py verilator --cc --build -j "$build_jobs" -O3 \
            --x-assign 0 --x-initial 0 --assert -MAKEFLAGS "OPT_FAST=-O3 OPT_SLOW=-O0" \
            -Wno-fatal --top-module Mesh --Mdir "$out/model/mesh" \
            +define+SYNTHESIS -y "$out/universal-rtl" +libext+.sv+.v "$out/universal-rtl/Mesh.sv" &&
        g++ -O3 -std=c++17 -Wall -Wextra -Werror \
            -I"$out/model/gemmini" -I"$out/model/mesh" -isystem "$verilator_inc" -isystem "$verilator_inc/vltstd" \
            -DVM_COVERAGE=0 -DVM_SC=0 -DVM_TIMING=0 -DVM_TRACE=0 \
            "$root/tests/rtl/mesh_screen_verilator.cpp" \
            "$out/model/gemmini/VGemmini__ALL.a" "$out/model/mesh/VMesh__ALL.a" \
            "$out/model/gemmini/verilated.o" "$out/model/gemmini/verilated_threads.o" \
            -pthread -o "$out/model/mesh_screen"
    }
    { time build_all >"$out/compile.log" 2>&1; } 2>"$out/compile.seconds" || rc=$?
    printf '%s\n' "$rc" > "$out/compile.exitcode"
    if (( rc != 0 )); then
        tail -20 "$out/compile.log" >&2
        exit "$rc"
    fi
    model="$out/model/mesh_screen"
fi

python3 scripts/screen/prepare.py sites "$signal_map" "$out" --words "$words" "$@"
{ time "$model" "$out/candidates_compiled.csv" "$out/results.csv" "$cycles" "$jobs" \
    >"$out/run.log" 2>&1; } 2>"$out/run.seconds" || rc=$?
printf '%s\n' "$rc" > "$out/run.exitcode"
if (( rc != 0 )); then
    tail -20 "$out/run.log" >&2
fi
exit "$rc"
