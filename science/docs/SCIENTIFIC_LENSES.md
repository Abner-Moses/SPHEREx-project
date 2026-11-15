# Scientific Lens environment

## What changed

The production entry is `frontend/src/research/ResearchApp.jsx`. It uses one
`useScientificExplorer` controller, the existing Aladin/WebGL/WASM renderer,
Firefly original-FITS viewer, ResultsTable, scientific inspector and TimeSequence.
The earlier App and Arjun source are retained as diagnostics/experimental code;
they are not imported by the production entry. Their independent projection tests
are preserved and run under Node instead of incorrectly through Vitest/jsdom.

The validated Python pipeline remains authoritative. There is no Rust numerical
rewrite or new inference runtime: local science is already subsecond, whereas
archive/calibration access dominates. An Axum/Rust migration is **not implemented**.
Changing languages without validating WCS, masks and calibration would not be an
acceptable scientific optimization. Aladin's established GPU/WASM path is reused.

## Public workflow

1. Enter decimal ICRS RA/Dec, or HMS/DMS (RA hours), and a 0.005–4.8° field.
2. Search & play observations returns metadata before retrieving any FITS.
3. A bounded candidate subset is automatically prepared on the common WCS; the
   full archive table remains available. Initial candidate detector follows the
   selected HiPS channel's detector to avoid six unnecessary calibration downloads.
4. If multiple science-accepted calibrated compatible frames exist, local playback
   starts. Play, pause, loop, step, scrub, frame rate and blink remain available.
5. If quality/calibration/coverage is insufficient, the actual reason is displayed;
   no relaxed tolerance, substituted image or synthetic frame supplies success.

Candidate detector selection does not establish wavelength compatibility. The
existing target CWAVE/CBAND rule is still required. The sequence now chooses the
largest deterministic seed-built pairwise-compatible group among prepared
candidates, with chronological tie-breaking. Every accepted pair is tested in
both directions. This is not a globally exhaustive clustering algorithm or a
claim to have downloaded every available epoch. Retrieval-attempt caps and all
rejections remain visible. The scientific target stays fixed in ICRS; object
resolution may set its initial epoch-aware center but never removes observed motion.

## Lens architecture and API

- `lens_registry.py`: typed ICRS circle/gnomonic rectangle/simple spherical polygon,
  execution/result contracts, eight families, conditional product/prerequisite
  matrix. All scientific categories are explicit.
- `lens_api.py`: read-only, bounded computation on existing assets/sequences. No
  arbitrary URL/path, remote retrieval, file alteration, detection invention or AI.
- `frontend/src/research/LensCanvas.jsx`: one SVG selection/overlay surface above
  the existing sky. Projection uses Aladin `world2pix`/`pix2world`, not a new renderer.
  Reduced maps are clipped to the sky-anchored selection. Null cells stay transparent.
- `Visualization.jsx`: shared graph, histogram, table and heatmap primitives;
  descriptor-driven inspector and SVG export. Unsupported specialized 3D/orbit/
  posterior/contour renderers are not represented as implemented.
- `session.js`: bounded scientific parameter serialization, URL and JSON round-trip.
  No URL can declare that an old scientific asset is still valid.

`GET /api/lenses/capabilities` includes the authoritative complete implementation
matrix and JSON schemas. `POST /api/lenses/execute` accepts:

```json
{"lens_id":"spatial","asset_id":"<existing 32-hex asset>",
 "selection":{"frame":"icrs","shape":"circle","ra":337.8783221,
 "dec":-5.1891588,"radius_deg":0.025},
 "parameters":{"products":["brightness-map"],"maximum":32}}
```

Rectangle uses `width_deg` and `height_deg` instead of radius. Polygon uses 3–64
`vertices` instead. Parameters are typed/bounded; self intersections and invalid
geometry fail. Numeric scientific selection is performed using Astropy/full FITS
WCS. Native pixel-center membership is disclosed; this is not fractional angular
aperture photometry or a guarantee of complete region coverage.

Inspector products identify data, method, original source, software policy,
selection, quality, statistical/systematic omissions and visualization descriptors.
Selecting pixels remains possible through original scientific inspection; clicking
catalog/plot coordinates can navigate the viewer when an association is supplied.
Only the active lens recalculates after a 650 ms pause. Analysis waits during
playback, avoiding disk reads at animation rate. Pinned records retain their old
asset/selection instead of silently relabeling old results as current.

## Composition

`GET /api/sequences/composition/operations` exposes the typed operation registry.
`POST /api/sequences/{id}/combine?output=fits|preview|metadata` accepts:

```json
{"op":"subtraction","frame_ids":["<first>","<second>"]}
```

Addition and arithmetic mean are also implemented. Original/source IDs, dates,
units, current quality, exact common WCS and mask intersection are checked. The
FITS derivative records provenance; PNG is a display derivative. No PSF/background
matching or aligned variance/covariance exists. Therefore weighted mean, difference
significance and physical variability claims are explicitly unavailable. ORIGVAR
is never misused as an aligned weight. Repeating playback uses local assets.

## Catalogs, coordinates and releases

`POST /api/coordinates/parse` delegates decimal/HMS/DMS parsing/formatting to
Astropy with strict range/nonfinite/boolean rejection. ICRS frame is distinct from
a coordinate epoch. Exact 24h wraps; decimal 360° is invalid.

`GET /api/catalog/overlay?catalog=gaia_dr3|allwise&ra=...&dec=...&radius=.01&limit=500`
uses official IRSA TAP, preserves source fields/units/descriptions, validates rows
against the cone and discloses protocol status, MAXREC and native catalog epochs.
It has bounded time/bytes/concurrency/rate, an exact-query cache and deduplication.
Epoch differences mean a marker is not a SPHEREx detection or a propagated match.
Actual catalog data retrieval is currently unverified because live TAP calls
returned 502/504; table schemas were retrieved successfully. UI surfaces failures.

`data_providers.py` is the canonical verified release registry (DR1, QR3, and deep
collections), also used by archive query construction. `/api/providers` documents
product identity/cache policy. New releases require verification before adding a
scientific product policy. No guessed URL or old calibration is silently reused.

## Session and export

Share URLs retain RA, Dec, FoV, spectral channel, overlay choices, lens shapes,
positions and selected products. Existing sequence ID/frame/FPS may be shared,
but are revalidated through the actual API and may expire on restart. Parameter
restoration does not automatically launch expensive archive searches. Save/restore
uses local storage plus downloadable JSON. CSV and original VOTable exports remain
in the archive table. Original and aligned/composed FITS are downloadable. Plots
export SVG; lens products export complete JSON. Parquet/notebook and arbitrary
research-result persistence are not implemented.

## Start, build, verify

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
SPHEREX_SEQUENCE_CALIBRATION_TIMEOUT=180 python main.py
```

```sh
cd frontend
npm ci
npm run dev
npm test
npm run build
```

Optional scientific viewer service: `docker compose up -d firefly`.
Use one API worker because job coordination/IDs remain process-local.
Production local stack: `docker compose -f compose.production.yaml up --build`.
This configuration is provided; the new full production stack has not been
publicly deployed or fully verified in this task. Keep Firefly's FITS origin
reachable from its container (`SPHEREX_FIREFLY_FITS_BASE`).

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q
.venv/bin/python scripts/verify_research_targets.py --api http://localhost:8000
.venv/bin/python scripts/verify_backend_integration.py
```

## Scientific limits

- WCS-WAVE estimates are visual approximations, never calibrated molecular spectra.
- HiPS mosaic channel navigation is spectral; its pixels are never photometry inputs.
- Comparable policy applies at the target, not every pixel across an extended region.
- No validated source tracker, trained model, labels, Imagination weights, independent
  ML evaluation, PSF matcher, cosmological inference, abundance solver, orbit fitter,
  stellar atmosphere fitting, physical 3D reconstruction or calibration response
  estimator was found/introduced. Their capability states explain prerequisites.
- No independent-training scores, fabricated candidates or target detections are claimed.
- Advanced unsupported capability states do not constitute completed scientific methods.

See LENS_AUDIT.md, LENS_COVERAGE.md and LENS_VERIFICATION.md for exact evidence.

Reproduce asset-level HTTP verification without downloading observations:

```sh
.venv/bin/python scripts/verify_lens_assets.py --api http://localhost:8000 --asset <validated-asset-id>
```

Third-party versions, licenses and data acknowledgement are recorded in
THIRD_PARTY_NOTICES.md. The capability manifest includes 56 product entries,
92 execution representations and 128 requested research-representation rows;
most advanced physical inference remains explicitly unavailable.
