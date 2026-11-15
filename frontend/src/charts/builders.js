// Chart specs from the three things a capture can supply:
//   science(bundle)  -> SPHEREx measurements for one of the five demo targets (backend /api/demo)
//   stars(list)      -> every catalogued star in a (Shift) multi-star capture
//   image(png)       -> the captured frame's own pixels
// Each entry: { id, group, title, kind (what it is, for the legend), spec, note }. Nothing is synthesised: a chart is
// only produced when its inputs exist, and every exploratory input is labelled as such in `note`.

const BAND = [[0.75, 1.09, '#7c8cff'], [1.1, 1.62, '#4fd1e0'], [1.63, 2.41, '#8fe07a'], [2.42, 3.82, '#f4d35e'], [3.83, 4.41, '#ff9b42'], [4.42, 5.1, '#ff5a5f']]
export const bandColor = (um) => (BAND.find(([a, b]) => um >= a && um <= b) ?? [0, 0, '#c7cdd2'])[2]
const AMBER = '#f2b705', BLUE = '#4aa3ff', CREAM = '#e6dfcc', RED = '#e0393b'
const num = (v) => typeof v === 'number' && Number.isFinite(v)
const quantiles = (arr) => { const s = [...arr].sort((a, b) => a - b), q = (p) => s[Math.min(s.length - 1, Math.max(0, Math.round(p * (s.length - 1))))]; return { lo: s[0], q1: q(0.25), q2: q(0.5), q3: q(0.75), hi: s[s.length - 1], n: s.length } }

/** Generalised Lomb–Scargle periodogram (floating mean), periods in days. */
export function lombScargle(t, y, { minP = 0.5, maxP, n = 600 } = {}) {
  const N = t.length
  if (N < 4) return null
  const span = Math.max(...t) - Math.min(...t)
  maxP = maxP ?? Math.max(minP * 4, span * 1.5)
  const mean = y.reduce((a, b) => a + b, 0) / N
  const yc = y.map((v) => v - mean)
  const varY = yc.reduce((a, b) => a + b * b, 0) / (N - 1) || 1
  const periods = [], power = []
  for (let k = 0; k < n; k++) {
    const P = minP * (maxP / minP) ** (k / (n - 1)), w = (2 * Math.PI) / P
    let s2 = 0, c2 = 0
    for (const ti of t) { s2 += Math.sin(2 * w * ti); c2 += Math.cos(2 * w * ti) }
    const tau = Math.atan2(s2, c2) / (2 * w)
    let yc_c = 0, yc_s = 0, cc = 0, ss = 0
    for (let i = 0; i < N; i++) { const a = w * (t[i] - tau), c = Math.cos(a), s = Math.sin(a); yc_c += yc[i] * c; yc_s += yc[i] * s; cc += c * c; ss += s * s }
    periods.push(P); power.push((yc_c * yc_c / (cc || 1) + yc_s * yc_s / (ss || 1)) / (2 * varY))
  }
  let best = 0
  for (let i = 1; i < n; i++) if (power[i] > power[best]) best = i
  return { periods, power, best: periods[best], bestPower: power[best] }
}

const hist = (values, bins = 24, log = false) => {
  const v = values.filter((x) => num(x) && (!log || x > 0)).map((x) => (log ? Math.log10(x) : x))
  if (v.length < 2) return null
  const lo = Math.min(...v), hi = Math.max(...v), w = (hi - lo) / bins || 1
  const counts = new Array(bins).fill(0)
  for (const x of v) counts[Math.min(bins - 1, Math.floor((x - lo) / w))]++
  return { x: counts.map((_, i) => lo + (i + 0.5) * w), y: counts, w }
}

/** Marching squares: iso-lines of a grid, as segments in grid coordinates (x right, y down). */
export function contours(grid, levels) {
  const { w, h, values: V } = grid
  const segs = []
  for (const L of levels) {
    for (let j = 0; j < h - 1; j++) for (let i = 0; i < w - 1; i++) {
      const a = V[j * w + i], b = V[j * w + i + 1], c = V[(j + 1) * w + i + 1], d = V[(j + 1) * w + i]
      if (![a, b, c, d].every(num)) continue
      const e = []
      const cross = (p, q, x0, y0, x1, y1) => { if ((p < L) !== (q < L)) { const t = (L - p) / (q - p); e.push([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]) } }
      cross(a, b, i, j, i + 1, j); cross(b, c, i + 1, j, i + 1, j + 1); cross(d, c, i, j + 1, i + 1, j + 1); cross(a, d, i, j, i, j + 1)
      if (e.length >= 2) segs.push([...e[0], ...e[1]])
      if (e.length === 4) segs.push([...e[2], ...e[3]])
    }
  }
  return segs
}

const gridOf = (rows) => { const h = rows.length, w = rows[0]?.length ?? 0; return { w, h, values: rows.flat().map((v) => (num(v) ? v : NaN)) } }

// ------------------------------------------------------------------ SPHEREx science (one demo target)
export function science(b) {
  if (!b) return []
  const out = []
  const sci = (b.spectrophotometry?.points ?? []).map((p) => ({ ...p, quantity: 'aperture_flux_jy', science: true }))
  const exp = b.exploratory?.points ?? []
  const pts = sci.length ? sci : exp
  const isRing = pts.some((p) => p.quantity === 'ring_surface_brightness_mjy_sr')
  const yLabel = isRing ? 'Inner-ring surface brightness' : 'Aperture flux density'
  const yUnit = isRing ? 'MJy/sr' : 'Jy'
  const tag = sci.length ? 'Calibrated SPHEREx aperture spectrophotometry (science-accepted frames).' : `Exploratory: ${exp.length} frames failed SPHEREx scientific acceptance; values ${isRing ? 'are innermost unflagged ring brightness (saturated core)' : 'include lower limits where core pixels are flagged'}.`
  const wl = pts.filter((p) => num(p.wavelength_um) && num(p.flux_jy))
  if (wl.length) {
    out.push({ id: 'sed', group: 'Spectrum', title: 'Spectral energy distribution', kind: 'SED · log flux vs wavelength', note: tag, spec: { type: 'xy', x: { label: 'Wavelength', unit: 'µm' }, y: { label: yLabel, unit: yUnit, log: wl.every((p) => p.flux_jy > 0) },
      series: [{ type: 'errorbars', x: wl.map((p) => p.wavelength_um), y: wl.map((p) => p.flux_jy), yerr: wl.map((p) => p.flux_err_jy), xerr: wl.map((p) => (p.bandwidth_um ?? 0) / 2), lower: wl.map((p) => p.lower_limit), color: CREAM, alpha: 0.7 },
        { type: 'points', x: wl.map((p) => p.wavelength_um), y: wl.map((p) => p.flux_jy), colors: wl.map((p) => bandColor(p.wavelength_um)), size: 7, labels: wl.map((p) => `${p.wavelength_um.toFixed(3)} µm · ${p.flux_jy.toPrecision(3)} ${yUnit}${p.lower_limit ? ' (lower limit)' : ''} · ${p.observation_id ?? ''}`) }] } })
    out.push({ id: 'sed-lin', group: 'Spectrum', title: 'Spectrum (linear, Fν)', kind: 'Fν vs λ with band colours', note: tag, spec: { type: 'xy', x: { label: 'Wavelength', unit: 'µm' }, y: { label: yLabel, unit: yUnit },
      series: [{ type: 'line', x: wl.map((p) => p.wavelength_um), y: wl.map((p) => p.flux_jy), color: AMBER, alpha: 0.5, hover: false }, { type: 'points', x: wl.map((p) => p.wavelength_um), y: wl.map((p) => p.flux_jy), colors: wl.map((p) => bandColor(p.wavelength_um)), size: 6 }] } })
    if (!isRing) {
      const lf = wl.map((p) => (2.998e-12 * p.flux_jy) / p.wavelength_um) // λFλ in W/m² (Jy → λF_λ = c·Fν/λ)
      out.push({ id: 'lfl', group: 'Spectrum', title: 'λFλ (energy per log band)', kind: 'Energy output vs wavelength', note: `${tag} λFλ = c·Fν/λ.`, spec: { type: 'xy', x: { label: 'Wavelength', unit: 'µm', log: true }, y: { label: 'λFλ', unit: 'W m⁻²', log: true },
        series: [{ type: 'points', x: wl.map((p) => p.wavelength_um), y: lf, colors: wl.map((p) => bandColor(p.wavelength_um)), size: 7 }] } })
    }
  }
  if (b.cube_spectrum?.wavelength_um?.length) {
    const c = b.cube_spectrum
    // Keep blank channels in place so line/area rendering leaves a gap in the spectrum.
    const xs = c.wavelength_um
    const ys = xs.map((_, i) => num(c.surface_brightness?.[i]) ? c.surface_brightness[i] : null)
    out.push({ id: 'cube', group: 'Spectrum', title: 'Local cube spectrum (102 channels)', kind: 'DR1 spectral cube at the target', note: 'Local dataset · DR1 combined spectral cube, not a single epoch. No variance supplied.', spec: { type: 'xy', x: { label: 'Wavelength', unit: 'µm' }, y: { label: 'Surface brightness', unit: c.unit || 'MJy/sr' },
      series: [{ type: 'area', x: xs, y: ys, color: BLUE, fill: 0.18 }, { type: 'line', x: xs, y: ys, color: BLUE }, { type: 'points', x: xs, y: ys, colors: xs.map(bandColor), size: 4 }] } })
  }
  const tl = pts.filter((p) => num(p.mjd) && num(p.flux_jy)).sort((a, b) => a.mjd - b.mjd)
  if (tl.length >= 2) {
    out.push({ id: 'lc', group: 'Time', title: 'Light curve', kind: 'Flux vs time (colour = wavelength)', note: `${tag} Different frames sample different wavelengths, so changes are not established variability.`, spec: { type: 'xy', x: { label: 'MJD', fmt: (v) => v.toFixed(0) }, y: { label: yLabel, unit: yUnit },
      series: [{ type: 'errorbars', x: tl.map((p) => p.mjd), y: tl.map((p) => p.flux_jy), yerr: tl.map((p) => p.flux_err_jy), lower: tl.map((p) => p.lower_limit), color: CREAM, alpha: 0.6 },
        { type: 'points', x: tl.map((p) => p.mjd), y: tl.map((p) => p.flux_jy), colors: tl.map((p) => (num(p.wavelength_um) ? bandColor(p.wavelength_um) : CREAM)), size: 7, labels: tl.map((p) => `${p.timestamp?.slice(0, 16) ?? p.mjd.toFixed(3)} · ${p.flux_jy.toPrecision(3)} ${yUnit}${num(p.wavelength_um) ? ` · ${p.wavelength_um.toFixed(2)} µm` : ''}`) }] } })
    const wlT = tl.filter((p) => num(p.wavelength_um))
    if (wlT.length >= 2) out.push({ id: 'sampling', group: 'Time', title: 'Spectral–temporal sampling', kind: 'Which wavelength was observed when', note: 'Coverage of the measured frames.', spec: { type: 'xy', x: { label: 'MJD', fmt: (v) => v.toFixed(0) }, y: { label: 'Wavelength', unit: 'µm' }, series: [{ type: 'points', x: wlT.map((p) => p.mjd), y: wlT.map((p) => p.wavelength_um), colors: wlT.map((p) => bandColor(p.wavelength_um)), size: 8 }] } })
    const ls = tl.length >= 4 ? lombScargle(tl.map((p) => p.mjd), tl.map((p) => p.flux_jy), { minP: 0.2 }) : null
    if (ls) {
      out.push({ id: 'ls', group: 'Time', title: 'Lomb–Scargle periodogram', kind: 'Periodicity search', note: `Best period ${ls.best.toFixed(3)} d (power ${ls.bestPower.toFixed(2)}). With ${tl.length} uneven samples this is exploratory and aliasing is likely.`, spec: { type: 'xy', x: { label: 'Period', unit: 'days', log: true }, y: { label: 'Normalised power' },
        series: [{ type: 'area', x: ls.periods, y: ls.power, color: AMBER, fill: 0.15 }, { type: 'line', x: ls.periods, y: ls.power, color: AMBER, hover: false }], annotations: [{ x: ls.best, label: `${ls.best.toFixed(2)} d` }] } })
      const ph = tl.map((p) => ((p.mjd / ls.best) % 1 + 1) % 1)
      out.push({ id: 'phase', group: 'Time', title: 'Phase-folded curve', kind: `Folded at ${ls.best.toFixed(3)} d`, note: 'Folded at the periodogram peak; shown twice (phase 0–2).', spec: { type: 'xy', x: { label: 'Phase', min: 0, max: 2 }, y: { label: yLabel, unit: yUnit },
        series: [{ type: 'points', x: [...ph, ...ph.map((v) => v + 1)], y: [...tl, ...tl].map((p) => p.flux_jy), colors: [...tl, ...tl].map((p) => (num(p.wavelength_um) ? bandColor(p.wavelength_um) : CREAM)), size: 6 }] } })
    }
  }
  const rp = b.radial_profile?.visualization_descriptors?.[0]?.points
  if (rp?.length) out.push({ id: 'radial', group: 'Image', title: 'Radial intensity profile', kind: 'Surface brightness vs radius', note: 'Native SPHEREx pixels around the target; flagged pixels excluded.', spec: { type: 'xy', x: { label: 'Radius', unit: 'px' }, y: { label: 'Surface brightness', unit: 'MJy/sr', log: rp.every((p) => p.y > 0) },
    series: [{ type: 'errorbars', x: rp.map((p) => p.x), y: rp.map((p) => p.y), yerr: rp.map((p) => p.error), color: CREAM, alpha: 0.7 }, { type: 'line', x: rp.map((p) => p.x), y: rp.map((p) => p.y), color: AMBER }, { type: 'points', x: rp.map((p) => p.x), y: rp.map((p) => p.y), color: AMBER, size: 5, labels: rp.map((p) => `r ${p.x} px · ${p.y.toPrecision(3)} MJy/sr · ${p.pixels} px`) }] } })
  const ep = b.epsf?.visualization_descriptors
  const epHeat = ep?.find((d) => d.kind === 'heatmap')
  if (epHeat?.values?.length) {
    const g = gridOf(epHeat.values)
    out.push({ id: 'epsf', group: 'Image', title: 'Effective PSF (instrument response)', kind: 'ePSF heatmap, log scale', note: 'Stored SPHEREx ePSF for this detector zone (not the star itself).', spec: { type: 'heat', grid: g, x: { label: 'x', unit: 'oversampled px', min: 0, max: g.w }, y: { label: 'y', min: 0, max: g.h }, z: { label: 'response', log: true }, cmap: 'ember' } })
    out.push({ id: 'epsf3d', group: '3D', title: 'Effective PSF in 3D', kind: 'ePSF surface', note: 'Same ePSF as a height field.', spec: { type: 'surface', grid: g, z: { label: 'response' }, cmap: 'ember' } })
  }
  const epLine = ep?.find((d) => d.kind === 'line')
  if (epLine?.points?.length) out.push({ id: 'epsf-r', group: 'Image', title: 'ePSF radial profile', kind: 'Encircled response shape', note: 'Mean stored ePSF response by radius.', spec: { type: 'xy', x: { label: 'Radius', unit: epLine.x_unit || 'px' }, y: { label: 'Mean response', log: true }, series: [{ type: 'line', x: epLine.points.map((p) => p.x), y: epLine.points.map((p) => p.y), color: BLUE }] } })
  const sf = b.surface?.values
  if (sf?.length) {
    const g = gridOf(sf)
    out.push({ id: 'surf3d', group: '3D', title: 'Image surface (3D)', kind: 'Pixel brightness as height', note: b.surface.height_meaning || 'Calibrated surface brightness, binned.', spec: { type: 'surface', grid: g, z: { label: 'MJy/sr' }, cmap: 'ember' } })
    const finiteV = g.values.filter(num).sort((a, b) => a - b)
    const levels = [0.5, 0.75, 0.9, 0.97, 0.995].map((q) => finiteV[Math.floor(q * (finiteV.length - 1))])
    const segs = contours(g, levels)
    out.push({ id: 'contour', group: 'Image', title: 'Contour map', kind: 'Iso-brightness contours over the image', note: 'Contours at the 50/75/90/97/99.5 % brightness levels.', spec: { type: 'heat', grid: g, x: { label: 'x bin', min: 0, max: g.w - 1 }, y: { label: 'y bin', min: g.h - 1, max: 0 }, z: { label: 'MJy/sr', log: finiteV[0] > 0 }, cmap: 'glacier',
      overlay: [{ type: 'segments', x: segs.map((s) => s[0]), y: segs.map((s) => s[1]), x2: segs.map((s) => s[2]), y2: segs.map((s) => s[3]), color: AMBER, width: 1.2 }] } })
    out.push({ id: 'heat', group: 'Image', title: 'Image heatmap', kind: 'Calibrated pixels, log stretch', note: 'Binned calibrated image around the target.', spec: { type: 'heat', grid: g, x: { label: 'x bin', min: 0, max: g.w - 1 }, y: { label: 'y bin', min: g.h - 1, max: 0 }, z: { label: 'MJy/sr', log: finiteV[0] > 0 }, cmap: 'aurora' } })
  }
  const st = b.statistics?.histogram
  if (st?.counts?.length) {
    const x = st.counts.map((_, i) => (st.edges[i] + st.edges[i + 1]) / 2)
    out.push({ id: 'pixhist', group: 'Statistics', title: 'Pixel histogram', kind: 'Brightness distribution (log counts)', note: `${b.statistics.valid_pixels} valid pixels · median ${b.statistics.median?.toPrecision(3)} ${b.statistics.unit}`, spec: { type: 'xy', x: { label: 'Surface brightness', unit: b.statistics.unit }, y: { label: 'Pixels', log: true }, series: [{ type: 'bars', x, y: st.counts.map((c) => c || NaN), width: st.edges[1] - st.edges[0], color: BLUE }] } })
    let acc = 0
    const tot = st.counts.reduce((a, c) => a + c, 0) || 1
    out.push({ id: 'cdf', group: 'Statistics', title: 'Cumulative distribution', kind: 'Fraction of pixels below a brightness', note: 'From the pixel histogram.', spec: { type: 'xy', x: { label: 'Surface brightness', unit: b.statistics.unit }, y: { label: 'Fraction ≤ value', min: 0, max: 1 }, series: [{ type: 'step', x, y: st.counts.map((c) => (acc += c) / tot), color: AMBER }] } })
  }
  for (const [i, d] of (b.diagnostics?.visualization_descriptors ?? []).entries()) {
    if (d.kind !== 'histogram' || !d.data?.counts) continue
    const e = d.data.edges ?? d.data.counts.map((_, k) => k)
    const x = d.data.counts.map((_, k) => (num(e[k + 1]) ? (e[k] + e[k + 1]) / 2 : e[k]))
    out.push({ id: `diag${i}`, group: 'Statistics', title: d.title, kind: 'Native diagnostic histogram', note: 'From the SphereX2 native diagnostics on the measured frame.', spec: { type: 'xy', x: { label: d.title.split(' ')[0], unit: d.unit }, y: { label: 'Count', log: true }, series: [{ type: 'bars', x, y: d.data.counts.map((c) => c || NaN), width: num(e[1]) ? e[1] - e[0] : 1, color: i === 2 ? RED : CREAM, alpha: 0.85 }] } })
  }
  const fc = b.quality?.flag_counts
  if (fc) {
    const names = Object.keys(fc)
    out.push({ id: 'flags', group: 'Statistics', title: 'Quality flags', kind: 'Pixels per SPHEREx flag', note: `${b.quality.usable_pixels} usable · ${b.quality.excluded_pixels} excluded pixels.`, spec: { type: 'xy', x: { label: 'Flag', fmt: (v) => names[Math.round(v)]?.slice(0, 4) ?? '' }, y: { label: 'Pixels', log: true }, series: [{ type: 'bars', x: names.map((_, i) => i), y: names.map((n) => fc[n] || NaN), width: 0.8, color: RED, labels: names.map((n) => `${n}: ${fc[n]}`) }] } })
  }
  return out
}

// ------------------------------------------------------------------ catalogued stars in a multi-star capture
export function stars(list) {
  const s = (list ?? []).filter((o) => num(o.ra) && num(o.dec))
  if (s.length < 2) return []
  const out = []
  const V = (o) => (num(o.mag) ? o.mag : num(o.vmag) ? o.vmag : null)
  const BV = (o) => (num(o.bv) ? o.bv : num(o.bmag) && num(V(o)) ? o.bmag - V(o) : null)
  const plx = (o) => (num(o.plx) ? o.plx : num(o.parallax_mas) ? o.parallax_mas : num(o.dist) && o.dist > 0 ? 3261.56 / o.dist : null)
  const lab = (o) => `${o.name}${num(V(o)) ? ` · V ${V(o).toFixed(2)}` : ''}`
  const cmd = s.filter((o) => num(BV(o)) && num(V(o)))
  if (cmd.length >= 2) out.push({ id: 'cmd', group: 'Stars', title: 'Colour–magnitude diagram', kind: 'V vs B−V (apparent)', note: `${cmd.length} stars with B and V.`, spec: { type: 'xy', x: { label: 'B − V', unit: 'mag' }, y: { label: 'V', unit: 'mag', reverse: true }, series: [{ type: 'points', x: cmd.map(BV), y: cmd.map(V), colors: cmd.map((o) => bvHex(BV(o))), size: 6, labels: cmd.map(lab) }] } })
  const hr = cmd.filter((o) => num(plx(o)) && plx(o) > 0)
  if (hr.length >= 2) out.push({ id: 'hr', group: 'Stars', title: 'Hertzsprung–Russell diagram', kind: 'Absolute magnitude vs colour', note: 'M_V = V + 5 log₁₀(ϖ/100 mas); no extinction correction.', spec: { type: 'xy', x: { label: 'B − V', unit: 'mag' }, y: { label: 'M_V', unit: 'mag', reverse: true }, series: [{ type: 'points', x: hr.map(BV), y: hr.map((o) => V(o) + 5 * Math.log10(plx(o) / 100)), colors: hr.map((o) => bvHex(BV(o))), size: 6, labels: hr.map(lab) }] } })
  const cc = s.filter((o) => num(o.jmag) && num(o.hmag) && num(o.kmag))
  if (cc.length >= 2) out.push({ id: 'jhk', group: 'Stars', title: 'Near-IR colour–colour', kind: 'J−H vs H−K (2MASS bands)', note: 'Infrared excess (discs, dust) shows as points right of the main locus.', spec: { type: 'xy', x: { label: 'H − K', unit: 'mag' }, y: { label: 'J − H', unit: 'mag' }, series: [{ type: 'points', x: cc.map((o) => o.hmag - o.kmag), y: cc.map((o) => o.jmag - o.hmag), color: RED, size: 6, labels: cc.map(lab) }] } })
  const mags = s.map(V).filter(num)
  const h = hist(mags, Math.min(30, Math.max(6, Math.round(Math.sqrt(mags.length) * 1.5))))
  if (h) {
    out.push({ id: 'lf', group: 'Stars', title: 'Luminosity function', kind: 'Stars per magnitude bin', note: `${mags.length} stars.`, spec: { type: 'xy', x: { label: 'V', unit: 'mag' }, y: { label: 'Stars' }, series: [{ type: 'bars', x: h.x, y: h.y, width: h.w, color: AMBER }] } })
    const sorted = [...mags].sort((a, b) => a - b)
    out.push({ id: 'logn', group: 'Stars', title: 'Cumulative counts (log N–m)', kind: 'Number brighter than m', note: 'Slope ≈ 0.6 for a uniform, unbounded star field.', spec: { type: 'xy', x: { label: 'V', unit: 'mag' }, y: { label: 'N(< m)', log: true }, series: [{ type: 'step', x: sorted, y: sorted.map((_, i) => i + 1), color: BLUE }] } })
  }
  const pm = s.filter((o) => num(o.pmra) && num(o.pmdec))
  if (pm.length >= 2) {
    out.push({ id: 'vpd', group: 'Kinematics', title: 'Proper-motion vector-point diagram', kind: 'µα* vs µδ', note: 'Co-moving groups cluster together.', spec: { type: 'xy', x: { label: 'µα cos δ', unit: 'mas/yr' }, y: { label: 'µδ', unit: 'mas/yr' }, series: [{ type: 'points', x: pm.map((o) => o.pmra), y: pm.map((o) => o.pmdec), color: BLUE, size: 6, labels: pm.map((o) => `${o.name} · ${Math.hypot(o.pmra, o.pmdec).toFixed(1)} mas/yr`) }] } })
    const c0 = { ra: s.reduce((a, o) => a + o.ra, 0) / s.length, dec: s.reduce((a, o) => a + o.dec, 0) / s.length }
    const dx = (o) => ((((o.ra - c0.ra + 540) % 360) - 180) * Math.cos((c0.dec * Math.PI) / 180)) * 60, dy = (o) => (o.dec - c0.dec) * 60
    const tot = pm.map((o) => Math.hypot(o.pmra, o.pmdec)), scale = (Math.max(...s.map((o) => Math.hypot(dx(o), dy(o)))) * 0.25) / (Math.max(...tot) || 1)
    out.push({ id: 'quiver', group: 'Kinematics', title: 'Sky motion map', kind: 'Positions with proper-motion arrows', note: 'East is left (sky view). Arrow length ∝ proper motion.', spec: { type: 'xy', x: { label: 'ΔRA cos δ', unit: 'arcmin', reverse: true }, y: { label: 'ΔDec', unit: 'arcmin' }, series: [{ type: 'vectors', x: pm.map(dx), y: pm.map(dy), dx: pm.map((o) => o.pmra * scale), dy: pm.map((o) => o.pmdec * scale), color: AMBER }, { type: 'points', x: s.map(dx), y: s.map(dy), color: CREAM, sizes: s.map((o) => Math.max(3, 10 - (V(o) ?? 10) * 0.6)), labels: s.map(lab) }] } })
  }
  const pl = s.filter((o) => num(plx(o)) && plx(o) > 0)
  if (pl.length >= 2) {
    const d = pl.map((o) => 3261.56 / plx(o))
    const hd = hist(d, 20, true)
    if (hd) out.push({ id: 'dist', group: 'Stars', title: 'Distance distribution', kind: 'Stars per log distance', note: 'From parallaxes (1/ϖ, no prior).', spec: { type: 'xy', x: { label: 'log₁₀ distance', unit: 'ly' }, y: { label: 'Stars' }, series: [{ type: 'bars', x: hd.x, y: hd.y, width: hd.w, color: CREAM }] } })
    out.push({ id: 'plxmag', group: 'Stars', title: 'Parallax vs magnitude', kind: 'Nearby stars stand out top-left', note: '', spec: { type: 'xy', x: { label: 'V', unit: 'mag' }, y: { label: 'Parallax', unit: 'mas', log: true }, series: [{ type: 'points', x: pl.map(V), y: pl.map(plx), color: AMBER, size: 6, labels: pl.map(lab) }] } })
  }
  const bins = [['O/B', -1, 0], ['A', 0, 0.3], ['F', 0.3, 0.58], ['G', 0.58, 0.81], ['K', 0.81, 1.4], ['M', 1.4, 3]]
  const groups = bins.map(([label, a, b], i) => { const m = cmd.filter((o) => BV(o) >= a && BV(o) < b).map(V); return m.length >= 2 ? { x: i, label, ...quantiles(m) } : null }).filter(Boolean)
  if (groups.length >= 2) out.push({ id: 'box', group: 'Stars', title: 'Magnitude by colour class', kind: 'Box plot (quartiles, range)', note: 'Colour classes from B−V, not spectroscopic types.', spec: { type: 'xy', x: { label: 'Colour class', fmt: (v) => bins[Math.round(v)]?.[0] ?? '' }, y: { label: 'V', unit: 'mag', reverse: true }, series: [{ type: 'box', groups, color: BLUE }] } })
  if (s.length >= 6) {
    const c0 = { ra: s.reduce((a, o) => a + o.ra, 0) / s.length, dec: s.reduce((a, o) => a + o.dec, 0) / s.length }
    const r = s.map((o) => Math.hypot((((o.ra - c0.ra + 540) % 360) - 180) * Math.cos((c0.dec * Math.PI) / 180), o.dec - c0.dec) * 60)
    const rmax = Math.max(...r) || 1, n = 8, w = rmax / n
    const dens = Array.from({ length: n }, (_, i) => r.filter((v) => v >= i * w && v < (i + 1) * w).length / (Math.PI * ((i + 1) ** 2 - i ** 2) * w * w))
    out.push({ id: 'rdp', group: 'Stars', title: 'Radial density profile', kind: 'Stars per arcmin² vs radius', note: 'A falling profile suggests a cluster centred in the frame.', spec: { type: 'xy', x: { label: 'Radius', unit: 'arcmin' }, y: { label: 'Density', unit: 'arcmin⁻²' }, series: [{ type: 'line', x: dens.map((_, i) => (i + 0.5) * w), y: dens, color: AMBER }, { type: 'points', x: dens.map((_, i) => (i + 0.5) * w), y: dens, color: AMBER, size: 5 }] } })
    const nn = s.map((o, i) => Math.min(...s.map((q, j) => (i === j ? Infinity : Math.hypot((((o.ra - q.ra + 540) % 360) - 180) * Math.cos((o.dec * Math.PI) / 180), o.dec - q.dec) * 3600))))
    const hn = hist(nn, 16)
    if (hn) out.push({ id: 'nn', group: 'Stars', title: 'Nearest-neighbour separations', kind: 'Close pairs and clumping', note: 'Many small separations hint at binaries or clustering.', spec: { type: 'xy', x: { label: 'Separation', unit: 'arcsec' }, y: { label: 'Stars' }, series: [{ type: 'bars', x: hn.x, y: hn.y, width: hn.w, color: BLUE }] } })
  }
  return out
}
const bvHex = (bv) => (bv < 0 ? '#9db4ff' : bv < 0.3 ? '#cad7ff' : bv < 0.58 ? '#f8f7ff' : bv < 0.81 ? '#fff4ea' : bv < 1.4 ? '#ffd2a1' : '#ffb56c')

// ------------------------------------------------------------------ the captured frame's own pixels
export async function image(png) {
  if (!png) return []
  const img = new Image()
  img.src = png
  await img.decode()
  const N = 64
  const c = document.createElement('canvas')
  c.width = c.height = N
  const g = c.getContext('2d')
  g.drawImage(img, 0, 0, N, N)
  const d = g.getImageData(0, 0, N, N).data
  const lum = new Array(N * N), r = [], gr = [], b = []
  for (let i = 0; i < N * N; i++) { lum[i] = 0.2126 * d[i * 4] + 0.7152 * d[i * 4 + 1] + 0.0722 * d[i * 4 + 2]; r.push(d[i * 4]); gr.push(d[i * 4 + 1]); b.push(d[i * 4 + 2]) }
  const grid = { w: N, h: N, values: lum }
  const H = (arr) => { const counts = new Array(32).fill(0); for (const v of arr) counts[Math.min(31, v >> 3)]++; return counts }
  const xs = Array.from({ length: 32 }, (_, i) => i * 8 + 4)
  return [
    { id: 'img3d', group: '3D', title: 'Captured frame as a 3D surface', kind: 'Screen brightness as height', note: 'Rendered pixels of the capture (display units, not calibrated flux).', spec: { type: 'surface', grid, z: { label: 'brightness (0–255)' }, cmap: 'ember' } },
    { id: 'imgheat', group: 'Image', title: 'Captured frame heatmap', kind: 'Brightness, 64 × 64', note: 'Display units.', spec: { type: 'heat', grid, x: { label: 'x', min: 0, max: N - 1 }, y: { label: 'y', min: N - 1, max: 0 }, z: { label: 'brightness', min: 0, max: 255 }, cmap: 'gray' } },
    { id: 'imghist', group: 'Statistics', title: 'Colour histogram (RGB)', kind: 'Channel distributions', note: 'Display units of the captured frame.', spec: { type: 'xy', x: { label: 'Value', min: 0, max: 256 }, y: { label: 'Pixels', log: true }, legend: true, series: [{ type: 'line', x: xs, y: H(r).map((v) => v || NaN), color: RED, label: 'R' }, { type: 'line', x: xs, y: H(gr).map((v) => v || NaN), color: '#8fe07a', label: 'G' }, { type: 'line', x: xs, y: H(b).map((v) => v || NaN), color: BLUE, label: 'B' }] } },
  ]
}
