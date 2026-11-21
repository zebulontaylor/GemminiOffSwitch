#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
original=$(realpath "${RTL_DIR:-build/elab/gemmini}")
switched=$(realpath "${1:?Usage: scripts/verify/test_cae.sh SWITCHED_RTL_DIR [RESULT_DIR]}")
out=$(realpath -m "${2:-build/off-switch-regression/results}")
mkdir -p "$out"

connection=$(mktemp -d)
socket="$connection/master"
host=${CAE_HOST:?Set CAE_HOST to your SSH user@host}
direct_grants=${DIRECT_GRANTS:-0}
if [[ "$direct_grants" != 0 && "$direct_grants" != 1 ]]; then
    echo "DIRECT_GRANTS must be 0 or 1" >&2
    exit 2
fi
mesh_workloads=${MESH_WORKLOADS:-0}
if [[ "$mesh_workloads" != 0 && "$mesh_workloads" != 1 ]]; then
    echo "MESH_WORKLOADS must be 0 or 1" >&2
    exit 2
fi
cleanup() {
    ssh -S "$socket" -O exit "$host" >/dev/null 2>&1 || :
    rm -rf "$connection"
}
trap cleanup EXIT
python3 scripts/verify/prepare_regression.py "$original" "$switched" "$connection/source"
cp tests/rtl/{cycle_tb.sv,rocc_workload.svh,off_switch_regression.svh} scripts/verify/vcs.py "$connection/source/"

ssh -o BatchMode=yes -o ConnectTimeout=25 -M -S "$socket" -fN "$host"
remote_ssh=(ssh -S "$socket" -o ProxyCommand=false)
remote=$("${remote_ssh[@]}" "$host" 'mktemp -d /tmp/gemmini-switch.XXXXXXXX')
echo "$remote" >"$out/remote.txt"
rsync -az -e "${remote_ssh[*]}" "$switched/" "$host:$remote/rtl/"
rsync -az -e "${remote_ssh[*]}" "$connection/source/" "$host:$remote/"

rc=0
"${remote_ssh[@]}" "$host" "cd $remote && bash -s -- $direct_grants $mesh_workloads" <<'REMOTE' || rc=$?
set -eo pipefail
hostname >hostname.txt
module load synopsys/vcs-mx/2026.03
rc=0
if [[ $1 == 1 ]]; then
    license_args=(+define+OFF_SWITCH_DIRECT)
else
    license_args=(-F rtl/license/license_mailbox.vc)
fi
profile_args=()
if [[ $2 == 1 ]]; then profile_args=(+define+MESH_PROFILE); fi
python3 vcs.py compile "${profile_args[@]}" +define+OFF_SWITCH_REGRESSION +define+SYNTHESIS +vcs+initreg+random \
    -j4 -y rtl +libext+.sv+.v -top cycle_tb cycle_tb.sv GemminiReference.sv \
    "${license_args[@]}" rtl/OffSwitchVariants.sv -o simv >compile.log 2>&1 || rc=$?
echo "$rc" >compile.exitcode
(( rc == 0 )) || exit "$rc"
if [[ $2 == 1 ]]; then
    for workload in 0 1 2 3; do
        case_rc=0
        python3 vcs.py run ./simv +vcs+initreg+0 "+WORKLOAD=$workload" >"workload-$workload.log" 2>&1 || case_rc=$?
        echo "$case_rc" >"workload-$workload.exitcode"
        echo "WORKLOAD $workload exitcode=$case_rc" >>run.log
        cat "workload-$workload.log" >>run.log
        if (( rc == 0 )); then rc=$case_rc; fi
    done
else
    python3 vcs.py run ./simv +vcs+initreg+0 >run.log 2>&1 || rc=$?
fi
echo "$rc" >run.exitcode
exit "$rc"
REMOTE
copy_rc=0
rsync -az -e "${remote_ssh[*]}" --include=hostname.txt --include='*.log' --include='*.exitcode' \
    --exclude='*' "$host:$remote/" "$out/" || copy_rc=$?
for file in compile.log run.log; do
    if [[ -f "$out/$file" ]]; then tail -15 "$out/$file"; fi
done
(( rc == 0 )) || exit "$rc"
exit "$copy_rc"
