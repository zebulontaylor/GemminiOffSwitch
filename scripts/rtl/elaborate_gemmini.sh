#!/usr/bin/env bash
# Elaborate the default Gemmini with Rocket's TileLink and RoCC parameters.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
guard="$root/scripts/hardware_guard.py"
if ! "$guard" --active; then
    exec "$guard" "$(realpath "$0")" "$@"
fi
export GEMMINI_CHIPYARD="$root/build/chipyard"
export GEMMINI_CHECKOUT="$root/gemmini"
export GEMMINI_ELAB_OUT
GEMMINI_ELAB_OUT=$(realpath -m "${GEMMINI_ELAB_OUT:-$root/build/elab/gemmini}")
mkdir -p "$root/build"
pin=$(cat "$GEMMINI_CHECKOUT/CHIPYARD.hash")
if [[ ! -d "$GEMMINI_CHIPYARD/.git" ]]; then
    git clone --filter=blob:none --no-checkout https://github.com/ucb-bar/chipyard.git "$GEMMINI_CHIPYARD"
fi
git -C "$GEMMINI_CHIPYARD" checkout --quiet --detach "$pin"
git -C "$GEMMINI_CHIPYARD" submodule update --init --depth 1 --jobs 5 \
    generators/rocket-chip generators/diplomacy generators/hardfloat tools/cde sims/firesim

# Download Chipyard's pinned Linux x86-64 FIRRTL compiler on first run.
firtool_version=1.75.0
firtool_cache="$root/build/tools/firtool-$firtool_version"
mkdir -p "$firtool_cache"
if [[ ! -x "$firtool_cache/firtool" ]]; then
    tmpdir=$(mktemp -d "$firtool_cache/download.XXXXXX")
    trap 'rm -rf "$tmpdir"' EXIT
    archive="$tmpdir/firtool.jar"
    curl --silent --show-error --fail --location --retry 3 --output "$archive" \
        "https://repo.maven.apache.org/maven2/org/chipsalliance/llvm-firtool/$firtool_version/llvm-firtool-$firtool_version-linux-x64.jar"
    hash=049a02bc0138d8600cc29444a567dc1354f32a9c1c5ac2957011f51909e95088
    echo "$hash  $archive" | sha256sum --check --status
    unzip -p "$archive" '*/bin/firtool' > "$tmpdir/firtool"
    chmod +x "$tmpdir/firtool"
    mv "$tmpdir/firtool" "$firtool_cache/firtool"
fi
export GEMMINI_FIRTOOL="$firtool_cache/firtool"

work="$root/build/elaboration-build"
mkdir -p "$work" "$GEMMINI_ELAB_OUT"
cp -R "$root/infra/gemmini/." "$work"
cd "$work"
export COURSIER_REPOSITORIES=https://repo.maven.apache.org/maven2
java -Xmx8G -XX:ActiveProcessorCount=4 -Dsbt.override.build.repos=true \
    -Dsbt.repository.config="$work/repositories" -jar "$GEMMINI_CHIPYARD/scripts/sbt-launch.jar" \
    'runner/runMain offswitch.Elaborate'
test -s "$GEMMINI_ELAB_OUT/Gemmini.sv"
echo "Wrote $GEMMINI_ELAB_OUT/Gemmini.sv and its submodules"
