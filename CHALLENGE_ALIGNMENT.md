# Planet X and SPHEREx — submission scope

Challenge: [NASA Space Apps 2026: Planet X and SPHEREx](https://www.spaceappschallenge.org/2026/challenges/planet-x-and-spherex/).
The official summary was found in indexed NASA Space Apps results. Direct retrieval of the detail page failed during this session; the [HackAthlone event mirror](https://hackathlone.com/challenges/planet-x-and-spherex) supplied the objectives and considerations. This is an independently built challenge submission, not NASA-certified analysis.

The core objective is a public web tool accepting RA/Dec and displaying SPHEREx observations as a time sequence. The named tests are Barnard's Star, Epsilon Indi B, WISE 0855, RW Aurigae and R Draconis. Optional considerations include spectral inspection, image combinations, field of view, other catalogues, coordinate URLs and contamination metadata.

| Objective | Implementation / evidence |
|---|---|
| Coordinate input | Dataset workspace accepts ICRS RA/Dec and field of view; discovery uses only local Level-2 footprints. Empty coverage is reported. |
| Time sequence / blinking | Existing sequence backend produces chronological WCS-aligned frames; workspace supplies play/pause, previous/next and rate selection. |
| Five named targets | All five tested with genuine local frames: 6 / 6 / 5 / 5 / 4 distinct prepared images. `audit-evidence/blink-api.json`. |
| Spectral data | Actual 102-channel DR1 cube samples in a table; missing channels remain absent. CSV exports preserve missing values. |
| Provenance / contamination | Product identifiers, real dates, detector/calibration/quality metadata, original source URLs, sequence warnings and FITS downloads. |
| User interface | Default dataset tables and blink inspection, retaining Endurance Deck styling. Existing sky remains accessible. |

## Scientific limits

Every image originates from an indexed local FITS observation. Blinking is a display of measured data after WCS reprojection, not generated images. A common physical-value display stretch is used across a sequence. Detector wavelength, poor quality, saturation, registration and PSF differences can resemble temporal change. Exploratory mixed-band playback does not establish motion, variability, a period or a discovery. Comparable mode retains the existing strict quality and calibrated spectral matching rules; insufficient data is an explicit result.

Cube samples are combined spectral products, not temporal frames. Cube variance is unavailable; NHITS measures coverage. Photometry distinguishes accepted measurements, exploratory measurements and lower limits. No interpolated blank channels, synthetic frames, AI estimates or invented catalogue values are used in the dataset workspace. Optional image subtraction, cross-survey overlays and automatic candidate identification are not claimed.

## Run

```sh
SPHEREX_OFFLINE=1 .venv/bin/python main.py
# second terminal
cd frontend
npm run dev
```

Open http://localhost:5173. Pick a local test target or enter coordinates, load the dataset, then prepare a sequence. Source FITS stay read-only. Derived aligned FITS and preview PNGs use the existing bounded cache. `.env` remains excluded from Git; the dataset workspace does not require an AI API key.
