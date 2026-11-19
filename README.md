# fmutate-cartridges

FM-1 Firmware Hub firmwares, built from their own published source as **native cartridges** for
[FMutate](https://github.com/GenericJam/fmutate), so they run at full speed instead of in FMutate's FM-1 emulator.

Each cartridge is the firmware's own code compiled for the phone (`libcart_<id>.so` for Android, `libcart_<id>.a`
linked into the iOS app), behind the `fm1c_*` calls of FMutate's engine (`native/engine/live.c`). Before anything is
published, the same firmware runs twice on a Linux runner, once as the native cartridge and once as the Hub's package
in FMutate's emulator, and the two must sound and look the same (the **parity gate**). The decision record is
FMutate's [docs/decisions/2026-11-14-native-catalog.md](https://github.com/GenericJam/fmutate/blob/native-catalog/docs/decisions/2026-11-14-native-catalog.md).

## What gets published

- **Index**: <https://genericjam.github.io/fmutate-cartridges/index.json> and `index.json.sig` (GitHub Pages, the
  `gh-pages` branch; a stable URL with ETags, which FMutate's loader polls). Schema 1: per firmware its id, Hub
  version, source repository and pinned commit, licence, how it built (`clean`, `shims`, `patch`), the parity numbers
  and, per platform (`android-arm64-v8a`, `android-x86_64`, `android-armeabi-v7a`, `ios-device`, `ios-sim`), the asset
  URL, SHA-256, size and signature. Firmwares that were tried and not published are listed under `tried` with the reason.
- **Assets**: GitHub Releases, one release per run (`build-YYYYMMDD-<run>.<attempt>`), each cartridge with a `.sig`,
  plus that run's `pins.json`.
- **Signatures**: Ed25519 over the exact bytes of `index.json` and of each asset, base64. The public key
  (`pipeline/signing.pub.pem`; raw, base64: `PlTQbGPmAqukMY78jvsMLR6BBxPQAa2E3T77ofuv8h8=`) is built into FMutate,
  which refuses an index or a cartridge that doesn't verify. The private key exists only as the Actions secret
  `CARTRIDGE_SIGNING_KEY`, read by the publish job alone.

## How a run works

`.github/workflows/cartridges.yml` (daily at 05:23 UTC, or `workflow_dispatch` with `ids` / `all`):

1. **plan**: reads the Hub's `catalog.json`, pins every firmware to a commit (`pipeline/pins.py`: a person's
   `overrides.json` pin for that version, else the Hub's build provenance, else its source ref, else the release tag,
   else the default branch's HEAD on the day, flagged `head`; a version already pinned, in `pins.json` or in the
   published index, keeps its pin), and picks what is new or changed since the published index (`pipeline/plan.py`):
   a pin already published or already tried is not built again. When nothing is new the run stops here, green, and
   the published index stays as it is.
2. **engine**: builds FMutate's native engine and emulator engine at a pinned FMutate commit (`pipeline/engine.sh`).
3. **android** (one job per firmware, Linux; in batches of at most 150, `.github/workflows/_build-android.yml`, under
   GitHub's 256-job matrix limit): fetches the source at its pin (`pipeline/fetch.sh`, plus pinned
   dependencies from `pipeline/deps/<id>.json`), builds it for the runner (`pipeline/build.py --target host`), checks it
   against the emulator (`pipeline/verify.py`), then builds the three Android ABIs.
4. **gate**: the firmwares whose check is `match` or `silent` (or accepted by a person in `pipeline/overrides.json`).
5. **ios** (one job per firmware that passed, macOS, batched the same way: `.github/workflows/_build-ios.yml`):
   `ios-device` and `ios-sim` static libraries. macOS runners are few (5 at a time); jobs queue rather than fail. A job
   that fails anyway (a lost runner) is re-run, up to twice, by `.github/workflows/retry.yml`.
6. **publish**: signs the assets and the index (`pipeline/publish.py`), uploads the release, pushes the index to
   `gh-pages`. A firmware that doesn't build doesn't fail its job: it lands in the index's `tried` list, so a green run
   means the pipeline worked, and the step summaries list each firmware's outcome. The new index starts from the
   published one (its signature checked) and only adds or updates this run's firmwares; a rebuild of a published pin
   that fails keeps the published entry.

## Run it locally

```sh
sh pipeline/engine.sh                      # engines into engine/ (needs Rust 1.99.1, a C compiler)
sh pipeline/fetch.sh skiff                 # source into work/src/skiff
python3 pipeline/build.py skiff --target host
python3 pipeline/verify.py skiff           # native vs emulator: level, spectrum, screens
python3 pipeline/build.py skiff --target android-arm64-v8a   # ANDROID_NDK_HOME = NDK r27c
python3 pipeline/report.py                 # everything built so far: pipeline/results.json + a table
```

`FM1_CATALOG_DIR` (default `work/`) and `FM1_ENGINE_DIR` (default `engine/`) move the work and engine directories.

## Adding a firmware

A firmware on the Hub is picked up by the next daily run with no change here. When it doesn't build or doesn't match,
in this order of preference:

1. **A recipe** (`pipeline/recipes/<name>.py`: `ORDER`, `detect(tree, id)`, `build(...)`) for a family of firmwares
   whose own host or browser build can be replayed natively, with its adapter in `pipeline/adapters/` mapping the
   family's API to the `fm1c_*` calls. Nothing of the firmware is changed.
2. **Generic shims** (`pipeline/shims/include`), tried automatically when a plain build fails.
3. **Pinned dependencies** (`pipeline/deps/<id>.json`, see `pipeline/deps.py`): what the firmware's build would fetch
   unpinned (a `git clone` of a branch, a download) or what its repository lacks, as a GitHub repository at a full
   commit SHA placed at a path in the tree, or another Hub firmware. Builds never fetch anything unpinned.
4. **A per-firmware patch** (`pipeline/patches/<id>/*.patch`, applied with `patch -p1`): small, with a header saying
   why, only when the firmware's own source can't build on a computer.
5. **`pipeline/overrides.json`**: `{"skip": {id: reason}}` not to build; `{"accept": {id: reason}}` to publish a
   firmware that differs from the emulator for a reason a person checked (for example the emulator never boots it);
   `{"pin": {id: {"version", "commit", "why"}}}`: the commit a version's package was built from, found by a person
   where the catalogue names none (a HEAD pin builds whatever the default branch holds).

`pipeline/causes/<id>.txt` holds one line per firmware that still can't be built, with what it would take.

## Licences and source

The firmwares are their authors' and keep their licences (most are GPL-3.0; the index carries each one's). A cartridge
is a build of exactly one public source commit: the index names the repository and commit of every published
cartridge, the release carries the `pins.json` it was built from, and this repository holds every recipe, adapter,
shim, patch and dependency pin that went into it. That is the complete corresponding source for each binary; it stays
fetchable here and at the authors' repositories. If any of it becomes unavailable, open an issue on this repository
and we will provide it. Firmwares without a licence are not published (`overrides.json` `skip`).

The pipeline itself (everything in this repository) is GPL-3.0, as FMutate (`LICENSE`).

## Security model

- Every build job runs a firmware's own build scripts, so build jobs get a read-only token, no persisted git
  credentials, and no secrets; only the publish job (which runs no firmware code) can sign and write.
- `publish.py` takes from each job's artifact only the files of that job's firmware, so one firmware's build can't
  replace another's cartridge, and it refuses to extend a previous index whose signature doesn't verify.
- Firmware sources, dependencies (`deps/`), FMutate's engine and fm1-emu are fetched at full commit SHAs; the
  workflow's actions are pinned by SHA; Python packages by hash (`pipeline/requirements.txt`); the one native tool
  built on the macOS runners (FriBiDi, for Pillow's text layout) from its release tarball, by SHA-256.
- A cartridge is native code from the firmware's author running in FMutate's process: publishing one trusts its
  author as much as installing their firmware on an FM-1 does. The parity gate checks behaviour, not intent.

History: moved from FMutate's `tools/native_catalog` (GenericJam/fmutate 277ad0c, history kept).
