// Colormaps are piecewise-linear gradients we define ourselves (not copies of any named
// scientific colormap). Each becomes a 256-entry RGBA lookup table for the GPU.

const STOPS = {
  gray: ['#000000', '#ffffff'],
  ember: ['#050208', '#2a0b3a', '#7a1c4b', '#d6402f', '#ff9a3c', '#ffe8a8', '#ffffff'],
  glacier: ['#020611', '#0a2a5c', '#127aa8', '#58d0d6', '#c8f5ee', '#ffffff'],
  aurora: ['#0b0233', '#33217a', '#1f6fa8', '#18b58f', '#a6e22e', '#fff3a0'],
}

export const COLORMAPS = [
  { id: 'gray', label: 'Grayscale' },
  { id: 'ember', label: 'Ember' },
  { id: 'glacier', label: 'Glacier' },
  { id: 'aurora', label: 'Aurora' },
]

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))

export function buildLut(id) {
  const stops = (STOPS[id] ?? STOPS.gray).map(hex)
  const lut = new Uint8Array(256 * 4)
  for (let i = 0; i < 256; i++) {
    const t = (i / 255) * (stops.length - 1)
    const a = Math.min(stops.length - 2, Math.floor(t))
    const f = t - a
    for (let c = 0; c < 3; c++) lut[i * 4 + c] = Math.round(stops[a][c] * (1 - f) + stops[a + 1][c] * f)
    lut[i * 4 + 3] = 255
  }
  return lut
}

export const cssGradient = (id) =>
  `linear-gradient(90deg, ${(STOPS[id] ?? STOPS.gray).join(', ')})`
