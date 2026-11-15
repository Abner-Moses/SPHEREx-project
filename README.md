# SPHEREx Dataset Explorer

A local SPHEREx dataset workspace with observation tables, measured spectra and chronological image blinking. Enter ICRS RA/Dec or select one of five demonstration targets. The existing sky explorer remains available. See [CHALLENGE_ALIGNMENT.md](CHALLENGE_ALIGNMENT.md) for NASA challenge objectives, implemented behavior and scientific limits.

## Download the dataset (Git LFS)

The complete `SphereX-data/` folder is versioned in this repository. Its 93 FITS files use **Git Large File Storage (LFS)**: ordinary Git stores small pointers, while Git LFS downloads the actual scientific files. The full dataset is about 5.6 GB; allow roughly 12 GB of free disk space for the working files and the local LFS cache.

### 1. Install Git LFS

Install Git and [Git LFS](https://git-lfs.com/) before cloning:

- **macOS with Homebrew:** `brew install git-lfs`
- **Ubuntu/Debian:** `sudo apt-get update && sudo apt-get install git-lfs`
- **Windows:** download and run the Windows installer from the Git LFS website, then open a new Git Bash or PowerShell terminal.

Enable LFS once for your user:

```bash
git lfs install
git lfs version
```

### 2. New clone

```bash
git clone https://github.com/Abner-Moses/SPHEREx-project.git
cd SPHEREx-project
git lfs pull origin
```

Cloning normally downloads LFS files automatically; the final pull also retrieves any files that were skipped or interrupted. GitHub's **Download ZIP** is not a reliable replacement for this workflow because archives may contain LFS pointers instead of FITS data.

### 3. Existing checkout

From the repository root, update the source and download the dataset:

```bash
git lfs install
git pull --ff-only origin main
git lfs pull origin
```

If you deliberately configured LFS include/exclude filters, explicitly request all dataset files:

```bash
git lfs pull --include="SphereX-data/**" --exclude="" origin
```

For this original working folder only, a repository-local Git LFS binary is also available in `.audit/git-lfs-bin/`. In macOS/Linux shells, `export PATH="$PWD/.audit/git-lfs-bin:$PATH"` makes it available without a system installation. Fresh clones need the normal installation in step 1; `.audit/` is not versioned.

### 4. Verify the download

```bash
git lfs ls-files
git lfs fsck
```

The listing should contain 93 FITS paths. `git lfs fsck` verifies local LFS objects. After installing Python, this additional check confirms the working files are real FITS rather than text pointers:

```bash
python -c "from pathlib import Path; files=list(Path('SphereX-data').rglob('*.fits')); assert len(files)==93, f'Expected 93 FITS files, got {len(files)}'; assert all(p.open('rb').read(6)==b'SIMPLE' for p in files), 'An LFS pointer or incomplete FITS file remains'; print('93 real FITS files ready')"
```

Expected contents: **47 Level-2 observations, 8 spectral cubes, and 38 calibration FITS files**. The cubes contain 102 channels; channels 50 and 51 are intentionally blank. Download manifests include original source URLs and SHA-256 checksums.

### 5. Start with local data

Keep the downloaded folder at `SphereX-data/` beside `main.py`. The merged backend finds it automatically. Install the application dependencies using the Run section below, then start the backend:

```bash
# macOS/Linux, from the repository root:
SPHEREX_OFFLINE=1 .venv/bin/python main.py
```

Windows PowerShell:

```powershell
$env:SPHEREX_OFFLINE="1"
.\.venv\Scripts\python.exe main.py
```

Restart the backend after downloading the data: the index is cached per process. `GET http://127.0.0.1:8000/api/science/status` should report `available: true` and `dataset_installed: true`. The inventory is at `/api/sci/api/local/products`.

### Troubleshooting

- **`git: 'lfs' is not a git command`:** install Git LFS and reopen the terminal, then repeat `git lfs install` and `git lfs pull origin`.
- **FITS files are only a few text lines or start with `version https://git-lfs.github.com/spec/v1`:** these are pointers. Run `git lfs pull origin`; do not rename or edit the files.
- **Download interrupted:** rerun `git lfs pull origin`. Already downloaded objects are reused.
- **Objects downloaded but working files remain pointers:** run `git lfs checkout`, then repeat the verification check.
- **Authentication, quota, or missing-object error:** read the LFS error message; repository access or the owner's GitHub LFS allowance may need attention. Such errors cannot be fixed by the application API key.

## Run

Backend (project folder):

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1   |   macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-science.txt
python main.py            # http://127.0.0.1:8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173
```

Try it: press **Open the Barnard's Star SPHEREx preview**. The uploaded `SphereX-data/` is used first for observation lists, real detector images and five-target science. Original FITS files remain read-only. For a fully local backend demo, run `SPHEREX_OFFLINE=1 .venv/bin/python main.py`; uncovered images return an explicit error.

Voyager loads server secrets from the repository-root `.env` (copy `.env.example` for a fresh setup). Set `DEEPSEEK_API_KEY` there and restart the backend. The local `.env` takes precedence over exported variables; restart after changing it. `.env` is ignored by Git.

Voyager (the AI guide): `DEEPSEEK_API_KEY` (required for Voyager to answer; stays on the server), `DEEPSEEK_MODEL` (default `deepseek-flash`, DeepSeek's V4.1 Flash tier), `DEEPSEEK_THINKING` (default `disabled` for fast replies), `DEEPSEEK_TIMEOUT` (default 30 s), `DEEPSEEK_BASE_URL`.

Environment: `SPHEREX_CACHE_DIR`, `SPHEREX_DOWNLOAD_TIMEOUT` (default 900 s), `SPHEREX_CORS_ORIGINS`, `SPHEREX_DATA_ROOT` (merged-server default: repository `SphereX-data/`), `HOST` (default `127.0.0.1`), `PORT`, `SPHEREX_COLLECTION` (default `spherex_qr3`).

## Tests

```bash
python -m pip install -r requirements-dev.txt && python -m pytest -q tests  # backend (offline)
cd frontend && npm test                                                # projections, WCS mesh, statistics
python tools/validate_archive_coverage.py                              # live archive check (network)
python tools/fetch_sky_data.py                                         # regenerate frontend/public/data/sky.json from SIMBAD
```

## What is accurate, and how it is checked

* **Projections** (stereographic, perspective, orthographic, equidistant, all-sky map) are computed in float64 on the CPU, each with an exact inverse. Tests: round trips below a micro-pixel at every zoom, pointer-grab and zoom-about-cursor invariants, and sub-arcsecond precision at the zoom ceiling (field of view 0.001° = 3.6″).
* **Image placement**: the backend samples the file's full SIP WCS on a 65×65 lattice; interpolating it reproduces the WCS to ~0.02″ (measured 0.078″ worst case on a real file with the earlier 33×33 lattice). The client inverse matches astropy's answer for Barnard's Star to 0.03 px.
* **Epoch**: stars move by SIMBAD proper motion to the chosen date. Barnard's Star has moved ~277″ since J2000 (about 45 detector pixels); at the image's own date it lands on the real bright star in the image.
* **Pixel mask**: follows the FLAGS table of the real files. Bit 21 `SOURCE` is informational and is not masked; defect/artifact bits are. (The earlier code had this inverted.)
* **Physical units**: the PNG is an 8-bit view clipped at the 1st/99th percentile; the meta gives `lo`/`hi` in MJy/sr. Photometry (`/api/products/{id}/photometry`) uses unclipped data and reports `lower_limit` when masked pixels (e.g. a saturated star core) fall in the aperture.

## API (see `main.py`)

`POST /api/observe` · `GET /api/products/{id}/image.png | mask.png | photometry` · `GET /api/observations` · `GET /api/resolve | suggest | stars`. `obs_id` is not unique (one exposure yields an image per detector): select with `obs_id` + `detector`. Archive searches are cached for 10 minutes and concurrent identical searches share one request, so listing the observations and opening an image cost one archive call between them.

## Science backend and capture graphs

The SphereX2 science backend (`science/`, from the teammate's repository) runs inside this server:

* **Install:** `python -m venv .venv`, then `.venv/bin/python -m pip install -r requirements-science.txt` (Windows: `.venv\Scripts\python.exe`).
* **Run:** `.venv/bin/python -m uvicorn main:app --port 8000`. Set `SPHEREX_OFFLINE=1` to forbid network access, and `SPHEREX_DATA_ROOT` to point at the local dataset (merged-server default `./SphereX-data`, resolved relative to `main.py`).
* **APIs:**
  * Its full API is at `/api/sci/api/*`, unchanged.
  * `/api/demo/*` is the five-target bridge (Barnard's Star, Epsilon Indi B, WISE 0855-0714, RW Aurigae, R Draconis): local dataset first, then the persistent IRSA cache, then live IRSA. Each target's bundle is stored in `science/cache/demo/`.
  * `/api/science/status` says whether the backend loaded.
* **Capture → Analyze** opens a deck of WebGL graphs, about 30 types, drawn only from real inputs:
  * **Science graphs** (only when a demo target is inside the frame): SED, linear spectrum, λFλ, local cube spectrum, light curve, Lomb–Scargle periodogram, phase-fold, spectral–temporal sampling, radial profile, ePSF heatmap/3D/profile, image 3D surface, contour map, heatmap, pixel histogram, CDF, native diagnostics, quality flags.
  * **Multi-star graphs:** colour–magnitude, HR, J−H/H−K, luminosity function, log N–m, proper-motion vector-point diagram, sky motion map, distance histogram, parallax–magnitude, box plot by colour class, radial density, nearest-neighbour separations.
  * **Frame graphs:** captured-frame 3D surface, heatmap, RGB histogram.
* **Shift + drag** captures every star in the frame, up to `clamp(round(area_px² / 2500), 2, 250)`, brightest first.
* **Dataset:** the uploaded local copy contains 47 observations and 8 cubes (102 nominal channels, 50/51 intentionally blank). All five target bundles have been recomputed and verified offline. See `SCIENCE_DATASET_REPORT.md` and `audit-report.md` for actual measurements and validation. The complete original dataset is versioned through Git LFS; regenerable caches remain excluded. Manifests retain source URLs and SHA-256 checksums. Install Git LFS before cloning, then run `git lfs install` and `git lfs pull` to download all 93 original FITS files. Cached target analysis bundles remain included.

## Sky view (Stellarium-style)

The Sky view draws the team's Stellarium-style sky (`spherex-code`) through this app's float64 projection, so SPHEREx footprints, image overlays, epochs, Capture and Voyager keep working:

* **Stars:** 41,487 HYG v4.1 stars to V 8 (`frontend/public/data/hyg.json`, built by `tools/build_sky_catalogue.py`), drawn with a two-Gaussian point-spread profile and B−V colour. About V 5.6 shows when wide, rising to V 8 by a ~26° field, with stars fading in at the limit. SIMBAD's fainter stars still stream in when zoomed in.
* **Imagery:** 2MASS near-infrared (default) or DSS visible HiPS tiles under the stars (`engine/hips.js`). The 24 base tiles ship in `public/surveys`; deeper tiles (up to order 6, ~7″ per pixel) stream from IRSA and STScI. Tile orientation was measured against catalogue stars: survey stars sit on HYG stars (4.1× the background at star positions; every other flip scores ~1.0). The original code's orientation was one of the wrong ones.
* **Constellations** (d3-celestial lines and names), and the **Sun and planets** from JPL mean elements at the chosen epoch (`lib/planets.js`, tested against published positions to within 1°).
* **Easier to use:** a right-hand rail with zoom ±, the sky span on screen, Sky / Field / Detail framings and the IR / VIS / OFF imagery switch. Hovering names a star (type, magnitude, distance). Keys: `+`/`−` zoom, arrows pan, `K` constellations, `B` imagery.
* Not taken: the ground/horizon landscape (SPHEREx sees the whole sky from orbit, and a horizon would hide half the coverage), the WASM camera (the JS projection already runs in float64), and the FITS research workspace (the analysis tools here already cover loaded SPHEREx images).

Measured draw time in the browser: 7–13 ms per frame from a 4° to a 180° field, and it only redraws when something changes.

## Motion

The sky is built to be comfortable to move around in, and every rule below is enforced in `engine/skyEngine.js` and `engine/cameraRig.js`:

* Dragging is 1:1 "grab the sky" (the point under the pointer stays under it) with no coasting.
* Wheel zoom is a critically damped spring on log(field of view): it starts from rest, never overshoots, and zooms about the view centre so the sky never sweeps sideways. Measured: three notches land exactly on the expected field of view, monotonically.
* Programmatic moves (search, fly-to, double-click) take a fixed 1 s with a smoothstep ease-in-out while the centre travels the great circle and the field of view changes in step. Measured: starts at 0.65°/s against a 19°/s peak, peaks at 483 ms, ends at 0.
* Nothing moves on its own: no auto-rotation, no pulsing or spinning markers, no idle redraws. `prefers-reduced-motion` makes every move instant.

### 3D views (Depth and 3D surface)

Both use one camera, `engine/cameraRig.js`, built to the same comfort rules. Each fixes something that made the old orbit camera nauseating:

* **The horizon never rolls.** Up is always up, and tilt stops short of straight up or down (the surface view stays between 6° and 85° above the floor), so the view can't whirl near the poles or flip under the surface.
* **Depth view has two modes.** It opens in *Overview*, which orbits the Sun at a gentle 120° per screen height (it was a fixed 0.34°/px). *Look around* works like Stellarium: you stand at the Sun, dragging grabs the sky exactly (closed-form solve, tested to < 0.5 px), and scroll narrows the field of view instead of moving you, so nothing rushes at you.
* **Keyboard:** `W A S D` move (fly in Look around, slide across the scene in orbit), `Q`/`E` down/up, arrows turn, `Shift` faster, `Home` or `R` resets. Movement eases in and out over ~0.2 s. Single-letter sky-layer shortcuts only apply in the Sky view.
* **Star sprites are bounded** (0.5×–2× by distance, hard 30 px cap) and fade within a few light-years of the camera, so you pass stars instead of flying through screen-filling flashes. The Sun is a fixed-size point, not a sphere that balloons as you approach.
* Angles follow input through a 50 ms smoothing (no jitter, no coasting). Zoom is a critically damped spring. Reset and mode switches take a fixed time with smoothstep. Both views redraw only while something changes.

Performance (measured in the browser): panning holds 60 fps at a 1.2 ms mean draw (2.2 ms with the image overlay loaded); an idle sky does zero work.

## Look and intro

The interface uses the **Endurance Deck** theme (`frontend/src/deck.css`; concepts and research in `design/`):
- **Materials:** charcoal and gunmetal.
- **Colour roles:** amber marks focus and the primary action, red marks live or alert, blue marks anything selectable.
- **Type:** Michroma for names, IBM Plex for data and text, Doto for the two clocks.
- **Deck pieces:** a command-line search, an attitude dial, a flight log of real events, and sky epoch vs your time with the gap between them.

First visits play a 34 s pixel-art intro film, then a boot sequence (`frontend/src/intro/`, lazily loaded, all sound generated with Web Audio). Returning visitors see only the boot. A Y/N prompt enables sound, Esc skips at any point, reduced-motion users go straight to the app, and the menu has "Replay the intro film".

## Capture

Press **C**, or use the viewfinder button beside the view switches, to freeze the sky (in Sky or Depth view). Then drag a frame around a star or body (or click for a quick frame). In Depth view the 3D camera holds still while you frame. Standing at the Sun, the frame is a real patch of sky and SIMBAD fills in fainter stars; elsewhere only the 3D map's own stars are identified, with their distance from you.
- **Snapshot:** the frame is grabbed from the sky canvas.
- **Identification:** objects come from what's drawn there, plus SIMBAD for fainter objects inside the frame, using the backend `/api/stars`.
- **Images:** the result card lists every SPHEREx image covering the identified object (`/api/observations`) and can open the latest. It can also select the object or hand it to Voyager.

## Voyager

A pixel-art guide (`frontend/src/voyager/`, backend `voyager.py`, `POST /api/voyager`).
- **Asking:** press **V** or click it to ask about anything on screen. The target card also has an "Ask Voyager" button.
- **Request:** the client sends the question, the humour and detail settings (default 50 / 60), the list of things currently on screen that Voyager can reach, and a compact context snapshot.
- **Reply:** `deepseek-flash` in JSON mode returns one command, `{say, move, point, face}`. Voyager flies over with a short arc and stretch, lands with a squash, extends its boom to point, and types the line.
- **Offline:** without a key or a backend, it says so plainly instead of making something up.
- **Voice and prompt:** documented in `design/voyager-voice.md`.

## AI tools (placeholders)

`frontend/src/model/index.js` defines the contract for the planned 6M-parameter model: `runTask(task, ctx) -> Result`. Today every result is a stand-in and is labelled "placeholder output": proper-motion track, flag statistics, a blackbody curve with the real measurement overlaid, a classical peak finder, and hand-placed regions for the Barnard's Star preview. The model file will be cached with the Cache Storage API (`modelStatus / installModel / removeModel`); set `MODEL_URL` when the weights exist. The AI explainer (`src/explainer.js`) expects `POST /api/explain`; the API key must stay on the server.

## Known limits

* HYG stars through V=8 and constellation lines/names are bundled. Fainter stars and catalogue enrichment come from SIMBAD on demand and require network access; offline Capture retains the bundled stars. Deeper HiPS imagery and Google Fonts are external browser resources; only base imagery and CSS fallback fonts are available without network.
* Live downloads are ~60 MB per image; on slow links the first fetch takes minutes (it resumes if interrupted).
* The sky overlay uses one image at a time. The dataset workspace supports chronological blinking of real WCS-aligned local observations, with explicit exploratory/comparable modes and shared display limits.

## Audited validation

Run the suites separately, since each backend owns a `test_pipeline.py` module:

```bash
.venv/bin/python -m pip install -r requirements-science.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
(cd science && SPHEREX_OFFLINE=0 SPHEREX_DATA_ROOT="$PWD/.test-no-dataset" ../.venv/bin/python -m pytest -q tests)
(cd science && SPHEREX_OFFLINE=1 SPHEREX_DATA_ROOT="$PWD/../SphereX-data" ../.venv/bin/python -m pytest -q tests/test_local_science_smoke.py tests/test_research_smoke.py tests/test_local_fallback.py)
(cd frontend && npm ci && npm test && npm run build)
```

The full science unit suite uses isolated fixtures and mock transports; the explicit local-data smoke run uses the unchanged uploaded FITS. `SPHEREX_OFFLINE=1` blocks backend remote discovery, FITS, calibration and SIMBAD requests. It does not disable browser requests for deeper HiPS tiles or web fonts. Voyager needs its server-side API key and an internet connection; no key is bundled.

Dataset update: `/api/dataset/observations?ra=&dec=` searches only local FITS footprints and registers their provenance for sequence preparation. It never falls through to a remote archive. All five target sequences were verified with distinct real PNGs; see `audit-evidence/blink-api.json`.

Dataset checkout requires Git LFS. The original dataset contains 153 files (5,585,354,234 bytes), including 93 FITS products; FITS content is stored as LFS objects rather than ordinary Git blobs.
