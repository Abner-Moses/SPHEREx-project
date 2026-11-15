# SphereX final audit report

## Completed repair work

- **Offline startup and truthful status:** root `main.py` now resolves the merged local data root, supports offline demo startup and cache fallback, reports the actual local index state, and carries local observation provenance through the API. The SIMBAD demo fallback is local/offline; core observation requests honor offline mode. Connected mode retains live archive access.
- **Local science path:** `science/demo_targets.py` uses the local index and read-only FITS products first, guards outbound discovery in offline mode, handles the WISE target alias, labels DR1 cube values as exploratory where warranted, and reports failed refreshes before considering stale successful bundles. `sky_services.py` blocks remote sky service calls in offline mode.
- **Frontend correctness:** `frontend/src/charts/builders.js` preserves blank spectral channels as gaps; `frontend/src/components/AnalysisDeck.jsx` reports measured frames rather than attempts; `frontend/src/explorer.js` selects an available local preview; `frontend/src/science/client.js` counts successful accepted and exploratory frame measurements; the existing area-based capture cap is unchanged. Regression coverage is in `frontend/src/lib/charts.test.js`.
- **Dependency and contract repairs:** `requirements.txt` now pins FastAPI to `0.163.1`, resolving its conflict with `requirements-science.txt` (dependency check confirms both installed requirements). Root regressions and science tests cover the offline/local behavior. `science/tests/test_pipeline.py` updates the prior upstream-error expectation to the explicit empty-fallback/HTTP 200 contract. `README.md` documents local data and offline use. `.gitignore` excludes large source FITS, regenerable caches, virtual environments and build outputs from publishing.

## Dataset and stitched partition validation

The placed dataset contains 47 observations, 8 cubes and 38 calibration FITS files. All **93 original FITS SHA-256 values match their manifests** (`audit-evidence/dataset-integrity.json`). Every cube's returned spectrum has 102 wavelength channels with 100 finite flux values; the two blank channels are retained as gaps. The five cached bundles were recomputed from the local index and validated: Barnard's Star (25 found / 11 measured), Epsilon Indi B (6 / 6), WISE 0855-0714 (5 / 5), RW Aurigae (5 / 5), R Draconis (4 / 4). All bundles are complete, carry local cube and Level-2 sources, and have `errors: []`. Epsilon Indi B and WISE reached science status; the other three remain exploratory. No original dataset files were written or altered. Derived caches and target bundles were regenerated. The other three regional cubes remain indexed but unused by the five-target demo.

## Validation run

- Root suite: `50 passed`.
- Full isolated science suite: `615 passed, 8 skipped`; skips cover absent fixture-isolated local data and the opt-in live archive test. Specified real-local smoke subset: `13 passed`.
- Frontend: `44 passed, 0 failed`; production Vite build succeeded.
- Offline guard: zero outbound request attempts while all five target bundles recomputed.
- Browser: all five target analysis views had local spectra and no unavailable charts or page errors; Barnard's preview, Capture/Analyze, Detector, Surface and Depth views passed.
- Dependency check: FastAPI `0.163.1` satisfies the aligned requirements.

Evidence is under `audit-evidence/` and `.audit/evidence/`. See `SCIENCE_DATASET_REPORT.md` for commands, acceptance checks 1–8, target-level results, and verbatim bundle error arrays.

## Existing integration retained

The science sub-application mount and shared startup/shutdown lifecycle, Capture target matching at multiple epochs, Shift-capture area cap, WCS overlay mesh, projections/camera behavior, and existing catalogue/science chart contracts were already connected. They were validated through their suites and browser flows and were not redesigned. The Endurance Deck CSS, intro, sky imagery assets, scientific acceptance policy and local indexer were preserved. Unattached optional services and intentionally blocked model workflows remain the handoff's explicit limits rather than new implementation work.

## Known limits

- Three targets remain exploratory because their frames did not pass scientific acceptance; Barnard's Star has lower-limit apertures, RW Aurigae has flagged-core lower limits, and saturated R Draconis uses the innermost unflagged ring. The DR1 cube is a combined product, not a temporal epoch; it has no variance, and channels 50/51 are blank by design.
- Light curves mix wavelengths and remain exploratory; no variability period is established. Sequence APIs are not wired to Capture playback.
- Gaia/WISE catalogue cross-match is unavailable offline; SIMBAD needs network for non-cached objects. The dataset does not populate catalogue fields.
- Deeper browser HiPS tiles and web fonts remain external resources. Voyager needs a server-side API key; the AI tools/explainer and model inference retain their explicitly labeled original limitations.
- Firefly, the Aladin sky layer, ImaginationSky/torch inference, and the SphereX2 research frontend remain unattached. Python 3.14 is the tested version. Browser verification used the local development services; check 8's five target UI run is at 2° FOV (within the guide's ≤5° condition).

## Changed files and reason

Source and project files changed from the `.audit/baseline.json` hashes:

- `main.py` — offline/data-root behavior, truthful status and local observation provenance.
- `spherex_pipeline.py` — local index to observation/readonly FITS integration and explicit offline failure instead of synthetic fallback.
- `science/demo_targets.py` — WISE alias repair, exploratory cube grading and failed-refresh stale-bundle protection; existing local-first analysis and offline guards were retained.
- `sky_services.py` — prevent external sky calls when offline.
- `frontend/src/explorer.js` — choose an available local preview.
- `frontend/src/science/client.js` — count successful measurements; retain existing capture area rules.
- `frontend/src/charts/builders.js` — leave blank wavelength channels disconnected.
- `frontend/src/components/AnalysisDeck.jsx` — show actual successful measurement counts.
- `frontend/src/lib/charts.test.js` — add frontend regressions for channel gaps and measured counts; retain capture tests.
- `tests/test_audit_repairs.py` — new regressions for WISE matching, cube grading, failed refreshes, local image loading and offline network guards.
- `tests/conftest.py`, `tests/test_pipeline.py` — isolate loopback HTTP and connected-mode fallback tests from offline demo mode.
- `science/tests/test_pipeline.py` — align the upstream failure test with the existing explicit empty-fallback contract.
- `tools/verify_submission.py`, `tools/verify_capture.cjs` — reproducible real-data API and browser validation scripts.
- `audit-evidence/` — final test logs, API/offline/integrity results and browser screenshots.
- `requirements.txt` — resolve FastAPI pin conflict with science requirements.
- `README.md` — document local dataset and offline run behavior.
- `.gitignore` — keep large original FITS, regenerable caches, `.venv`, `node_modules`, and `dist` out of the publish scope.

Generated target bundles in `science/cache/demo/` were refreshed; generated observation cache/index files in `science/cache/observations/` were regenerated/removed during validation. They are not source changes and are excluded from publication. `SCIENCE_DATASET_REPORT.md` and `audit-report.md` are the two audit deliverables.

## Publishing scope and state

The publishable source scope includes the project code, tests, manifests, documentation, five regenerated demo bundles, and validation evidence; it excludes the original 5.4 GB FITS dataset, regenerable caches, `.venv`, `node_modules`, and frontend `dist`. No `.git` repository existed at baseline. A fresh repository commit and push to `https://github.com/Abner-Moses/SPHEREx-project.git` are **pending**; this report does not claim they completed.
