#!/usr/bin/env bash
# Estimate the mapped area of the array clamps and signed-license Rocket system.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
guard="$root/scripts/hardware_guard.py"
if ! "$guard" --active; then
    exec "$guard" "$(realpath "$0")" "$@"
fi
cd "$root"

if (( $# != 3 )); then
    echo "Usage: $0 LIBERTY BASELINE_RTL INSTRUMENTED_RTL" >&2
    exit 2
fi
liberty=$1
baseline=$2
instrumented=$3
sites="$instrumented/switch-sites.csv"
engine_count=$(python3 -c '
import csv
import sys
with open(sys.argv[1], newline="") as stream:
    print(sum(1 for _ in csv.DictReader(stream)))
' "$sites")
if (( engine_count < 1 || engine_count > 32 )); then
    echo "Expected 1 to 32 sites in $sites" >&2
    exit 2
fi

output_root=${AREA_OUT_ROOT:-build/campaign-area}
mkdir -p "$output_root"
out=$(mktemp -d "$output_root/run.XXXXXXXX")
echo "Area reports will be written to $out"
python3 scripts/rtl/insert_off_switches.py "$baseline" "$out/clamp-rtl" --sites "$sites"
python3 scripts/rtl/license_mailbox.py stage "$out/license-partition" --ecdsa-blackbox > "$out/license-partition-files.txt"
while IFS= read -r source; do
    if [[ $source == ecdsa.sv ]]; then
        cmp <(sed -n '1,/^);$/p' "$instrumented/license/$source") \
            <(sed -n '1,/^);$/p' "$out/license-partition/$source")
    else
        cmp "$instrumented/license/$source" "$out/license-partition/$source"
    fi
done < "$out/license-partition-files.txt"
(cd "$instrumented/license" && sha256sum ecdsa.sv) > "$out/ecdsa-source.sha256"

gemmini_sram=(
    --exclude-macro mem_4096x128=4
    --exclude-macro mem_512x512=2
)
rocket_sram=(
    --exclude-macro rockettile_dcache_data_arrays_256x512=2
    --exclude-macro rockettile_dcache_tag_array_64x176=1
    --exclude-macro rockettile_icache_data_arrays_256x256=4
    --exclude-macro rockettile_icache_tag_array_64x168=1
)
map_area=(python3 scripts/area/map.py --abc-fast --liberty "$liberty")

stage_rocket() {
    local rtl=$1
    local dest=$2
    local source
    mkdir "$dest"
    for source in "$rtl"/*.sv "$rtl"/*.v; do
        if [[ ${source##*/} == Gemmini.sv ]]; then
            sed -n '1,/^);$/p' "$source" > "$dest/Gemmini.sv"
            printf 'endmodule\n' >> "$dest/Gemmini.sv"
        else
            ln -s "$(realpath "$source")" "$dest/"
        fi
    done
}

stage_rocket "$baseline" "$out/rocket-rtl"
stage_rocket "$instrumented" "$out/rocket-switch-rtl"

"${map_area[@]}" --top Gemmini --rtl "$baseline" --out "$out/gemmini-baseline" "${gemmini_sram[@]}"
"${map_area[@]}" --top Gemmini --rtl "$out/clamp-rtl" --out "$out/gemmini-switch" "${gemmini_sram[@]}"
"${map_area[@]}" --top DTLB_2 --rtl "$baseline" --out "$out/dtlb-baseline"
"${map_area[@]}" --top DTLB_2 --rtl "$instrumented" --out "$out/dtlb-switch"
"${map_area[@]}" --top ecdsa --filelist "$instrumented/license/license_mailbox.vc" --out "$out/ecdsa"
"${map_area[@]}" --top ExampleRocketSystem --rtl "$out/rocket-rtl" --out "$out/rocket-baseline" \
    --exclude-macro Gemmini=1 "${rocket_sram[@]}"
"${map_area[@]}" --top ExampleRocketSystem --rtl "$out/rocket-switch-rtl" --out "$out/rocket-switch" \
    --extra-filelist "$out/license-partition/license_mailbox.vc" --exclude-macro "ecdsa=$engine_count" \
    --exclude-macro Gemmini=1 "${rocket_sram[@]}"

python3 - "$out" <<'PY'
from decimal import Decimal
import json
from pathlib import Path
import sys

out = Path(sys.argv[1])

def summary(name):
    return json.loads((out / name / "summary.json").read_text(), parse_float=Decimal)

baseline = summary("rocket-baseline")
instrumented = summary("rocket-switch")
ecdsa = summary("ecdsa")
copies = instrumented["excluded_macro_instances"]["ecdsa"]
gemmini_baseline = summary("gemmini-baseline")["area_um2"]
gemmini_switch = summary("gemmini-switch")["area_um2"]
dtlb_delta = summary("dtlb-switch")["area_um2"] - summary("dtlb-baseline")["area_um2"]
engine_area = copies * ecdsa["area_um2"]
rocket_baseline = baseline["area_um2"] + gemmini_baseline
rocket_switch = instrumented["area_um2"] + gemmini_switch + dtlb_delta
combined_area = rocket_switch + engine_area
delta = combined_area - rocket_baseline
lines = [
    f"Gemmini baseline:                  {gemmini_baseline:>15,.3f} um^2",
    f"Gemmini with clamps:               {gemmini_switch:>15,.3f} um^2",
    f"Gemmini clamp area delta:          {gemmini_switch - gemmini_baseline:>15,.3f} um^2",
    f"Gemmini relative area delta:       {gemmini_switch / gemmini_baseline - 1:>15.3%}",
    f"Gemmini DTLB MMIO decode delta:     {dtlb_delta:>15,.3f} um^2",
    f"Rocket outside baseline Gemmini:   {baseline['area_um2']:>15,.3f} um^2",
    f"Rocket outside Gemmini and ECDSA:  {instrumented['area_um2']:>15,.3f} um^2",
    f"Rocket baseline with Gemmini:      {rocket_baseline:>15,.3f} um^2",
    f"Rocket with clamps and mailboxes:  {rocket_switch:>15,.3f} um^2",
    f"One ECDSA engine:                  {ecdsa['area_um2']:>15,.3f} um^2",
    f"{copies} ECDSA engines:                  {engine_area:>15,.3f} um^2",
    f"Rocket with all ECDSA engines:     {combined_area:>15,.3f} um^2",
    f"Rocket area delta with engines:    {delta:>15,.3f} um^2",
    f"Rocket relative area delta:        {delta / rocket_baseline:>15.3%}",
]
report = "\n".join(lines) + "\n"
(out / "comparison.txt").write_text(report)
print(report, end="")
PY
