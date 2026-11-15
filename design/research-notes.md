# Design research notes (2026-11-15)

What creative, non-Apple sites and brands near our space do, and what we take from each.

## Bungie — *Marathon* (2025–26), "graphic realism"
- Art director Joseph Cross: a simplified, deconstructed design language, **strong graphic and colour statements, limited materials**. Influences: Mirror's Edge, Ghost in the Shell, Aeon Flux, The Designers Republic (Wipeout).
- "Graphic retro futurism": retro roots, oddball cool, modern tech, **digital dithering**. Its dense typographic "sauce" split fans, so it's loved and hated in equal measure.
- **We take:** only two materials (gunmetal and charcoal), colour used as a statement not a decoration, dithering for skies and transitions, and a little typographic sauce (tags, arrows, codes) kept around the edges so it never covers content.
- Sources: [Creative Bloq interview](https://www.creativebloq.com/3d/video-game-design/bungies-art-director-explains-marathons-controversial-art-style), [Space.com interview](https://www.space.com/entertainment/space-games/bungie-explains-marathons-graphic-retro-futurism-aesthetic-and-the-live-narrative-lessons-it-learned-from-destiny-interview), [80.lv influences](https://80.lv/articles/marathon-art-director-shares-the-team-s-creative-influences), [PC Gamer on the UI](https://www.pcgamer.com/games/fps/marathon-ui-designer-declares-himself-the-fontslop-merchant-and-promises-bungie-will-never-remove-the-sauce-from-the-ui), [Fonts In Use](https://fontsinuse.com/uses/67879/marathon-2026-video-game-1)

## Teenage Engineering
- **Monospace type everywhere**, zero corner radius like an instrument panel, a monochrome palette where the brand colour is rare, and **the product is the hero**. Depth and texture without pretending to be analogue (no fake skeuomorphism).
- **We take:** mono as the primary voice, the sky as the hero (UI chrome recedes), yellow kept rare, and texture that's honest. Brushed metal from Slab/Kit gets toned down to a faint grain.
- Sources: [Blake Crosley guide (DE)](https://blakecrosley.com/de/guides/design/teenage-engineering), [design system summary](https://www.shadcn.io/design/teenage-engineering/raw), [Lapa Ninja](https://lapa.ninja/post/teenage-engineering-3), [Sessions notes](https://sessions.edu/notes-on-design/super-systems-teenage-engineering)

## Nothing
- Dot-matrix type (Ndot) for **identity moments only**, such as product names. A sans handles editorial text and a mono handles labels. Nothing OS 5 deliberately pulled dot-matrix back to where it helps "character, not readability".
- **We take:** a dot-matrix display face (Doto, open source) only for object names, the clock and big numbers. Never for paragraphs.
- Sources: [Nothing design system summary](https://www.shadcn.io/design/nothing), [Made Good Designs](https://madegooddesigns.com/nothing-brand-guidelines/), [Nothing OS evolution](https://nothing.community/d/61774-nothing-os-evolution), [Design Milk on the Glyph Matrix](https://design-milk.com/the-nothing-phone-3s-glyph-matrix-turns-notifications-into-pixel-art/)

## Awwwards winners (KPR 2022, Igloo Inc 2024, Lusion) and terminal-style entries
- Judges praised the "mixture of minimalism, futurism and unique animation" (KPR) and "like your favourite video game transformed into a website" (Igloo). Terminal-style sites (e.g. Avinyr) use a **boot sequence** as the signature moment.
- **We take:** mostly calm and minimal, with a few unforgettable animated moments. One boot sequence, shown on first visit only and skippable.
- Sources: [Awwwards SOTY 2022](https://www.awwwards.com/annual-awards-2022/site-of-the-year), [Awwwards 2024](https://awwwards.com/annual-awards-2024), [Avinyr](https://www.awwwards.com/sites/avinyr)

## NASA — Eyes on the Solar System
- A web 3D explorer with WASD/QE(ZC) controls "instantly familiar to PC gamers" and colour-coded highlights for current positions.
- **We take:** confirmation that our WASD/QE 3D controls are the right call. Colour-code what's live (blue = selectable or live, yellow = Voyager's focus, red = recording or alert).
- Sources: [NASA/JPL announcement](https://www.jpl.nasa.gov/news/explore-the-solar-system-with-nasas-new-and-improved-3d-eyes/), [80.lv](https://80.lv/articles/nasa-s-eyes-on-the-solar-system-vizualization-tool-gets-a-major-update), [Lifeboat write-up](https://lifeboat.com/blog/2022/09/nasas-revamped-eyes-on-the-solar-system-lets-you-explore-space-in-your-browser)

## The mix: our rules (the "music" brief)
| Layer | Share | What it is here |
|---|---|---|
| Normal | ~70% | Charcoal panels, clean Plex Sans text, generous spacing, readable data. The sky is the hero. |
| Crazy | ~25% | Terminal headers and command line, ASCII frame corners, the live console log, tick-mark rulers, dither, dot-matrix names. |
| Very crazy | ~5% | Three moments only: the first-visit boot sequence, the "uplink" dither wipe when an image loads, and Voyager's yellow signal beam when it reaches a target. |

**Tempo:** all motion sits on a 120 BPM grid: 1/16 = 125 ms (hovers), 1/8 = 250 ms (panels), 1/4 = 500 ms (moves), 1 bar = 2 s (very-crazy moments only). Nothing loops while idle.

**Colour roles:** charcoal and gunmetal are the materials. Yellow is the signal: Voyager, the primary action and the current focus (rare). Red means record, alert or live (rarer). Blue is the cold glint: links, focus rings, the selectable state, grid ticks (the "hint where possible"). Detector-band colours survive only as the small spectrum strip.

Avoid: copying any brand's logo, name or exact layout (no bat symbol, no Ndot, no Marathon assets).
