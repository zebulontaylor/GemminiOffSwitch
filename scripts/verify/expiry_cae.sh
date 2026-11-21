#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
out=$(realpath -m "${1:-build/off-switch-expiry}")
mkdir -p "$out"

connection=$(mktemp -d)
socket="$connection/master"
host=${CAE_HOST:?Set CAE_HOST to your SSH user@host}
cleanup() {
    ssh -S "$socket" -O exit "$host" >/dev/null 2>&1 || :
    rm -rf "$connection"
}
trap cleanup EXIT

source_dir="$connection/source"
python3 scripts/rtl/license_mailbox.py stage "$source_dir" >/dev/null
cp tests/rtl/off_switch_expiry_tb.sv scripts/verify/vcs.py "$source_dir/"
PYTHONPATH=scripts python3 - >"$source_dir/off_switch_expiry_vectors.svh" <<'PY'
from rtl.license_mailbox import reset_nonce, sign_fixture

for instance in range(2):
    nonce = reset_nonce(instance)
    print(f"localparam logic [255:0] NONCE{instance} = 256'h{nonce:064x};")
    for signer in range(2):
        signature = sign_fixture(nonce, signer + 2)
        print(f"localparam logic [511:0] SIGNER{signer}{instance} = 512'h{signature:0128x};")
PY

ssh -o BatchMode=yes -o ConnectTimeout=25 -M -S "$socket" -fN "$host"
remote=$(ssh -S "$socket" "$host" 'mktemp -d /tmp/gemmini-switch-expiry.XXXXXXXX')
rsync -az -e "ssh -S $socket" "$source_dir/" "$host:$remote/"

rc=0
ssh -S "$socket" "$host" "cd $remote && bash -s" <<'REMOTE' || rc=$?
set -eo pipefail
hostname >hostname.txt
module load synopsys/vcs-mx/2026.03
rc=0
python3 vcs.py compile -F license_mailbox.vc off_switch_expiry_tb.sv \
    -top off_switch_expiry_tb -o simv >compile.log 2>&1 || rc=$?
echo "$rc" >compile.exitcode
(( rc == 0 )) || exit "$rc"
python3 vcs.py run ./simv >run.log 2>&1 || rc=$?
echo "$rc" >run.exitcode
exit "$rc"
REMOTE

copy_rc=0
rsync -az -e "ssh -S $socket" --include='hostname.txt' --include='*.log' \
    --include='*.exitcode' --exclude='*' "$host:$remote/" "$out/" || copy_rc=$?
for file in compile.log run.log; do
    if [[ -f "$out/$file" ]]; then tail -15 "$out/$file"; fi
done
(( rc == 0 )) || exit "$rc"
exit "$copy_rc"
