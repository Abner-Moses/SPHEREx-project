# Opus handoff — presentation mode

## Current state

The dataset/blinking workspace is already committed and pushed at `51a939c` on `main`, remote `https://github.com/Abner-Moses/SPHEREx-project.git`. This follow-up adds a presentation mode. The user requested a handoff now; implementation has stopped. **The latest revised presentation builds, but its complete flow has not been browser-validated or recorded yet. Do not describe the submission video as finished.**

Latest user directions supersede the original narration/order:

- Remove spoken voice. Keep music, interface clicks, captions and Voyager as the visual guide.
- Include the original first intro, slightly shortened rather than skipped.
- Begin the tour in the sky explorer with Barnard’s Star; then enter the data panel and blinking; then inspect other scientific observations.
- Spend longer slowly scrolling scientific information.
- Keep natural cursor movement and portray the important features for a NASA submission video.
- Commit/push the current work and hand it to Opus.

Timing needs care: the initial request specified 30 seconds. A later optional question asked whether that included the restored intro; no direct timing answer arrived. **Current code plays the original intro at 1.2×, followed by a 30-second tour. Total duration exceeds 30 seconds.** The startup UI discloses this. Do not silently claim the combined film is 30 seconds.

## What changed

- `frontend/src/Presentation.jsx`: startup mode selection, real-data preflight, original intro, scripted tour, Voyager captions, curved/smooth cursor paths, click rings, selection rectangle, sound controls, exit/cancel/replay, completion screen and action trace.
- `frontend/src/Presentation.css`: presentation styling using Endurance Deck colors/fonts; overlay positioning, cursor and captions.
- `frontend/src/main.jsx`: renders `PresentationRoot` instead of immediately entering the dataset workspace.
- `frontend/src/App.jsx`: optional presentation API ref for preparation, star centering, view switching and capture. Preloads the actual Barnard image and secondary views; suppresses the duplicate app intro/chat guide during the controlled presentation.
- `frontend/src/components/Capture.jsx`: optional ref exposes the same actual `shoot` callback used by ordinary user capture. Screenshots and source identification remain real.
- `frontend/src/DatasetWorkspace.jsx`: optional presentation API ref for readiness, sequence preparation, tab selection, target selection and playback. Added target buttons beside scientific tables so other targets can be inspected without repeatedly scrolling back to the top.
- `frontend/src/DatasetWorkspace.css`: styles the scientific target selector.
- `frontend/src/intro/Intro.jsx`, `intro.js`: optional `autoStart` and `timeScale`; defaults preserve ordinary behavior. Presentation plays all original intro/boot scenes with `timeScale=1.2`; intro audio is disabled because the external music bed continues through it.
- `frontend/public/demo/audio/presentation.mp3`: **music only**, 30-second loop. The earlier version had narration, but it was replaced after the user requested removal.
- `frontend/public/demo/audio/click.ogg`: Kenney CC0 online interface sound, bundled for offline playback. Full license included as `LICENSE-Kenney.txt`; credits in `frontend/public/licenses/DEMO-AUDIO.md`.
- `tools/build_presentation_audio.py`: encodes the music-only bed. **It currently depends on `.audit/demo-audio/original-underscore.wav`, which exists locally but is ignored by Git.** Preserve or make the source/generator reproducible before a clean-checkout rebuild.

The music follows the actual existing intro’s A-minor / 120 BPM character. No live AI API is required for the scripted tour. No scientific arrays, original FITS, measurements or quality policy were modified.

## Latest tour schedule (after intro)

| Tour time | Actual actions |
|---|---|
| 0–2 s | Barnard’s Star in the sky with calibrated image and object information |
| 2–5 s | Activate Capture, drag a frame, take the real screenshot, open Analyze · science |
| 5–10 s | Slowly scroll the analysis deck’s scientific charts and quality information |
| 10–11 s | Return to the dataset workspace |
| 11–16 s | Play the preloaded chronological local observation sequence |
| 16–20 s | Inspect and scroll the actual 102-channel cube table |
| 20–25 s | Inspect Epsilon Indi B, then WISE 0855 photometry |
| 25–27 s | Inspect R Draconis, then export its table |
| 27–30 s | Barnard detector Image, Depth and 3D surface views |

The action handlers use real existing controls and component callbacks. Data/sequence/image preparation occurs before the presentation clock starts. Source files remain read-only. All-mode blinking is explicitly exploratory; different wavelengths/quality can resemble variability. R Draconis ring brightness remains MJy/sr with lower limits, not Jy aperture photometry.

## Validation — distinguish the versions

### Latest source

- `npm --prefix frontend test`: **44 passed** (`.audit/evidence/presentation-tests.txt`).
- `npm --prefix frontend run build`: **succeeded** (`.audit/evidence/presentation-build.txt`).
- Existing nonfatal Vite warnings: shared static/dynamic imports for `api.js` and now `Intro.jsx`.
- The **restored intro, new sky-first order, slow scientific scroll and additional target selections have NOT had a complete browser run** since the latest edits.

### Earlier presentation version (before the user’s revisions)

A production-preview Playwright run did complete all nine chapters, with a 30-second tour, zero page errors, a real screenshot, analysis deck, Image/Depth/3D views, blink and CSV export. That version started in the dataset workspace, omitted the intro and originally included spoken narration. Its evidence is useful for mechanics, **not proof that the final requested flow works**.

- `.audit/evidence/presentation-browser-final.log`
- `.audit/demo-recording/result.json`: old action trace and feature list; `startOffset=4.479` in its raw recording.
- `.audit/demo-recording/page@d62e06e88bd81a309ac79b2fcce70d55.webm`: old raw silent browser video, includes preflight/completion screens.
- `.audit/demo-recording/scene-*.png`, `ready.png`, `done.png`: older screenshots.
- `.audit/check-presentation.cjs`: older QA/recording harness; **update its button names, intro handling, chapter timings and duration expectations before reusing.**

These `.audit` files are local working evidence and are excluded from Git.

## What remains for Opus

1. Browser-run the latest production build through the entire original intro and tour. Verify no voice, music continuity, intro/boot completion, sky-first ordering, actual captured screenshot, analysis scrolling, blinking, target-specific tables and all final views.
2. Confirm the scientific target buttons stay visible while switching Epsilon/WISE/R Draconis; scope automation under `.dw-science-targets` because target names also exist in the top target table.
3. Check late cue races: capture/analysis must finish before the dataset transition; other-target table loads must finish before export. The last three views have only about a second each.
4. Exercise cancel preparation, Escape, mute, replay and normal exploration. Replay must reset Barnard’s Star and Sky view, not retain the last target/view.
5. **Produce the actual final MP4.** No final MP4 has been rendered. The completion link points to `/demo/spherex-demo.mp4`, but that file does not exist yet. Render it or hide the link until available.
6. Record the revised UI, trim preflight/completion screens, mux the music-only bed and click events from the trace, and verify video duration/audio. `ffmpeg`/`ffprobe` are available. A normal recording has no browser audio in Playwright, so mux the local music and CC0 clicks afterward. No narration should be included.
7. Make the music source/generator reproducible from a fresh checkout; update README/audit documentation if needed. Run relevant frontend tests/build after fixes, then commit/push the final validated changes.

## Local run / tools

Work only inside `/Users/abnermoses/Downloads/SphereX/SphereX`. Preserve the uploaded dataset and theme. Do not expose `.env` or keys.

```sh
SPHEREX_OFFLINE=1 .venv/bin/python main.py
npm --prefix frontend run dev -- --host 127.0.0.1
# production check after build:
npm --prefix frontend run build
npm --prefix frontend run preview -- --host 127.0.0.1
```

Services started during this session: backend `8000`, dev UI `5173`, production preview `4173`; check availability before starting duplicates. Preview serves the most recent `frontend/dist` build. All original local data and five cached analysis bundles are available.

Playwright is installed only in `.audit/browser/node_modules/playwright`; browser binaries are in `.audit/browsers`:

```sh
PLAYWRIGHT_BROWSERS_PATH="$PWD/.audit/browsers" node .audit/check-presentation.cjs
```

Update that harness first. The computer-use browser host was unavailable earlier, so repository-local Playwright was used. The existing dataset validation reports remain in `audit-report.md`, `SCIENCE_DATASET_REPORT.md`, `CHALLENGE_ALIGNMENT.md` and `audit-evidence/`.

## Dataset committed after this handoff

The user subsequently requested the dataset itself be committed. All 153 original `SphereX-data/` files are now included; the 93 FITS files use Git LFS via `.gitattributes`. Install Git LFS and run `git lfs pull` after cloning. Source dataset bytes are unchanged. Generated `.audit` assets and secrets remain excluded. This dataset addition does not complete the pending presentation validation or MP4 render described above.
