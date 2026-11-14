#!/bin/sh
# Fetches firmware sources at their pins (pins.json beside this script) into $FM1_CATALOG_DIR/src/<id> (default
# work/src):
# one shallow fetch of the pinned commit, submodules shallow too (at the commits the tree's gitlinks name), then the
# firmware's pinned build dependencies (deps.py, deps/<id>.json); then, to keep 213 trees in a few GB, the git
# data goes (the commit stays in <id>/.pin) and so do media no build reads (video, audio previews, PDFs, GIFs,
# archives, prebuilt .wasm). (Builds read docs/ and JPEGs: Duell's MIDI map, Ombak's logo, Guitchordi's tuning
# presets.) Re-runnable (a tree at its pin, with the same deps file, is kept).
#
#   pipeline/fetch.sh <id>...      those
#   pipeline/fetch.sh --all        every pinned firmware (8 at a time)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PINS=$HERE/pins.json
SRC=${FM1_CATALOG_DIR:-$HERE/../work}/src
mkdir -p "$SRC"

if [ "$1" = --all ]; then
    python3 -c 'import json, sys; print("\n".join(json.load(open(sys.argv[1]))))' "$PINS" |
        xargs -P 8 -n 1 sh "$0"
    exit 0
fi

for id in "$@"; do
    case $id in *[!A-Za-z0-9_.-]*|.*|'') echo "$id: not a firmware id" >&2; exit 2 ;; esac
    set -- $(python3 -c 'import json, sys; p = json.load(open(sys.argv[1]))[sys.argv[2]]; print(p["repo"], p["commit"])' "$PINS" "$id")
    repo=$1 commit=$2
    case $commit in *[!0-9a-f]*|'') echo "$id: FETCH FAILED (no pinned commit)"; continue ;; esac
    case $repo in */*/*|*[!A-Za-z0-9_./-]*|/*|*/) echo "$id: FETCH FAILED (bad repo $repo)"; continue ;; esac
    pin=$commit
    digest=$(python3 "$HERE/deps.py" --digest "$id")
    [ -z "$digest" ] || pin="$commit deps:$digest"
    dir=$SRC/$id
    if [ "$(cat "$dir/.pin" 2>/dev/null)" = "$pin" ]; then
        continue
    fi
    rm -rf "$dir"
    mkdir -p "$dir"
    export GIT_TERMINAL_PROMPT=0
    if git -C "$dir" init -q && git -C "$dir" remote add origin "https://github.com/$repo.git" &&
        git -C "$dir" fetch -q --depth 1 origin "$commit" && git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD; then
        sub=""
        git -C "$dir" submodule -q update --init --recursive --depth 1 2>"$dir.submodule.log" || sub=" (submodules failed: $dir.submodule.log)"
        [ -s "$dir.submodule.log" ] || rm -f "$dir.submodule.log"
        python3 "$HERE/deps.py" "$id" "$dir" || { echo "$id: FETCH FAILED (deps)"; continue; }
        find "$dir" -name .git -prune -exec rm -rf {} +
        find "$dir" -type f \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.mp3' -o -iname '*.pdf' -o -iname '*.gif' \
            -o -iname '*.zip' -o -iname '*.wasm' -o -iname '*.webm' \) -delete
        echo "$pin" > "$dir/.pin"
        echo "$id: $(du -sh "$dir" | cut -f1)$sub"
    else
        echo "$id: FETCH FAILED"
    fi
done
