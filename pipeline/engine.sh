#!/bin/sh
# Builds the two engines the parity check (verify.py) runs a firmware in, from FMutate at a pinned commit, into
# $FM1_ENGINE_DIR (default engine/):
#   libfm1engine.so  FMutate's native engine (native/engine/fm1engine.c), which loads a cartridge
#   libfm1emu.so     FMutate's emulator engine (native/emu over fm1-emu, which native/build_emu.sh pins)
#   cartbench        the speed bench (pipeline/cartbench.c)
# The FMutate checkout goes to $FMUTATE_DIR (default fmutate/). Bump FMUTATE_COMMIT to check against a newer engine.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(dirname "$HERE")
FMUTATE_REPO=https://github.com/GenericJam/fmutate.git
FMUTATE_COMMIT=47000b468ad5e2368bdaefe609ccbe20ade7265f     # master, 2026-11-14 (engine as on native-catalog)
FM=${FMUTATE_DIR:-$ROOT/fmutate}
OUT=${FM1_ENGINE_DIR:-$ROOT/engine}

if [ "$(git -C "$FM" rev-parse HEAD 2>/dev/null)" != "$FMUTATE_COMMIT" ]; then
    rm -rf "$FM"
    mkdir -p "$FM"
    git -C "$FM" init -q
    git -C "$FM" remote add origin "$FMUTATE_REPO"
    git -C "$FM" fetch -q --depth 1 origin "$FMUTATE_COMMIT"
    git -C "$FM" -c advice.detachedHead=false checkout -q FETCH_HEAD
fi
mkdir -p "$OUT"
cc -O2 -o "$OUT/libfm1engine.so" "$FM/native/engine/fm1engine.c" -ldl
cc -O2 -o "$OUT/cartbench" "$HERE/cartbench.c" -ldl -lm
ABIS="" sh "$FM/native/build_emu.sh"
cp "$FM/native/build/host/libfm1emu.so" "$OUT/"
echo "engine: $(ls "$OUT" | tr '\n' ' ')(FMutate ${FMUTATE_COMMIT%"${FMUTATE_COMMIT#????????????}"})"
