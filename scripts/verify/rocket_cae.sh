#!/usr/bin/env bash
# Boot the instrumented Rocket system and run the signed-license firmware test.
set -euo pipefail
cd "$(dirname "$0")/../.."
rtl="${RTL_DIR:-build/elab/gemmini-off-switch}"
out="${RESULT_DIR:-build/off-switch-rocket-smoke}"
check="${ROCKET_CHECK:-full}"
case "$check" in
    full) firmware_args=() ;;
    boot) firmware_args=(-DROCKET_BOOT_CHECK) ;;
    *) echo "ROCKET_CHECK must be full or boot" >&2; exit 2 ;;
esac
host="${CAE_HOST:?Set CAE_HOST to your SSH user@host}"
test -s "$rtl/ExampleRocketSystem.sv"
test -s "$rtl/license/license_mailbox.vc"
site_count=$(python3 -c 'import csv, sys; print(sum(1 for _ in csv.DictReader(open(sys.argv[1]))))' \
    "$rtl/switch-sites.csv")
if (( site_count < 2 || site_count > 32 )); then
    echo "The Rocket test requires an elaboration with 2 to 32 sites" >&2
    exit 2
fi
mkdir -p "$out"

connection=$(mktemp -d)
socket="$connection/control"
trap 'ssh -S "$socket" -O exit "$host" >/dev/null 2>&1 || :; rm -rf "$connection"' EXIT
python3 scripts/rtl/license_mailbox.py vectors 0 > "$connection/vectors.json"
python3 - "$connection/vectors.json" "$connection/off_switch_rocket_vectors.inc" <<'PYTHON'
import json
from pathlib import Path
import sys

vectors = json.loads(Path(sys.argv[1]).read_text())
labels = {
    "nonce": "initial_nonce",
    "signer0": "signature_zero",
    "signer1": "signature_one",
}
with Path(sys.argv[2]).open("w") as output:
    for name, label in labels.items():
        value = bytes.fromhex(vectors[name])[::-1]
        print(f"{label}:", file=output)
        for offset in range(0, len(value), 8):
            word = int.from_bytes(value[offset:offset + 8], "little")
            print(f"    .quad 0x{word:016x}", file=output)
PYTHON
"${CLANG:-clang}" --target=riscv64-unknown-elf -march=rv64i_zicsr -mabi=lp64 \
    -I "$connection" "-DROCKET_SITE_COUNT=$site_count" "${firmware_args[@]}" \
    -nostdlib -fuse-ld=lld -Wl,-Ttext,0x80000000 -Wl,--no-relax \
    tests/rtl/off_switch_rocket.S -o "$connection/firmware.elf"
"${LLVM_OBJCOPY:-llvm-objcopy}" -O binary --only-section=.text \
    "$connection/firmware.elf" "$connection/firmware.bin"
python3 - "$connection/firmware.bin" "$connection/firmware.hex" <<'PY'
from pathlib import Path
import sys

firmware = Path(sys.argv[1]).read_bytes()
if len(firmware) > 4096:
    raise SystemExit("Rocket test firmware exceeds the 4 KiB ROM")
Path(sys.argv[2]).write_text("".join(f"{byte:02x}\n" for byte in firmware.ljust(4096, b"\x00")))
PY

ssh -o BatchMode=yes -o ConnectTimeout=25 -M -S "$socket" -fN "$host"
ssh_mux=(ssh -S "$socket" -o ProxyCommand=false)
rsync_shell="ssh -S $socket -o ProxyCommand=false"
remote=$("${ssh_mux[@]}" "$host" 'mktemp -d /tmp/gemmini-rocket.XXXXXXXX')
echo "$remote" > "$out/remote.txt"
rsync -az -e "$rsync_shell" --include='/license/***' --include='/*.sv' --include='/*.v' --exclude='*' \
    "$rtl/" "$host:$remote/rtl/"
rsync -az -e "$rsync_shell" tests/rtl/off_switch_rocket_tb.sv \
    scripts/verify/vcs.py "$connection/firmware.hex" "$host:$remote/"

compile_rc=0
"${ssh_mux[@]}" "$host" "cd $remote || exit
hostname >hostname.txt
{
    module load synopsys/vcs-mx/2026.03 &&
    python3 vcs.py compile +define+SYNTHESIS +vcs+initreg+random -j4 -y rtl \
        +libext+.sv+.v -F rtl/license/license_mailbox.vc -top off_switch_rocket_tb off_switch_rocket_tb.sv \
        rtl/OffSwitchVariants.sv -o simv
} >compile.log 2>&1
stage_rc=\$?
printf '%s\n' \"\$stage_rc\" >compile.exitcode
exit \"\$stage_rc\"" || compile_rc=$?
printf '%s\n' "$compile_rc" > "$out/compile.exitcode"
copy_rc=0
rsync -az -e "$rsync_shell" "$host:$remote/compile.log" \
    "$host:$remote/hostname.txt" "$out/" || copy_rc=$?
if (( compile_rc != 0 )); then exit "$compile_rc"; fi
if (( copy_rc != 0 )); then exit "$copy_rc"; fi

run_rc=0
"${ssh_mux[@]}" "$host" "cd $remote || exit
{
    module load synopsys/vcs-mx/2026.03 && python3 vcs.py run ./simv +vcs+initreg+0 +ROCKET_CHECK=$check
} >run.log 2>&1
stage_rc=\$?
printf '%s\n' \"\$stage_rc\" >run.exitcode
exit \"\$stage_rc\"" || run_rc=$?
printf '%s\n' "$run_rc" > "$out/run.exitcode"
rsync -az -e "$rsync_shell" "$host:$remote/run.log" "$out/" || copy_rc=$?
if [[ -f "$out/run.log" ]]; then tail -12 "$out/run.log"; fi
if (( run_rc != 0 )); then exit "$run_rc"; fi
if (( copy_rc != 0 )); then exit "$copy_rc"; fi
