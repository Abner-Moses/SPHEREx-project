// Los Palomos easter eggs: one pixel pigeon shared by the menu credit, Voyager's visitor and the capture tool.
// Every egg is rare on purpose; none of them changes how the app works.

// Side view, facing left. k head, a beak, o eye, n iridescent neck, b body, w wing, t tail, f feet
export const PIGEON = {
  perched: ['.kk......', 'aok......', '.knbbb...', '..bbbwbtt', '...bbbbt.', '....f.f..'],
  bob: ['.........', '.kk......', 'aoknbb...', '..bbbwbtt', '...bbbbt.', '....f.f..'],
  up: ['....ww...', '.kk.ww...', 'aoknwbb..', '..bbbbbtt', '...bbbt..', '.........'],
  down: ['.........', '.kk......', 'aoknbbb..', '..bbwwbtt', '...www...', '....w....'],
}
export const PIGEON_COLORS = { k: '#4b545c', a: '#c9b98f', o: '#f2b705', n: '#3fa38f', b: '#9aa3ab', w: '#c7cdd2', t: '#5d666e', f: '#e0393b' }

/** Draw a pigeon frame into a 2D context, top-left at (x, y), one sprite pixel = s screen pixels. */
export function drawPigeon(g, frame, x, y, s = 1, flip = false) {
  const rows = PIGEON[frame]
  const w = rows[0].length
  rows.forEach((row, r) => [...row].forEach((ch, c) => {
    const col = PIGEON_COLORS[ch]
    if (!col) return
    g.fillStyle = col
    g.fillRect(Math.round(x + (flip ? w - 1 - c : c) * s), Math.round(y + r * s), s, s)
  }))
}

/** The same frame as a tiny inline SVG string (for the menu credit). */
export function pigeonSvg(frame = 'perched', s = 3) {
  const rows = PIGEON[frame]
  let rects = ''
  rows.forEach((row, r) => [...row].forEach((ch, c) => { if (PIGEON_COLORS[ch]) rects += `<rect x="${c}" y="${r}" width="1" height="1" fill="${PIGEON_COLORS[ch]}"/>` }))
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${rows[0].length} ${rows.length}" width="${rows[0].length * s}" height="${rows.length * s}" shape-rendering="crispEdges" aria-hidden="true">${rects}</svg>`
}

// Columba, "the Dove" (paloma in Spanish): the constellation south of Orion, centred near Phact (alpha Columbae)
export const COLUMBA = { name: 'Columba', ra: 87.5, dec: -35.5, point: true, egg: 'columba' }
export const COLUMBA_NOTE = 'Columba, the Dove. Paloma, if you ask a Spanish speaker. Brightest star: Phact, about 260 light-years away. Named in the 1590s; the pigeons were not consulted.'
export const isPigeonQuery = (t) => /^\s*(los\s+)?palom(o|os|a|as)\s*$|^\s*(pigeons?|doves?|columba|coo)\s*$/i.test(t)

/** Is (ra, dec) in degrees roughly inside Columba? (its official box: RA 5h04m–6h40m, Dec -27°..-43°) */
export const inColumba = (ra, dec) => ra >= 75.9 && ra <= 99.9 && dec >= -43.2 && dec <= -27.1

/** Roll for a rare egg. */
export const rare = (p) => Math.random() < p
