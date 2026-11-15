// Stars with a trustworthy parallax, and their real distances. Shared by the 3D depth view and the panel beside it.
import { prettyStarName } from './astro.js'

export const MIN_PLX_MAS = 5 // bright-star parallaxes get unreliable beyond ~650 ly
export const LY_PER_PARSEC = 3261.56

export function nearestStars(stars, count = 8) {
  return stars
    .filter((s) => s.plx && s.plx >= MIN_PLX_MAS)
    .map((s) => ({ name: prettyStarName(s.name), ly: LY_PER_PARSEC / s.plx, v: s.v }))
    .sort((a, b) => a.ly - b.ly)
    .slice(0, count)
}
