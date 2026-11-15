# SPHEREx science dataset verification

**Dataset root:** `/Users/abnermoses/Downloads/SphereX/SphereX/SphereX-data`  
**Run:** local backend on `http://127.0.0.1:8000`, `SPHEREX_OFFLINE=1`. Data stayed read-only. SHA-256 verification covered 93 original FITS files (47 observations + 8 cubes + 38 calibration files): `verified: 93`, `all_match: true` (`audit-evidence/dataset-integrity.json`).

## Indexed data and target bundles

`GET /api/sci/api/local/products` returned **47 observations, 8 cubes, 102 wavelengths**. `GET /api/demo/targets` returned installed local data and one cube for each target. The table records WCS-covering discovery counts from each completed bundle; the target endpoint's regional inventory counts are listed in check 3.

| Target | Bundle status | Sources | Frames found / measured | Cube spectrum | Errors (verbatim `bundle.errors`) |
|---|---|---|---:|---|---|
| Barnard's Star | exploratory | local dataset: DR1 spectral cube; local dataset: Level-2 observations | 25 / 11 | yes, 102 channels (100 finite) | `[]` |
| Epsilon Indi B | science | local dataset: DR1 spectral cube; local dataset: Level-2 observations | 6 / 6 | yes, 102 channels (100 finite) | `[]` |
| WISE 0855-0714 | science | local dataset: DR1 spectral cube; local dataset: Level-2 observations | 5 / 5 | yes, 102 channels (100 finite) | `[]` |
| RW Aurigae | exploratory | local dataset: DR1 spectral cube; local dataset: Level-2 observations | 5 / 5 | yes, 102 channels (100 finite) | `[]` |
| R Draconis | exploratory | local dataset: DR1 spectral cube; local dataset: Level-2 observations | 4 / 4 | yes, 102 channels (100 finite) | `[]` |

Each target has one local cube. For all five, sources include both required local dataset labels, the cube wavelength array has 102 entries, and the state is not unavailable. Epsilon Indi B has 3 science-accepted plus 3 exploratory photometry points; WISE has 3 plus 2; RW Aurigae has 5 exploratory points. Bundle-level `errors` is the empty array for every target. Individual rejected-frame reasons are retained verbatim in `audit-evidence/workstream-c-targets.json` under `rejected_verbatim` and `exploratory_skipped_verbatim`; those include quality and coverage rejections, separate from bundle errors.

## Checks 1–8

1. **PASS — science status.** `GET /api/science/status` (HTTP 200; captured in `audit-evidence/workstream-b-index-post-repair.json`): `available: true`, `dataset_installed: true`, `data_root: /Users/abnermoses/Downloads/SphereX/SphereX/SphereX-data`, `offline: true`.
2. **PASS — local products.** `GET /api/sci/api/local/products` (HTTP 200; same evidence): `observation_count: 47`, `cube_count: 8`, `wavelengths_um_count: 102`.
3. **PASS — five-target local coverage.** `GET /api/demo/targets` (captured in `audit-evidence/workstream-c-targets.json`): all five report `local.installed: true`, `local.cubes: 1`; observations: Barnard 26, Epsilon Indi B 7, WISE 5, RW Aurigae 5, R Draconis 4. Each meets the guide's minimum.
4. **PASS — bundle spectra and sources.** `POST /api/demo/targets/<id>/analysis?refresh=1`, then `GET /api/demo/targets/<id>/analysis`; all five completed. `audit-evidence/workstream-c-targets.json` records `check4_pass: true`; each has both local sources and a 102-entry `cube_spectrum.wavelength_um`, and statuses are exploratory or science.
5. **PASS — formerly empty target plots.** Epsilon Indi B has a 102-channel cube and nonempty `spectrophotometry.points` (3 accepted, plus 3 in `exploratory.points`); RW Aurigae has a 102-channel cube and nonempty `exploratory.points` (5). Evidence: `science/cache/demo/eps-indi-b.json`, `science/cache/demo/rw-aurigae.json`, and `check5_pass: true` in `audit-evidence/workstream-c-targets.json`.
6. **PASS — offline recomputation.** With `SPHEREX_OFFLINE=1`, monkeypatched `requests.Session.request` and `httpx2.AsyncClient.request` to count and fail outbound requests, then recomputed all five. `audit-evidence/workstream-c-offline-guard.json`: `outbound_attempt_count: 0`, `outbound_attempts: []`; every bundle completed and every `errors` array is `[]`. `check6_pass: true` in target evidence.
7. **PASS — test suites and production build.** Root `.venv/bin/python -m pytest -q tests`: `50 passed` (`audit-evidence/root-tests-final.txt`). Science full isolated pytest suite: `615 passed, 8 skipped` (network-dependent skips; `audit-evidence/science-tests-final.txt`). The guide-specified smoke subset also passed; the dedicated real-local run was `13 passed` (`audit-evidence/science-local-tests.txt`). Frontend `npm test`: `44 pass, 0 fail`; `npm run build`: `✓ built` (`audit-evidence/frontend-tests.txt`, `audit-evidence/frontend-build.txt`).
8. **PASS — browser checks.** Five targets were analyzed at 2° FOV with local cube spectra; each recorded `localCube: true`, `unavailableAll: []`, `unavailableSpectrum: []`, and no page errors (`audit-evidence/frontend-browser-results.json`). Sky Capture (`C`), Barnard's Star preview and Analyze · science were exercised; Detector, Surface, and Depth views passed with no page errors (`audit-evidence/sky-overlay-browser.json`). The analysis deck displayed “LOCAL CUBE SPECTRUM (102 CHANNELS)” for all five. `audit-evidence/sky-integration.json` separately verifies real image PNGs, masks and WCS positions for all five targets with zero backend outbound requests.

## Report file changes

- `SCIENCE_DATASET_REPORT.md` — records the dataset counts, per-target bundle results, errors, and evidence for checks 1–8.
- `audit-report.md` — summarizes the repair work, validation, limits, changed source files, and publishing scope.

See the changed-file inventory in `audit-report.md` for every repaired source/project file and generated artifact group. The original dataset, `science/local_science.py`, theme/styles, science acceptance policy, model limitations and unused regional cubes were preserved.

Publication: the validated source was committed and pushed to `main` at https://github.com/Abner-Moses/SPHEREx-project.git; remote SHA verification is recorded in `audit-evidence/publishing.json`. Original FITS remain unchanged and were subsequently added using Git LFS at the user’s request; manifests, cached bundles and evidence are versioned.
