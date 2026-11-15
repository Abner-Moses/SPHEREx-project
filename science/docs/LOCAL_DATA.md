# Local scientific demonstration

Keep the original dataset outside this repository. `SPHEREX_DATA_ROOT` defaults to `~/SphereX-data`. Indexing uses read-only observations, cubes and their download manifests. Only requested small Level-2 cutout derivatives enter the existing asset cache. Calibration associations use the observation's embedded version history, detector and release; ambiguous associations are rejected.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
SPHEREX_OFFLINE=1 SPHEREX_DATA_ROOT="$HOME/SphereX-data" .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
# Separate terminal:
npm --prefix frontend ci
npm --prefix frontend run dev -- --host 127.0.0.1
# Existing Firefly service, when required:
docker compose -f compose.production.yaml up -d firefly
```

Open http://localhost:5173. Select a local cube in the research inspector, choose its downloaded field center, extract a spectrum or calculate a feature map. Clicking a map cell selects its actual FITS-WCS position. The volume is a sampled spatial–spectral representation, never physical depth. Cube spectra are independent of temporal selection.

For temporal inspection, search a target field, then prepare a sequence. Comparable mode retains strict calibrated-bandpass and quality requirements. Explicit exploratory/all-observations mode permits inspection of mixed wavelengths and poor-quality observations; apparent differences are not established variability or motion. No synthetic frames are supplied.

`SPHEREX_OFFLINE=1` prevents remote FITS, calibration, catalogue, enrichment and uncached HiPS requests on the integrated workflows. Uncached all-sky HiPS imagery is unavailable offline; the sky canvas instead loads the actual WCS-registered local cube channel when its footprint covers the view center. This is a limited spectral image, never an all-sky or temporal substitute. Set `SPHEREX_OFFLINE=0` for connected operation. Local matches are complete only within the local index, never claimed to represent the complete archive.

## Scientific access

- `/api/local/products`: provenance and actual spectral channel centers.
- `/api/local/cubes/{id}/spectrum`, `/slice`, `/rgb`, `/feature`, `/volume`, `/position`: bounded lazy cube access. PNGs are display derivatives. Volume transfer is binary float32.
- `/api/research/spectrum`: independent native Level-2 aperture measurements with CWAVE/CBAND calibration, variance errors and rejected observations.
- `/api/research/assets/{id}/epsf`, `/radial-profile`, `/diagnostics`: instrument response, native profiles and statistics.
- `/api/research/centroids`: variance-weighted Gaussian centroids and formal covariance.
- `/api/research/sequences/{id}/motion`: conditional common-source linear motion fit for comparable sequences, not automatic source association.
- Discovery lens `native-peaks`: bounded variance-aware exploratory image candidates with quality/provenance; not calibrated detection probabilities.

The committed `validation/local-data-smoke.json` records actual offline results. Eight cubes expose 102 nominal channels, including explicitly missing channels. NHITS never supplies measurement variance. Feature maps use an unweighted linear continuum and measured channel-center trapezoids; optical depth and equivalent width require positive intensities and an appropriate continuum. No molecular identification or column density is inferred.

Formal aperture/centroid errors omit calibration and inter-pixel covariance. Common-PSF matching, robust temporal association, parallax/orbit inference and laboratory-calibrated molecular column density remain unavailable. Uploaded ImaginationSky weights are retained, but real-FITS inference and PairHead are blocked pending the exact training normalization and feature contracts. Tensor smoke tests do not establish scientific validity.

```sh
.venv/bin/python -m pytest -q tests/test_local_science_smoke.py tests/test_research_smoke.py
.venv/bin/python scripts/verify_local_demo.py
npm --prefix frontend exec -- vitest run --root frontend src/research/Visualization.test.jsx src/research/ResearchApp.test.jsx
npm --prefix frontend run build
```

Browser smoke (requires Playwright Chromium):

```sh
npm --prefix frontend exec -- playwright install chromium --only-shell
SPHEREX_DEMO_URL=http://localhost:5173 node frontend/scripts/verify-local-representations.mjs
```

The real Orion browser check rendered six covered spectral samples at the manifest field center and 16,319 finite feature-map cells; missing channels remain absent, not interpolated. The 512×512 display slice loaded and no page errors were observed. This checks UI integration, not molecular detection or absolute astrometric validation. Dense maps use bounded raster rendering, not one DOM element per pixel.

`SPHEREX_DEMO_URL=http://localhost:5173 node frontend/scripts/verify-local-playback.mjs` checks real Epsilon Indi B exploratory playback. Six registered frames were displayed; Next, Play, Pause and 5 FPS changed decoded astronomical pixels with zero upstream requests. Spectral/quality incompatibility remains warned. No strict comparable pair, blink validation, physical variability or motion detection is claimed. Original detector coordinates are retained when cropping an already-cropped IRSA product, preserving EPSF and CWAVE/CBAND associations.

## Automatic fallback

Local matches always win, including when connected mode is enabled. Remote product IDs are matched only to the same immutable filename and release; another exposure is never silently substituted. Exact persistent metadata and validated FITS/calibration caches remain usable offline. Upstream discovery failures return local or cached results with explicit scope/freshness; repeated failures have a two-minute per-service cooldown. DataLink calibration failures no longer initiate a second immediate request. Missing local TAP/DataLink fields remain explicitly unavailable.

The five downloaded target names resolve offline to labelled **acquisition-manifest field centers**, not manufactured SIMBAD identities or epoch-propagated source positions. Gaia/WISE catalogues remain unavailable offline unless independently supplied. Existing spectroscopy, photometry, source/centroid analysis and sequence services consume these same local scientific assets. Local data availability does not validate missing ML preprocessing, invent a detection, or relax scientific acceptance.

Offline sky regression: `SPHEREX_DEMO_URL=http://localhost:5173 node frontend/scripts/verify-offline-sky.mjs`. This loads a genuine local FITS channel through Aladin's supported FITS interface, retains the celestial HPX WCS, and asserts zero external HTTP requests. The derived plane preserves covered brightness samples and marks absent NHITS coverage as invalid; it is not a telescope time frame. The downloaded cubes only cover their actual footprints. Local asset admission also respects the shared cache budget and active-file leases.
