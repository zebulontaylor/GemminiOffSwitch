#!/usr/bin/env bash
# Compile the stock and switched lanes once and test each grant independently.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
original=$(realpath "${RTL_DIR:-build/elab/gemmini}")
switched=$(realpath "${1:?Usage: scripts/verify/lock_cae.sh SWITCHED_RTL_DIR [RESULT_DIR]}")
out=$(realpath -m "${2:-build/off-switch-lock/results}")
cycles=${CYCLES:-14001}
jobs=${JOBS:-4}
if [[ ! $cycles =~ ^[1-9][0-9]{0,8}$ ]] || (( cycles < 1767 )) ||
   [[ ! $jobs =~ ^[1-9][0-9]?$ ]]; then
    echo "Set CYCLES to at least 1767 and JOBS to a positive number up to 99." >&2
    exit 2
fi
switch_count=$(python3 - "$switched/switch-sites.csv" <<'PY'
import csv
import sys

with open(sys.argv[1], newline="") as manifest:
    bits = sorted(int(row["grant_bit"]) for row in csv.DictReader(manifest))
if not bits or bits != list(range(len(bits))):
    raise SystemExit("switch-sites.csv must contain one row per contiguous grant bit starting at 0")
print(len(bits))
PY
)
mkdir -p "$out"
if [[ -e "$out/remote.txt" ]]; then
    echo "Choose a new result directory to preserve the existing run in $out." >&2
    exit 2
fi

connection=$(mktemp -d)
socket="$connection/master"
host=${CAE_HOST:?Set CAE_HOST to your SSH user@host}
cleanup() {
    ssh -S "$socket" -O exit "$host" >/dev/null 2>&1 || :
    rm -rf "$connection"
}
trap cleanup EXIT
python3 scripts/verify/prepare_regression.py "$original" "$switched" "$connection/source"
cp tests/rtl/{cycle_tb.sv,rocc_workload.svh,off_switch_regression.svh,off_switch_lock.svh} \
    scripts/verify/vcs.py "$connection/source/"
cat <<'BATCH' >"$connection/source/off_switch_lock_batch.sh"
#!/usr/bin/env bash
# Run each grant configuration in a fresh process inside one CAE container.
set -uo pipefail
simv=$1
cycles=$2
shift 2
out=$(dirname "$simv")
status=0
max_attempts=3
while (( $# )); do
    workload=$1
    lock_bit=$2
    shift 2
    name="workload-$workload-lock-$lock_bit"
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        attempt_name="$out/$name.attempt-$attempt"
        rc=0
        (
            scratch=$(mktemp -d "$out/simulation.XXXXXXXX") || exit 1
            trap 'rmdir "$scratch" 2>/dev/null || :' EXIT
            cd "$scratch" || exit 1
            timeout --kill-after=5s 180s "$simv" -no_save -exitstatus +vcs+initreg+0 \
                "+WORKLOAD=$workload" "+LOCK_BIT=$lock_bit" "+CYCLES=$cycles"
        ) >"$attempt_name.log" 2>&1 || rc=$?
        echo "$rc" >"$attempt_name.exitcode"
        if (( rc != 124 && rc != 137 )) ||
           grep -Eiq 'OFF_SWITCH_LOCK (START|PASS)|LOCK_RESULT|fatal|assertion' "$attempt_name.log"; then
            break
        fi
    done
    cp "$attempt_name.log" "$out/$name.log" || exit 1
    cp "$attempt_name.exitcode" "$out/$name.exitcode" || exit 1
    if (( status == 0 )); then status=$rc; fi
done
exit "$status"
BATCH
chmod +x "$connection/source/off_switch_lock_batch.sh"
cp "$switched/switch-sites.csv" "$out/"

ssh -o BatchMode=yes -o ConnectTimeout=25 -M -S "$socket" -fN "$host"
remote_ssh=(ssh -S "$socket" -o ProxyCommand=false)
remote=$("${remote_ssh[@]}" "$host" 'mktemp -d /tmp/gemmini-switch-lock.XXXXXXXX')
echo "$remote" >"$out/remote.txt"
rsync -az -e "${remote_ssh[*]}" "$switched/" "$host:$remote/rtl/"
rsync -az -e "${remote_ssh[*]}" "$connection/source/" "$host:$remote/"

rc=0
"${remote_ssh[@]}" "$host" "cd $remote && bash -s -- $cycles $jobs $switch_count" <<'REMOTE' || rc=$?
set -eo pipefail
hostname >hostname.txt
cycles=$1
jobs=$2
switch_count=$3
rc=0
(
    module load synopsys/vcs-mx/2026.03 &&
    python3 vcs.py compile +define+MESH_PROFILE +define+OFF_SWITCH_REGRESSION \
        +define+OFF_SWITCH_DIRECT +define+OFF_SWITCH_LOCK +define+SYNTHESIS +vcs+initreg+random \
        -j4 -y rtl +libext+.sv+.v -top cycle_tb cycle_tb.sv GemminiReference.sv \
        rtl/OffSwitchVariants.sv -o simv
) >compile.log 2>&1 || rc=$?
echo "$rc" >compile.exitcode
(( rc == 0 )) || exit "$rc"
module load synopsys/vcs-mx/2026.03

# Round-robin cases across licensed containers and preserve each simulation's status.
pids=()
case_count=$((4 * (switch_count + 1)))
if (( jobs > case_count )); then jobs=$case_count; fi
for ((group = 0; group < jobs; group++)); do
    cases=()
    for ((index = group; index < case_count; index += jobs)); do
        cases+=("$((index / (switch_count + 1)))" "$((index % (switch_count + 1) - 1))")
    done
    python3 vcs.py batch "$PWD/off_switch_lock_batch.sh" "$PWD/simv" "$cycles" "${cases[@]}" \
        >"group-$group.log" 2>&1 &
    pids+=("$!")
done
for group in "${!pids[@]}"; do
    group_rc=0
    wait "${pids[$group]}" || group_rc=$?
    echo "$group_rc" >"group-$group.exitcode"
    if (( rc == 0 )); then rc=$group_rc; fi
done
echo "$rc" >run.exitcode
exit "$rc"
REMOTE
copy_rc=0
# Fetch the final results and the individual .attempt-N logs and exit codes.
rsync -az -e "${remote_ssh[*]}" --include=hostname.txt --include='*.log' --include='*.exitcode' \
    --exclude='*' "$host:$remote/" "$out/" || copy_rc=$?
if [[ -f "$out/compile.log" ]]; then tail -10 "$out/compile.log"; fi
(( rc == 0 )) || exit "$rc"
exit "$copy_rc"
