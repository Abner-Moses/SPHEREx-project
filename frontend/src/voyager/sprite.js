// Voyager, Direction B ("slab probe", design/voyager-avatar.html): a 32×32 graphite slab with a gold-foil belt holding
// the visor, a gimballed dish and two solar wings. Amber key light from the top left, blue rim light on the right,
// dark outline. The eye carries the expression. Each expression is rendered once to a small canvas.

export const SIZE = 32
export const FACES = ['deadpan', 'unimpressed', 'surprised', 'pleased', 'side', 'blink']

const COL = { K: '#0b0d10', W: '#dfe3e6', w: '#8f989f', i: '#b5bcc2', A: '#7f888f', R: '#e0393b', G: '#73490f', H: '#c08226', Y: '#f2c25e', V: '#090b0f', E: '#f2b705', e: '#fff6cf', l: '#4a300b', d: '#7a5c08', S: '#3f474f', s: '#77818a', t: '#a9b2ba', z: '#22282e', c: '#173352', p: '#2f6aa8', B: '#4aa3ff' }

const EYES = {
  deadpan: (dx, dy) => (Math.abs(dx) <= 2 && (dy === -1 || dy === 0) ? (dx === -2 && dy === -1 ? 'e' : 'E') : null),
  unimpressed: (dx, dy) => (Math.abs(dx) <= 2 && dy === 0 ? 'E' : Math.abs(dx) <= 2 && dy === -1 ? 'l' : null),
  surprised: (dx, dy) => (Math.abs(dx) <= 1 && dy >= -2 && dy <= 1 ? (dx === -1 && dy === -2 ? 'e' : 'E') : null),
  pleased: (dx, dy) => ((dy === -1 && dx === 0) || (dy === 0 && Math.abs(dx) === 1) || (dy === 1 && Math.abs(dx) === 2) ? 'E' : null),
  side: (dx, dy) => (dx >= 1 && dx <= 4 && (dy === -1 || dy === 0) ? (dx === 1 && dy === -1 ? 'e' : 'E') : null),
  blink: (dx, dy) => (Math.abs(dx) <= 2 && dy === 0 ? 'd' : null),
}

const roundedRect = (x, y, cx, cy, hx, hy, r) => { const qx = Math.abs(x - cx) - hx + r; const qy = Math.abs(y - cy) - hy + r; return Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) + Math.min(Math.max(qx, qy), 0) - r }
const hash = (x, y) => (((x * 73856093) ^ (y * 19349663)) >>> 0) % 9
function lit(x, y, cx, cy, tones, foil) { const k = (x - cx) * 0.55 + (y - cy) * 0.8; let i = k < -3 ? 2 : k > 3.5 ? 0 : 1; if (foil) { const h = hash(x, y); if (h === 0) i = Math.min(2, i + 1); else if (h === 1) i = Math.max(0, i - 1) } return tones[i] }

function pixel(x, y, face) {
  if (Math.hypot(x - 26, y - 2) <= 1.1) return 'R'
  if (((x - 22) / 5.5) ** 2 + ((y - 4) / 2) ** 2 <= 1 && y <= 4) return ((x - 22) / 3.8) ** 2 + ((y - 3.6) / 1) ** 2 <= 1 ? 'i' : x < 22 ? 'W' : 'w'
  if (x === 20 && y >= 5 && y <= 6) return 'A'
  if (((x >= 2 && x <= 6) || (x >= 26 && x <= 30)) && y >= 14 && y <= 22) return x % 2 === 0 || y % 3 === 0 ? 'p' : 'c'
  if ((x === 7 || x === 8 || x === 24 || x === 25) && y === 18) return 'A'
  if (roundedRect(x, y, 16, 17.5, 7, 10.5, 2) <= 0) {
    if (y >= 15 && y <= 21) {
      if (x >= 11 && x <= 21 && y >= 16 && y <= 20) return EYES[face](x - 16, y - 18) || 'V'
      return lit(x, y, 16, 18, ['G', 'H', 'Y'], true)
    }
    if (y === 11 || y === 25) return 'z'
    if ((x === 11 || x === 21) && (y === 9 || y === 27)) return 'z'
    return lit(x, y, 16, 17.5, ['S', 's', 't'], false)
  }
  return null
}

const cache = {}
/** A 32×32 canvas of Voyager with the given expression. */
export function sprite(face = 'deadpan') {
  const f = EYES[face] ? face : 'deadpan'
  if (cache[f]) return cache[f]
  const g = []
  for (let y = 0; y < SIZE; y++) { g[y] = []; for (let x = 0; x < SIZE; x++) g[y][x] = pixel(x, y, f) }
  const out = g.map((r) => r.slice())
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const c = g[y][x]
      if (c && 'GHYSst'.includes(c) && !g[y][x + 1]) out[y][x] = 'B'
      if (!c && (g[y][x - 1] || g[y][x + 1] || g[y - 1]?.[x] || g[y + 1]?.[x])) out[y][x] = 'K'
    }
  }
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = SIZE
  const ctx = canvas.getContext('2d')
  out.forEach((row, y) => row.forEach((v, x) => { if (v) { ctx.fillStyle = COL[v]; ctx.fillRect(x, y, 1, 1) } }))
  cache[f] = canvas
  return canvas
}

// where things are on the sprite, in sprite pixels
export const ANCHOR = { boomLeft: [9, 18], boomRight: [23, 18], thrusters: [[13, 29], [19, 29]] }
