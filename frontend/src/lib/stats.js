// Pure image-analysis helpers over an 8-bit grayscale buffer (row 0 = top, as in the PNG).
// IMPORTANT: the backend percentile-stretches every detector into 0..255 before sending it, so
// everything computed here is in "display units", not physical flux. The UI says so.

export function histogram(gray) {
  const bins = new Uint32Array(256)
  for (let i = 0; i < gray.length; i++) bins[gray[i]]++
  return bins
}

export function summarize(bins) {
  let n = 0
  let sum = 0
  for (let v = 0; v < 256; v++) {
    n += bins[v]
    sum += v * bins[v]
  }
  if (n === 0) return { n: 0, mean: NaN, median: NaN, std: NaN, min: NaN, max: NaN }
  const mean = sum / n
  let varSum = 0
  let min = 255
  let max = 0
  let median = 0
  let acc = 0
  let medianFound = false
  for (let v = 0; v < 256; v++) {
    if (bins[v]) {
      min = Math.min(min, v)
      max = Math.max(max, v)
      varSum += bins[v] * (v - mean) ** 2
    }
    acc += bins[v]
    if (!medianFound && acc >= n / 2) {
      median = v
      medianFound = true
    }
  }
  return { n, mean, median, std: Math.sqrt(varSum / n), min, max }
}

/** Statistics inside a circular aperture centred on pixel centre (cx, cy). Pixel (x,y) centre is at (x+0.5, y+0.5). */
export function apertureStats(gray, width, height, cx, cy, radius) {
  const x0 = Math.max(0, Math.floor(cx - radius))
  const x1 = Math.min(width - 1, Math.ceil(cx + radius))
  const y0 = Math.max(0, Math.floor(cy - radius))
  const y1 = Math.min(height - 1, Math.ceil(cy + radius))
  const bins = new Uint32Array(256)
  let peak = -1
  let peakX = -1
  let peakY = -1
  const r2 = radius * radius
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const dx = x + 0.5 - cx
      const dy = y + 0.5 - cy
      if (dx * dx + dy * dy > r2) continue
      const v = gray[y * width + x]
      bins[v]++
      if (v > peak) {
        peak = v
        peakX = x
        peakY = y
      }
    }
  }
  const s = summarize(bins)
  return { ...s, sum: s.mean * s.n, peakX, peakY }
}

/** Mean brightness in concentric annuli around (cx, cy). Returns [{ r, mean, n }] with r = annulus mid-radius in pixels. */
export function radialProfile(gray, width, height, cx, cy, maxRadius, step = 1) {
  const count = Math.max(1, Math.floor(maxRadius / step))
  const sums = new Float64Array(count)
  const ns = new Uint32Array(count)
  const x0 = Math.max(0, Math.floor(cx - maxRadius))
  const x1 = Math.min(width - 1, Math.ceil(cx + maxRadius))
  const y0 = Math.max(0, Math.floor(cy - maxRadius))
  const y1 = Math.min(height - 1, Math.ceil(cy + maxRadius))
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) {
      const r = Math.hypot(x + 0.5 - cx, y + 0.5 - cy)
      const bin = Math.floor(r / step)
      if (bin >= count) continue
      sums[bin] += gray[y * width + x]
      ns[bin]++
    }
  }
  const out = []
  for (let i = 0; i < count; i++) if (ns[i]) out.push({ r: (i + 0.5) * step, mean: sums[i] / ns[i], n: ns[i] })
  return out
}

function bilinear(gray, width, height, x, y) {
  const fx = Math.min(width - 1, Math.max(0, x - 0.5))
  const fy = Math.min(height - 1, Math.max(0, y - 0.5))
  const ix = Math.floor(fx)
  const iy = Math.floor(fy)
  const jx = Math.min(width - 1, ix + 1)
  const jy = Math.min(height - 1, iy + 1)
  const tx = fx - ix
  const ty = fy - iy
  const top = gray[iy * width + ix] * (1 - tx) + gray[iy * width + jx] * tx
  const bottom = gray[jy * width + ix] * (1 - tx) + gray[jy * width + jx] * tx
  return top * (1 - ty) + bottom * ty
}

/** Brightness sampled along a line in image-pixel coordinates. Returns [{ d, v }] with d = distance in pixels. */
export function lineProfile(gray, width, height, x0, y0, x1, y1, samples) {
  const length = Math.hypot(x1 - x0, y1 - y0)
  const n = Math.max(2, samples ?? Math.ceil(length) + 1)
  const out = []
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    out.push({ d: t * length, v: bilinear(gray, width, height, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) })
  }
  return out
}

/** Block-average a rectangle of the image down to outW x outH, normalised to 0..1. */
export function downsample(gray, width, height, rect, outW, outH) {
  const rx = Math.max(0, Math.floor(rect.x))
  const ry = Math.max(0, Math.floor(rect.y))
  const rw = Math.min(width - rx, Math.floor(rect.w))
  const rh = Math.min(height - ry, Math.floor(rect.h))
  const out = new Float32Array(outW * outH)
  for (let j = 0; j < outH; j++) {
    const ya = ry + Math.floor((j * rh) / outH)
    const yb = Math.max(ya + 1, ry + Math.floor(((j + 1) * rh) / outH))
    for (let i = 0; i < outW; i++) {
      const xa = rx + Math.floor((i * rw) / outW)
      const xb = Math.max(xa + 1, rx + Math.floor(((i + 1) * rw) / outW))
      let sum = 0
      let n = 0
      for (let y = ya; y < yb; y++) for (let x = xa; x < xb; x++) {
        sum += gray[y * width + x]
        n++
      }
      out[j * outW + i] = sum / n / 255
    }
  }
  return out
}
