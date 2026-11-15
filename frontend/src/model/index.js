// PLACEHOLDER analysis layer for the planned 6M-parameter model (to be swapped in when trained).
//
// Contract the real model must satisfy: each task is `run(task, input) -> Promise<Result>` where
//   Result = { task, placeholder: boolean, summary: string, items: Item[], notes?: string[], ... }
// and the UI renders whatever comes back. Everything marked `placeholder: true` below is a stand-in:
// either a classical algorithm, a physical model, or hand-prepared content for the Barnard's Star
// preview. Nothing here is a trained-model output, and the UI says so on every result.

import { DEG } from '../lib/astro.js'
import { skyToImage, imageToSky } from '../lib/mesh.js'
import { propagate, yearsSinceJ2000, mjdToMs } from '../lib/skyview.js'
import * as api from '../api.js'

export const TASKS = [
  { id: 'moving', label: 'Moving objects', blurb: 'What shifts between visits', icon: 'move' },
  { id: 'anomaly', label: 'Anomalies', blurb: 'Surface what looks unusual', icon: 'alert' },
  { id: 'noise', label: 'Noise & artifacts', blurb: 'Streaks, hot pixels, saturation', icon: 'noise' },
  { id: 'spectrum', label: 'Spectrum', blurb: 'Infrared colours of a source', icon: 'spectrum' },
  { id: 'detect', label: 'Detect & track', blurb: 'Find and follow sources', icon: 'detect' },
]

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const pause = async (onProgress, ms = 900) => { for (let i = 1; i <= 10; i++) { await sleep(ms / 10); onProgress?.(i / 10) } }

// ------------------------------------------------------------------ model cache (Cache Storage)
const MODEL_CACHE = 'spherex-model-v1'
// When the trained model exists, point this at it; install() will stream it into the cache with real progress.
const MODEL_URL = null
const MANIFEST = '/model/manifest.json'

export async function modelStatus() {
  if (!('caches' in window)) return { supported: false, installed: false, bytes: 0 }
  const cache = await caches.open(MODEL_CACHE)
  const hit = await cache.match(MANIFEST)
  if (!hit) return { supported: true, installed: false, bytes: 0 }
  const info = await hit.clone().json()
  return { supported: true, installed: true, bytes: info.bytes, version: info.version, placeholder: info.placeholder }
}

export async function installModel(onProgress) {
  const cache = await caches.open(MODEL_CACHE)
  if (MODEL_URL) { // real path, ready for the trained weights
    const res = await fetch(MODEL_URL)
    const total = Number(res.headers.get('Content-Length')) || 0
    const reader = res.body.getReader(); const chunks = []; let got = 0
    for (;;) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); got += value.length; onProgress?.(total ? got / total : 0.5) }
    await cache.put(MODEL_URL, new Response(new Blob(chunks)))
    await cache.put(MANIFEST, new Response(JSON.stringify({ version: 'trained', bytes: got, placeholder: false })))
    return
  }
  await pause(onProgress, 1400) // placeholder install: stores a tiny manifest so the cache flow is real
  await cache.put(MANIFEST, new Response(JSON.stringify({ version: 'placeholder-0', bytes: 2048, placeholder: true }), { headers: { 'Content-Type': 'application/json' } }))
}
export const removeModel = () => caches.delete(MODEL_CACHE)

// ------------------------------------------------------------------ tasks
/** Robust background noise from the 8-bit image: 1.4826 * MAD of pixel values (display units). */
function robustSigma(gray) {
  const bins = new Uint32Array(256)
  for (let i = 0; i < gray.length; i += 7) bins[gray[i]]++
  const total = bins.reduce((a, b) => a + b, 0)
  const med = (() => { let a = 0; for (let v = 0; v < 256; v++) { a += bins[v]; if (a >= total / 2) return v } return 0 })()
  const dev = new Uint32Array(256); for (let v = 0; v < 256; v++) dev[Math.abs(v - med)] += bins[v]
  let a = 0; let mad = 0; for (let v = 0; v < 256; v++) { a += dev[v]; if (a >= total / 2) { mad = v; break } }
  return { median: med, sigma: 1.4826 * mad }
}

/** Classical peak finder (a stand-in for the model's detector): local maxima above background + k sigma. */
function findPeaks(img, k = 6, maxN = 60) {
  const { gray, width: W, height: H } = img
  const { median, sigma } = robustSigma(gray)
  const thr = Math.min(250, median + Math.max(k * sigma, 12))
  const candidates = []
  for (let y = 3; y < H - 3; y++) for (let x = 3; x < W - 3; x++) {
    const v = gray[y * W + x]
    if (v < thr) continue
    let ok = true
    for (let dy = -2; dy <= 2 && ok; dy++) for (let dx = -2; dx <= 2; dx++) { if ((dx || dy) && gray[(y + dy) * W + x + dx] > v) { ok = false; break } }
    if (ok) candidates.push({ x: x + 0.5, y: y + 0.5, peak: v })
  }
  // Bright stars clip at 255 and form flat plateaus, so equal neighbours all pass the test above.
  // Non-maximum suppression keeps one detection per source: the brightest, and the first in raster order among ties.
  const sep = 7
  const peaks = []
  for (const c of candidates.sort((a, b) => b.peak - a.peak).slice(0, 6000)) {
    if (peaks.length >= maxN) break
    if (peaks.every((p) => Math.hypot(p.x - c.x, p.y - c.y) > sep)) peaks.push(c)
  }
  return { peaks, median, sigma, threshold: thr }
}

function blackbodyJy(lamUm, T, rSun, dPc) {
  const h = 6.62607e-34, c = 2.99792e8, k = 1.380649e-23
  const nu = c / (lamUm * 1e-6)
  const B = (2 * h * nu ** 3) / c ** 2 / Math.expm1((h * nu) / (k * T))
  return (Math.PI * B * ((rSun * 6.957e8) / (dPc * 3.0857e16)) ** 2) / 1e-26
}

export async function runTask(task, ctx, onProgress) {
  const { selected, product, epochMs } = ctx
  await pause(onProgress, task === 'detect' ? 500 : 900)
  const isBarnard = selected?.name === "Barnard's Star"
  switch (task) {
    case 'moving': {
      if (!selected || selected.pmra == null) return { task, placeholder: true, summary: 'Select a catalogue star with a known proper motion.', items: [] }
      const yrs = [0, yearsSinceJ2000(product?.mjd_avg ? mjdToMs(product.mjd_avg) : epochMs), yearsSinceJ2000(epochMs) + 5]
      const labels = ['J2000.0', product?.mjd_avg ? 'This observation' : 'Epoch shown', '+5 yr from epoch']
      const items = yrs.map((y, i) => { const p = propagate(selected.ra, selected.dec, selected.pmra, selected.pmdec, y); return { label: labels[i], ra: p.lon / DEG, dec: p.lat / DEG, years: y } })
      const mas = Math.hypot(selected.pmra, selected.pmdec)
      const pa = ((Math.atan2(selected.pmra, selected.pmdec) * 180) / Math.PI + 360) % 360
      return { task, placeholder: true, summary: `${selected.name} is moving at ${(mas / 1000).toFixed(2)}″ per year toward position angle ${pa.toFixed(1)}°. The model's detector will find movers without a catalogue; this track comes from the catalogue's proper motion.`, items, track: items, stats: { 'Proper motion': `${(mas / 1000).toFixed(3)} ″/yr`, 'Direction (PA)': `${pa.toFixed(1)}°`, 'Moved since J2000': `${((mas / 1000) * yrs[1]).toFixed(1)}″` } }
    }
    case 'anomaly': {
      if (!isBarnard || !product) return { task, placeholder: true, summary: 'The anomaly model is not trained yet. The Barnard\'s Star preview shows what a result will look like.', items: [] }
      return { task, placeholder: true, summary: 'Placeholder regions, hand-placed for the Barnard\'s Star preview image. The trained model will score every part of the frame.', items: [
        { id: 'a1', label: 'Thin linear streak (possible satellite or cosmic-ray trail)', score: 0.83, box: { x: 640, y: 0, w: 190, h: 560 } },
        { id: 'a2', label: 'Very bright saturated source', score: 0.74, box: { x: 610, y: 1440, w: 80, h: 80 } },
      ] }
    }
    case 'noise': {
      if (!product) return { task, placeholder: true, summary: 'Load an image first.', items: [] }
      const n = robustSigma(product.image?.gray ?? new Uint8Array(0))
      return { task, placeholder: true, summary: 'Real numbers from the file\'s own quality flags, plus a simple noise estimate. The trained model will additionally classify artifact types the flags miss.', stats: { 'Flagged pixels': `${(product.bad_fraction * 100).toFixed(2)} %`, 'Background level': `${n.median} / 255`, 'Noise (robust σ)': `${n.sigma.toFixed(1)} display units` }, items: [], mask: true }
    }
    case 'spectrum': {
      if (!selected) return { task, placeholder: true, summary: 'Select a source first.', items: [] }
      const curve = []
      for (let l = 0.75; l <= 5.01; l += 0.05) curve.push({ x: l, y: blackbodyJy(l, 3238, 0.187, 1.8266) })
      let measured = null
      if (isBarnard && product?.meshP && product.source === 'irsa') {
        const p = propagate(selected.ra, selected.dec, selected.pmra, selected.pmdec, yearsSinceJ2000(mjdToMs(product.mjd_avg)))
        const px = skyToImage(product.meshP, p.lon / DEG, p.lat / DEG)
        if (px) { try { const ph = await api.photometry(product.id, px.x, px.y, 4); measured = { x: ph.wavelength_um, y: ph.flux_jy, err: ph.flux_err_jy, lowerLimit: ph.lower_limit, masked: ph.masked_fraction } } catch { /* keep model only */ } }
      }
      return { task, placeholder: true, summary: isBarnard ? 'A blackbody model (T ≈ 3200 K, R ≈ 0.19 R☉, 5.96 ly) stands in for the spectral model, with the real SPHEREx measurement from the loaded image overlaid.' : 'The spectral model is not trained yet.', curve, measured, items: [] }
    }
    case 'detect': {
      if (!product?.image) return { task, placeholder: true, summary: 'Load an image first.', items: [] }
      const r = findPeaks(product.image)
      const items = r.peaks.map((p, i) => ({ id: `s${i}`, ...p, sky: imageToSky(product.meshP, p.x, p.y) }))
      return { task, placeholder: true, summary: `A classical peak finder (not the model) found ${items.length} sources above ${r.threshold.toFixed(0)}/255. The trained detector will also classify and track them across observations.`, items }
    }
    default: return { task, placeholder: true, summary: 'Unknown task.', items: [] }
  }
}
