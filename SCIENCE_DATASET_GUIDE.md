# SphereX science dataset: integration guide for Codex and the reviewing teammate

This file is the complete specification for attaching the local SPHEREx dataset to SPHEREx Sky Explorer. Follow it exactly. Where the guide says MUST, there is no alternative. Where it says MUST NOT, doing it is a defect.

## 0. Instructions to Codex (read first)

1. Create subagents and run them in parallel. Use exactly these five subagents, one per workstream in section 6: **A Dataset placement**, **B Index verification**, **C Five-target analyses**, **D Frontend checks**, **E Report**. A, B and C run in sequence (each needs the previous one). D starts as soon as A finishes and runs in parallel with B and C. E runs last.
2. Prioritise fast implementation. Do not refactor, rename, reformat or "improve" any file this guide does not name. Do not add dependencies.
3. The dataset is read-only. MUST NOT modify, move, recompress or rename any file inside the dataset folder after placing it.
4. MUST NOT fabricate, interpolate, smooth or synthesise any measurement, frame, spectrum or coordinate. If a step cannot produce a real value, it MUST report the reason and leave the value absent.
5. Scope is **five targets only**: Barnard's Star, Epsilon Indi B, WISE 0855-0714, RW Aurigae, R Draconis. MUST NOT extend backend analysis to other stars. Other cubes in the dataset (Cygnus X, Orion, COSMOS) stay indexed but unused by the demo.
6. Finish by producing the report in section 7. The teammate reviews that report and the diff; Codex MUST NOT commit or push.

## 1. What is already done (do not redo)

- The SphereX2 science backend lives in `science/` (copied unchanged except `main.py` renamed to `science/sci_main.py`). It is mounted inside our server at `/api/sci` (for example `/api/sci/api/local/products`). Its startup and shutdown run inside our app's lifespan (`main.py`).
- `science/demo_targets.py` is the five-target bridge at `/api/demo/*`. It picks data in this fixed order: **local dataset, then the persistent IRSA cache (`science/cache/`), then live IRSA** (live IRSA is skipped when `SPHEREX_OFFLINE=1`).
- Capture produces WebGL graphs (frontend `src/charts/`, `src/components/AnalysisDeck.jsx`). Shift + drag captures many stars (section 5).
- Python environment: `SphereX/.venv` (Python 3.14) has every science dependency installed. Use `SphereX/.venv/Scripts/python.exe` on Windows, or `.venv/bin/python` elsewhere.
- Without the dataset, all five targets produce **exploratory** results from IRSA frames, now held in `science/cache/` and usable offline. Final development run:

  | Target | Frames measured (all with approximate WCS wavelengths) |
  |---|---|
  | Barnard's Star | 12 |
  | WISE 0855 | 12 |
  | R Draconis | 12 (saturated, so inner-ring surface brightness only) |
  | Epsilon Indi B | 11 |
  | RW Aurigae | 12 |

  - No frame passed SphereX2's scientific acceptance.
  - IRSA intermittently returned HTTP 502 during development; the bridge retries 3 times and falls back to cached queries within 0.05°.
  - The dataset adds the DR1 spectral cubes (102-channel spectra) and local Level-2 frames with matched calibration.

## 2. Exact dataset layout (MUST match)

Root folder: environment variable `SPHEREX_DATA_ROOT`. Default, used when the variable is unset: `~/SphereX-data`, which is `C:\Users\<user>\SphereX-data` on Windows.

```
<SPHEREX_DATA_ROOT>/
  cached_products.json                optional; list of archive records ({"source_url", "id", ...}); used to recover provenance
  observations/<any subfolders>/*.fits  SPHEREx Level-2 images (must contain HDUs IMAGE, VARIANCE, FLAGS; header OBSID, DETECTOR, MJD-OBS, CRVAL1/2)
  cubes/<target_folder>/*.fits          SPHEREx DR1 spectral cubes (HDUs IMAGE [102 x 512 x 512], NHITS, FLAGS, SPECTRAL_CHANNELS table)
  <any folder>/manifest.jsonl           one JSON object per line: {"file": "<file name>", "source_url": "<IRSA URL>", "sha256": "...", "region": "<target name>", "ra": <deg>, "dec": <deg>}
```

Rules the indexer (`science/local_science.py::index`) applies. These rules are why the layout must match:

- Only `*.fits` directly or recursively under `observations/` and `cubes/` are indexed. Any other location is invisible.
- A file's **target** is the `region` field of its `manifest.jsonl` line. If there is no manifest line, the target is the **parent folder name**.
- The five targets are recognised after lower-casing and removing non-alphanumerics. Exactly these normalised names map to the demo targets (`science/local_science.py::_TARGET_NAMES`):
  - Barnard's Star: `barnardsstar` or `barnardstar`
  - Epsilon Indi B: `epsilonindib`
  - WISE 0855: `wise0855`
  - RW Aurigae: `rwaurigae` or `rwaur`
  - R Draconis: `rdraconis` or `rdra`

  So the folder names `barnards_star`, `epsilon_indi_b`, `wise_0855`, `rw_aurigae`, `r_draconis` are correct. MUST NOT use any other spelling (for example `eps_ind_b` is NOT recognised).
- A cube's field centre comes from its manifest `ra`/`dec`. A cube without a manifest line that has `ra` and `dec` gives no field centre, so `/api/demo` cannot locate it.
- A Level-2 file needs either a `cached_products.json` record whose `source_url` ends with the file name, or a manifest line with a `source_url` containing `/dr1/` or `/qr3/`. A file with neither is indexed but excluded from discovery ("lacks archive provenance").

Expected contents, from the dataset author's own run (`science/docs/validation/local-data-smoke.json`):

| Quantity | Expected value |
|---|---|
| Indexed Level-2 observations | **47** |
| Indexed spectral cubes | **8**: barnards_star, epsilon_indi_b, wise_0855, rw_aurigae, r_draconis, cygnus_x, orion, cosmos |
| Cube spectral channels | **102** each (channels 50 and 51 are blank by design) |

Expected cube product IDs:

| Cube | Product ID |
|---|---|
| barnards_star | `a961becafe10b2208e592832251f93e7` |
| epsilon_indi_b | `b747c362d227264b8b5369a52e5440d8` |
| wise_0855 | `8c191816ac45eb07ca61cd31c7cdd58d` |
| rw_aurigae | `bae44296e4a6765f6412ec2c77ca5e23` |
| r_draconis | `790cc3ea06a7821990e541075d349d9e` |

Level-2 frames covering each target (exploratory count):

| Target | Frames |
|---|---|
| Barnard's Star | 14 |
| Epsilon Indi B | 6 |
| WISE 0855 | 5 |
| RW Aurigae | 5 |
| R Draconis | 4 |

## 3. Steps (exact)

1. **Place the data.** Extract or copy the dataset so that section 2 holds at `C:\Users\<user>\SphereX-data`. If it is placed elsewhere, set `SPHEREX_DATA_ROOT` to that absolute path for every command below.
2. **Restart the backend.** The index is built once per process (`lru_cache`) and the data root is read at import, so a running server will not see new files. From `SphereX/`:
   - Windows PowerShell:
     ```
     $env:SPHEREX_DATA_ROOT="C:\Users\<user>\SphereX-data"
     .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
     ```
   - bash:
     ```
     SPHEREX_DATA_ROOT=~/SphereX-data .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
     ```
   - For a demo with the network off, also set `SPHEREX_OFFLINE=1`.
3. **Clear the stale demo bundles.** They were computed without the dataset. Delete `science/cache/demo/*.json` but keep `*.astrometry.json`. Alternatively, call `POST /api/demo/targets/<id>/analysis?refresh=1` for each of the five IDs:
   - `barnards-star`
   - `eps-indi-b`
   - `wise-0855`
   - `rw-aurigae`
   - `r-draconis`
4. **Recompute the five targets.** For each ID: `POST /api/demo/targets/<id>/analysis?refresh=1`, then poll `GET /api/demo/targets/<id>/analysis` every 2 s until `state` is `done` or `failed`. Expected time is under 60 s per target with local data.
5. **Verify** against section 4.

## 4. Acceptance criteria (all MUST pass; record each result in the report)

| # | Check | Pass condition |
|---|---|---|
| 1 | `GET /api/science/status` | `available: true`, `dataset_installed: true`, `data_root` equals the placed folder |
| 2 | `GET /api/sci/api/local/products` | 47 `observations` and 8 `cubes` in `products`; `wavelengths_um` has 102 entries |
| 3 | `GET /api/demo/targets` | for each of the five targets, `local.installed: true`, `local.cubes ≥ 1`, `local.observations ≥` the counts in section 2 |
| 4 | Each target's bundle (`GET /api/demo/targets/<id>/analysis`) | `bundle.sources` contains `local dataset: DR1 spectral cube`; `bundle.cube_spectrum.wavelength_um` has 102 entries; `bundle.status` is `science` or `exploratory` (never `unavailable`) |
| 5 | Epsilon Indi B and RW Aurigae | bundles now contain `cube_spectrum` and either `spectrophotometry.points` or `exploratory.points` (these two were empty before the dataset) |
| 6 | Offline run | restart with `SPHEREX_OFFLINE=1`; repeat check 4 for all five; zero outbound requests (the science code enforces this; confirm no `IRSA discovery` errors appear in `bundle.errors`) |
| 7 | Tests | from `SphereX/`: `.venv/Scripts/python.exe -m pytest -q tests` passes. From `SphereX/science/`: `../.venv/Scripts/python.exe -m pytest -q tests/test_local_science_smoke.py tests/test_research_smoke.py tests/test_local_fallback.py` passes or skips only network tests. From `SphereX/frontend/`: `npm test` passes and `npm run build` succeeds |
| 8 | UI (D subagent) | in the Sky view, press `C`. Click Barnard's Star at ≤ 5° field of view, then **Analyze · science**. The deck shows the group **Spectrum** containing "Local cube spectrum (102 channels)", and no chart shows "Chart unavailable". Repeat for the other four targets |

## 5. Multi-star capture contract (Shift), which the data must satisfy

- **Trigger:** hold Shift while dragging (or Shift-click, which frames 168 × 168 px) in Capture mode. The frame turns blue and dashed and shows `MULTI · up to N stars`.
- **Star cap, proportional to area:** `N = clamp(round(width_px × height_px / 2500), 2, 250)`, using CSS pixels of the frame. Implemented in `frontend/src/science/client.js::starCap`, tested in `frontend/src/lib/charts.test.js`. The brightest `N` stars are kept (by V magnitude; stars without V go last).
- **Star sources:** the stars drawn in the sky (HYG catalogue, V ≤ 8) plus SIMBAD stars in the frame. In multi mode, SIMBAD is searched 2.5 magnitudes deeper than in single mode.
- **Science scope:** backend science runs for every one of the five demo targets whose position falls inside the frame's circumscribed circle. Positions are checked at J2000, at survey epoch 2025.8, and today, so fast movers such as Barnard's Star and WISE 0855 still match. Any other captured star gets catalogue graphs only, never backend analysis.
- **Fields each captured star may carry:**
  - `ra`, `dec`, `mag` (V), `bv`, `bmag`, `jmag`, `hmag`, `kmag`, `pmra`, `pmdec` (mas/yr), `plx` (mas).
  - A graph is drawn only when its inputs exist. For example, the near-IR colour–colour diagram needs J, H and K for at least two stars.
  - The dataset adds nothing here, and MUST NOT be used to fill catalogue fields.

## 6. Workstreams for the five subagents

- **A Dataset placement:** do section 3 steps 1–2; confirm checks 1 and 2.
- **B Index verification:** check 3. If a target is not recognised, fix only the **folder name or manifest `region`** in a copy of the manifest, per section 2. MUST NOT edit `local_science.py`.
- **C Five-target analyses:** section 3 steps 3–4; checks 4, 5 and 6. For every failed step, copy `bundle.errors` verbatim into the report.
- **D Frontend checks:** check 8 and the frontend part of check 7, in a browser at `http://localhost:5173` with the backend from step 2.
- **E Report:** section 7.

## 7. Report (Codex output, for the teammate's review)

Write a file `SCIENCE_DATASET_REPORT.md` next to this guide containing:

1. The data root used and the exact counts from checks 2 and 3.
2. A table with one row per target. Columns: status (`science` / `exploratory`), sources, frames found, frames measured, cube spectrum (yes/no), and errors (verbatim).
3. The results of checks 1–8, each marked PASS or FAIL with the evidence (the command and the relevant output lines).
4. Every file Codex changed, with the reason. Expected: none outside the dataset copy, `science/cache/`, and the report itself.

## 8. What is NOT implemented (explicit limits; do not attempt unless the teammate asks)

1. **Science-grade measurements for these five targets are not guaranteed.** SphereX2's policy rejected every IRSA frame tried for them as "inspection-only quality". The app then shows **exploratory** values that are clearly labelled:
   - lower-limit apertures where core pixels are flagged;
   - inner-ring surface brightness for saturated R Draconis;
   - approximate WCS wavelengths when calibration (CWAVE/CBAND) is unavailable.

   The local dataset may supply matched calibration; whether it upgrades any target to `science` is unknown until run.
2. **Light curves mix wavelengths.** Each SPHEREx frame samples a different wavelength. The light curve, periodogram and phase-folded plots are exploratory and do not establish variability or a period.
3. **Time sequences (multi-epoch alignment, `/api/sci/api/sequences/*`) are mounted and callable but not wired into Capture.** The deck builds its time graphs from per-frame photometry instead. Sequence playback UI is not implemented.
4. **No catalogue cross-match from the dataset.** Gaia and WISE catalogues are not part of the dataset and are unavailable offline. Multi-star graphs use HYG and SIMBAD only, which needs the network for SIMBAD.
5. **Not attached from SphereX2:**
   - Firefly (Docker)
   - the Aladin HiPS proxy as a sky layer (the endpoint is mounted, but the sky uses its own 2MASS/DSS imagery)
   - the ImaginationSky/torch model (its own docs say inference is blocked)
   - its React research frontend
6. **Port 8000 during development:** an older backend process was still holding port 8000 and could not be stopped from the development session. The merged backend was verified on port 8001 (`.claude/launch.json` → `backend-science`, `frontend-science`). Use port 8000 after restarting normally.
7. **Python 3.14 only was tested.** Other versions are untested.
