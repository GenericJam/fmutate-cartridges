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
  URL, SHA-256, size, signature and optional expressive `caps`. Capabilities belong to that exact signed asset, not
  the firmware id: an older retained iOS asset does not inherit a new Android build's features. Firmwares that were
  tried and not published are listed under `tried` with the reason.
- **Assets**: GitHub Releases, one per run (`build-YYYYMMDD-<run>`, its re-runs adding to it; a run with more than 900
  cartridges continues in `...-2`, `...-3`: GitHub keeps at most 1000 assets in a release), each with that run's
  `pins.json`; a cartridge's file name ends in the start of its SHA-256, so no file an index names is ever replaced.
  The index goes out only once every cartridge it names is in its release with its size and SHA-256
  (`pipeline/release.py`).
- **Signatures**: Ed25519 over the exact bytes of `index.json` and of each asset, base64 (an asset's in its index
  entry). The public key
  (`pipeline/signing.pub.pem`; raw, base64: `PlTQbGPmAqukMY78jvsMLR6BBxPQAa2E3T77ofuv8h8=`) is built into FMutate,
  which refuses an index or a cartridge that doesn't verify. The private key exists only as the Actions secret
  `CARTRIDGE_SIGNING_KEY`, read by the publish job alone.

## How a run works

`.github/workflows/cartridges.yml` (daily at 05:23 UTC, or `workflow_dispatch` with `ids` / `all`, or `reindex`: build
nothing, re-sign and republish the published index as `publish.py` writes it now). The index is strict JSON: a
metric that isn't a finite number is `null`, and a check that has no number doesn't pass the gate.

1. **plan**: reads the Hub's `catalog.json`, pins every firmware to a commit (`pipeline/pins.py`: a person's
   `overrides.json` pin for that version, else the Hub's build provenance, else its source ref, else the release tag,
   else the default branch's HEAD on the day, flagged `head`; a version already pinned, in `pins.json` or in the
   published index, keeps its pin), and picks what is new or changed since the published index (`pipeline/plan.py`):
   a pin already published or already tried is not built again, unless its configured expression revision changed.
   That revision hashes the firmware's manifest entry and patch bytes, so a same-version expression rebuild is not
   lost. When nothing is new the run stops here, green, and the published index stays as it is.
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

## Native expression

`pipeline/expression.json` pins the source commits for firmwares with FMutate native expression (**Dinghy**,
**Skiff**, **Caravel**, **FM-106**, **GR!MM**, **FRASIL**). Each native-only patch adds OberVave-style MIDI poly pressure
(`0xAn`, key, level): 127 is unchanged unity (gain skipped, bit-identical), 64 is about one-quarter amplitude, and 0
silences that key. Pressure is a separate square-law gain; original note velocity, envelopes and presets are preserved,
and ordinary notes without pressure keep their original level. Every entry also provides ±2-semitone MIDI pitch bend
(`0xEn`), including on the firmware's own keys; CC120/123 release notes while keeping a held bend, and CC121 resets bend
and pressure.

In Felucca-family firmwares (`FELUCCA_FAMILY` in `pipeline/expression_config.py`), expression ownership comes strictly
from MIDI input paths (including arp/strum/chord expansion and Caravel's Harmony CHRD BUS retrigger of a held MIDI key),
never from sequencer or panel notes that happen to share a pitch. Caravel's **DRONE cells** deliberately ignore per-note
gain because several keys excite one shared network/host voice.

`verify.py` runs the neutral emulator comparison **and** `expression_verify.py`'s offline audio scenarios:
real two-note isolation, square-law gain, silence/restoration, retrigger, channel ownership, measured bend (MIDI
notes and the firmware's own keys), panic recovery, and All Notes Off keeping a held bend (only CC121 resets it). For
every `FELUCCA_FAMILY` id it also compiles `expression_felucca.c` against the pinned source's real host harness
(MONO/LEGATO/UNISON fallback, pedal/reset, chord tones, ARP octaves, deferred engine switch, stolen slots, sequencer
isolation, and Caravel Harmony CHRD BUS retrigger); the gate requires that check for every Felucca-family firmware. No
audio device is opened.

**Nothing else may change.** Every manifest entry must list `neutral` tests: commands from the firmware's own test
suite (`run`, a shell command; `{shim}` becomes the native shims' compiler flags) and the files they write
(`compare`). Each runs on a copy of the patched source and on a copy with `patches/<id>/expression.patch` reversed;
both must exit 0 and every compared file must be byte-identical. Dinghy's are its four host scripts (default, MIDI,
SMK-37, SMK-37 wheel and pads); they caught a patch that had quietly changed the stock wheel curve. Prefer tests
that render audio over every preset (Felucca's golden renders). A manifest without them is refused.

Publication requires all expression checks and matching host/platform build identities. A parity override
cannot bypass missing, failed, incomplete or stale expression proof. An incompatible source pin fails closed
until its patch is ported and verified. The signed asset receives `pitch_bend` and `note_pressure` only after
that proof; stock Hub packages and emulator entries receive neither from this manifest.

```sh
python3 pipeline/expression_verify.py dinghy  # expression only; after its host build
```


## Tests

`tests/` checks the pipeline's own logic with pytest: the index is strict JSON, a run's assets split over releases
and all checked present (GitHub faked), the plan and pin rules, signatures (with a throwaway key) and the parity gate.
No builds, network or secrets; `.github/workflows/tests.yml` runs it on pushes to `main` and on pull requests.

```sh
pip install --require-hashes -r pipeline/requirements.txt -r pipeline/requirements-dev.txt
python3 -m pytest tests
```

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
   why. Build-compatibility patches preserve stock behavior; intentional native expression extensions additionally
   require a pinned manifest entry and the non-overrideable expression gate described above.
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

The pipeline itself (everything in this repository) is GPL-3.0 (`LICENSE`); patches to firmwares under compatible
copyleft licences (such as MPL-2.0) are licensed under the patched firmware's licence.
FMutate's original application code is MIT; the firmware code keeps its authors' licences.

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
