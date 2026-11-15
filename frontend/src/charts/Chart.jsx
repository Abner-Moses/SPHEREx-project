import { useEffect, useRef, useState } from 'react'
import { frame, rgba } from './gl.js'

// Spec shapes (built in builders.js):
//  { type:'xy', x:{label,log,reverse,min,max}, y:{...}, series:[{type, x:[], y:[], ...}], legend }
//     series types: points (size, colors[]), line, step, area, bars (width), errorbars (yerr, xerr, lower[]),
//                   vectors (dx, dy), segments (x2, y2), box (groups:[{x,q1,q2,q3,lo,hi}]), band (y2)
//  { type:'heat', grid:{w,h,values}, x:{label,min,max}, y:{label,min,max}, z:{label,log}, cmap, overlay:[series] }
//  { type:'surface', grid:{w,h,values}, z:{label}, cmap }
const PAD = { l: 50, r: 14, t: 10, b: 34 }
const FONT = '10px "IBM Plex Mono", ui-monospace, monospace'
const AXIS = 'rgba(155,148,132,0.9)'
const GRID = [0.6, 0.65, 0.7, 0.08]

const finite = (v) => typeof v === 'number' && Number.isFinite(v)
function extent(vals, log) {
  let lo = Infinity, hi = -Infinity
  for (const v of vals) if (finite(v) && (!log || v > 0)) { if (v < lo) lo = v; if (v > hi) hi = v }
  if (!finite(lo)) return log ? [1, 10] : [0, 1]
  if (lo === hi) { const d = log ? lo * 0.5 : Math.abs(lo) * 0.1 || 1; return [lo - (log ? lo * 0.5 : d), hi + d] }
  if (log) return [lo / 1.3, hi * 1.3]
  const pad = (hi - lo) * 0.06
  return [lo - pad, hi + pad]
}
function ticks(lo, hi, log, n = 5) {
  if (log) { const out = []; for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) { const v = 10 ** e; if (v >= lo * 0.999 && v <= hi * 1.001) out.push(v) } return out.length >= 2 ? out : [lo, hi] }
  const step0 = (hi - lo) / n, p = 10 ** Math.floor(Math.log10(step0)), m = step0 / p
  const step = (m >= 5 ? 10 : m >= 2 ? 5 : m >= 1 ? 2 : 1) * p
  const out = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v)
  return out
}
export const fmt = (v) => { if (!finite(v)) return '—'; const a = Math.abs(v); if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(1); return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(a >= 1 ? 2 : 3) }

function axisMap(ax, lo, hi, a, b) {
  const L = ax.log ? Math.log10 : (v) => v
  const l0 = L(lo), l1 = L(hi)
  const f = (v) => a + ((L(v) - l0) / (l1 - l0 || 1)) * (b - a)
  return ax.reverse ? (v) => a + b - f(v) : f
}

/** Draw a spec into a canvas; returns the hover index (screen points with labels). */
function render(canvas, spec, w, h, dpr, view) {
  const P = frame(w, h, dpr)
  const hover = []
  const text = []
  if (spec.type === 'surface') {
    const vals = spec.grid.values.filter(finite)
    const min = Math.min(...vals), max = Math.max(...vals)
    P.surface(spec.grid, { min, max, yaw: view.yaw, pitch: view.pitch, cmap: spec.cmap || 'ember', zoom: view.zoom })
    P.blit(canvas)
    const g = canvas.getContext('2d')
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.font = FONT; g.fillStyle = AXIS
    g.fillText(`${spec.z?.label ?? 'value'}: ${fmt(min)} – ${fmt(max)}${spec.z?.unit ? ' ' + spec.z.unit : ''}`, 8, h - 10)
    g.fillText('drag to rotate · wheel to zoom', w - 170, h - 10)
    return hover
  }
  const plot = [PAD.l, PAD.t, w - PAD.r, h - PAD.b]
  let xs, ys
  if (spec.type === 'heat') { xs = [spec.x.min, spec.x.max]; ys = [spec.y.min, spec.y.max] } else {
    const allX = [], allY = []
    for (const s of spec.series) {
      if (s.type === 'box') { for (const g of s.groups) { allX.push(g.x - 0.6, g.x + 0.6); allY.push(g.lo, g.hi) } continue }
      for (let i = 0; i < s.x.length; i++) {
        allX.push(s.x[i]); allY.push(s.y[i])
        if (s.yerr) { allY.push(s.y[i] - (s.yerr[i] || 0), s.y[i] + (s.yerr[i] || 0)) }
        if (s.x2) { allX.push(s.x2[i]); allY.push(s.y2[i]) }
        if (s.dx) { allX.push(s.x[i] + s.dx[i]); allY.push(s.y[i] + s.dy[i]) }
        if (s.type === 'band') allY.push(s.y2[i])
        if (s.type === 'bars' || s.type === 'area') allY.push(spec.y.log ? NaN : 0)
      }
    }
    xs = extent(allX, spec.x.log); ys = extent(allY, spec.y.log)
  }
  if (finite(spec.x.min) && spec.type !== 'heat') xs[0] = spec.x.min
  if (finite(spec.x.max) && spec.type !== 'heat') xs[1] = spec.x.max
  if (finite(spec.y.min) && spec.type !== 'heat') ys[0] = spec.y.min
  if (finite(spec.y.max) && spec.type !== 'heat') ys[1] = spec.y.max
  const X = axisMap(spec.x, xs[0], xs[1], plot[0], plot[2])
  const Y = axisMap({ ...spec.y, reverse: !spec.y.reverse }, ys[0], ys[1], plot[1], plot[3])
  const xt = ticks(xs[0], xs[1], spec.x.log), yt = ticks(ys[0], ys[1], spec.y.log)
  // grid
  const grid = []
  for (const v of xt) grid.push(X(v), plot[1], X(v), plot[3])
  for (const v of yt) grid.push(plot[0], Y(v), plot[2], Y(v))
  P.segments(grid, GRID, 1)
  if (spec.type === 'heat') {
    const vals = spec.grid.values.filter((v) => finite(v) && (!spec.z?.log || v > 0))
    const sorted = [...vals].sort((a, b) => a - b)
    const min = spec.z?.min ?? sorted[Math.floor(sorted.length * 0.01)] ?? 0, max = spec.z?.max ?? sorted[Math.floor(sorted.length * 0.995)] ?? 1
    P.heat(spec.grid, [X(xs[0]), Y(ys[1]), X(xs[1]), Y(ys[0])], { min, max, log: spec.z?.log, cmap: spec.cmap || 'ember' })
    text.push([`${spec.z?.label ?? ''} ${fmt(min)} – ${fmt(max)}${spec.z?.unit ? ' ' + spec.z.unit : ''}`, plot[2] - 4, plot[1] + 12, 'right'])
  }
  for (const s of [...(spec.series || []), ...(spec.overlay || [])]) {
    const col = rgba(s.color || '#f2b705', s.alpha ?? 1)
    const pc = (i) => (s.colors ? rgba(s.colors[i], s.alpha ?? 1) : col)
    const n = s.x?.length ?? 0
    if (s.type === 'points') {
      const pos = [], c = [], sz = []
      for (let i = 0; i < n; i++) {
        if (!finite(s.x[i]) || !finite(s.y[i]) || (spec.x.log && s.x[i] <= 0) || (spec.y.log && s.y[i] <= 0)) continue
        const px = X(s.x[i]), py = Y(s.y[i]); pos.push(px, py); c.push(...pc(i)); sz.push(s.sizes ? s.sizes[i] : s.size || 5)
        hover.push({ px, py, label: s.labels?.[i] ?? `${fmt(s.x[i])}, ${fmt(s.y[i])}` })
      }
      P.points(pos, new Float32Array(c), sz)
    } else if (s.type === 'line' || s.type === 'step') {
      const seg = []
      for (let i = 1; i < n; i++) {
        if (![s.x[i - 1], s.y[i - 1], s.x[i], s.y[i]].every(finite)) continue
        if (s.type === 'step') seg.push(X(s.x[i - 1]), Y(s.y[i - 1]), X(s.x[i]), Y(s.y[i - 1]), X(s.x[i]), Y(s.y[i - 1]), X(s.x[i]), Y(s.y[i]))
        else seg.push(X(s.x[i - 1]), Y(s.y[i - 1]), X(s.x[i]), Y(s.y[i]))
      }
      P.segments(seg, col, s.width || 1.6)
      if (s.hover !== false) for (let i = 0; i < n; i++) if (finite(s.x[i]) && finite(s.y[i])) hover.push({ px: X(s.x[i]), py: Y(s.y[i]), label: s.labels?.[i] ?? `${fmt(s.x[i])}, ${fmt(s.y[i])}` })
    } else if (s.type === 'area' || s.type === 'band') {
      const pos = [], c = []
      const base = (i) => (s.type === 'band' ? Y(s.y2[i]) : Y(spec.y.log ? ys[0] : Math.max(ys[0], 0)))
      for (let i = 1; i < n; i++) {
        if (![s.x[i - 1], s.y[i - 1], s.x[i], s.y[i]].every(finite)) continue
        const x0 = X(s.x[i - 1]), x1 = X(s.x[i]), a0 = Y(s.y[i - 1]), a1 = Y(s.y[i])
        pos.push(x0, a0, x1, a1, x1, base(i), x0, a0, x1, base(i), x0, base(i - 1))
        for (let v = 0; v < 6; v++) c.push(col[0], col[1], col[2], s.fill ?? 0.25)
      }
      P.tris(pos, new Float32Array(c))
    } else if (s.type === 'bars') {
      const pos = [], c = []
      const bw = s.width ?? (n > 1 ? Math.abs(s.x[1] - s.x[0]) : 1)
      for (let i = 0; i < n; i++) {
        if (!finite(s.y[i])) continue
        const x0 = X(s.x[i] - bw / 2) + 0.5, x1 = X(s.x[i] + bw / 2) - 0.5, y0 = Y(spec.y.log ? ys[0] : Math.max(ys[0], 0)), y1 = Y(Math.max(s.y[i], spec.y.log ? ys[0] : -Infinity))
        pos.push(x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1)
        const cc = pc(i); for (let v = 0; v < 6; v++) c.push(...cc)
        hover.push({ px: (x0 + x1) / 2, py: y1, label: s.labels?.[i] ?? `${fmt(s.x[i])}: ${fmt(s.y[i])}` })
      }
      P.tris(pos, new Float32Array(c))
    } else if (s.type === 'errorbars') {
      const seg = []
      for (let i = 0; i < n; i++) {
        if (!finite(s.x[i]) || !finite(s.y[i])) continue
        const px = X(s.x[i]), py = Y(s.y[i])
        const e = s.yerr?.[i]
        if (finite(e) && e > 0) { const a = Y(spec.y.log ? Math.max(s.y[i] - e, ys[0]) : s.y[i] - e), b = Y(s.y[i] + e); seg.push(px, a, px, b, px - 3, a, px + 3, a, px - 3, b, px + 3, b) }
        const ex = s.xerr?.[i]
        if (finite(ex) && ex > 0) seg.push(X(s.x[i] - ex), py, X(s.x[i] + ex), py)
        if (s.lower?.[i]) seg.push(px, py, px, py - 14, px, py - 14, px - 4, py - 8, px, py - 14, px + 4, py - 8) // lower limit: arrow up
      }
      P.segments(seg, col, 1.2)
    } else if (s.type === 'vectors' || s.type === 'segments') {
      const seg = []
      for (let i = 0; i < n; i++) {
        const x2 = s.type === 'vectors' ? s.x[i] + s.dx[i] : s.x2[i], y2 = s.type === 'vectors' ? s.y[i] + s.dy[i] : s.y2[i]
        if (![s.x[i], s.y[i], x2, y2].every(finite)) continue
        const a = [X(s.x[i]), Y(s.y[i])], b = [X(x2), Y(y2)]
        seg.push(...a, ...b)
        if (s.type === 'vectors') { const ang = Math.atan2(b[1] - a[1], b[0] - a[0]); for (const d of [2.6, -2.6]) seg.push(...b, b[0] - 6 * Math.cos(ang + d / 6), b[1] - 6 * Math.sin(ang + d / 6)) }
      }
      P.segments(seg, col, s.width || 1.2)
    } else if (s.type === 'box') {
      const pos = [], c = [], seg = []
      for (const g of s.groups) {
        const x0 = X(g.x - 0.3), x1 = X(g.x + 0.3), xm = X(g.x)
        pos.push(x0, Y(g.q1), x1, Y(g.q1), x1, Y(g.q3), x0, Y(g.q1), x1, Y(g.q3), x0, Y(g.q3))
        for (let v = 0; v < 6; v++) c.push(col[0], col[1], col[2], 0.35)
        seg.push(x0, Y(g.q2), x1, Y(g.q2), xm, Y(g.q3), xm, Y(g.hi), xm, Y(g.q1), xm, Y(g.lo), xm - 6, Y(g.hi), xm + 6, Y(g.hi), xm - 6, Y(g.lo), xm + 6, Y(g.lo))
        hover.push({ px: xm, py: Y(g.q2), label: `${g.label ?? fmt(g.x)} · median ${fmt(g.q2)} · n ${g.n ?? ''}` })
      }
      P.tris(pos, new Float32Array(c)); P.segments(seg, col, 1.3)
    }
  }
  P.blit(canvas)
  // 2D: axes, ticks and labels
  const g = canvas.getContext('2d')
  g.setTransform(dpr, 0, 0, dpr, 0, 0)
  g.strokeStyle = 'rgba(155,148,132,0.5)'; g.lineWidth = 1
  g.strokeRect(plot[0] + 0.5, plot[1] + 0.5, plot[2] - plot[0], plot[3] - plot[1])
  g.font = FONT; g.fillStyle = AXIS
  g.textAlign = 'center'; for (const v of xt) g.fillText(spec.x.fmt ? spec.x.fmt(v) : fmt(v), X(v), plot[3] + 12)
  g.textAlign = 'right'; for (const v of yt) g.fillText(spec.y.fmt ? spec.y.fmt(v) : fmt(v), plot[0] - 4, Y(v) + 3)
  g.textAlign = 'center'; g.fillText(`${spec.x.label ?? ''}${spec.x.unit ? ` (${spec.x.unit})` : ''}`, (plot[0] + plot[2]) / 2, h - 6)
  g.save(); g.translate(11, (plot[1] + plot[3]) / 2); g.rotate(-Math.PI / 2); g.fillText(`${spec.y.label ?? ''}${spec.y.unit ? ` (${spec.y.unit})` : ''}`, 0, 0); g.restore()
  for (const [t, x, y, al] of text) { g.textAlign = al; g.fillText(t, x, y) }
  for (const a of spec.annotations || []) { if (!finite(a.x)) continue; const px = X(a.x); g.fillStyle = a.color || '#f2b705'; g.fillRect(px, plot[1], 1, plot[3] - plot[1]); g.textAlign = 'left'; g.fillText(a.label, px + 3, plot[1] + 10) }
  if (spec.legend) { let lx = plot[0] + 6; g.textAlign = 'left'; for (const s of spec.series) { if (!s.label) continue; g.fillStyle = s.color || '#f2b705'; g.fillRect(lx, plot[1] + 6, 8, 8); g.fillStyle = AXIS; g.fillText(s.label, lx + 11, plot[1] + 14); lx += g.measureText(s.label).width + 24 } }
  return hover
}

/** One WebGL chart. Hover reads the nearest mark; 3D surfaces rotate with drag and zoom with the wheel. */
export default function Chart({ spec, height = 220 }) {
  const wrap = useRef(null)
  const cv = useRef(null)
  const hov = useRef([])
  const [tip, setTip] = useState(null)
  const [err, setErr] = useState(null)
  const view = useRef({ yaw: 0.75, pitch: 0.6, zoom: 1 })
  const draw = useRef(() => {})
  useEffect(() => {
    const c = cv.current, box = wrap.current
    if (!c || !box) return undefined
    draw.current = () => {
      const w = Math.max(160, box.clientWidth), dpr = Math.min(2, window.devicePixelRatio || 1)
      c.width = Math.round(w * dpr); c.height = Math.round(height * dpr); c.style.width = `${w}px`; c.style.height = `${height}px`
      try { hov.current = render(c, spec, w, height, dpr, view.current); setErr(null) } catch (e) { setErr(e.message) }
    }
    draw.current()
    const ro = new ResizeObserver(() => draw.current())
    ro.observe(box)
    return () => ro.disconnect()
  }, [spec, height])
  const onMove = (e) => {
    const r = cv.current.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top
    if (spec.type === 'surface') {
      if (e.buttons & 1) { view.current.yaw += e.movementX * 0.01; view.current.pitch = Math.max(0.05, Math.min(1.45, view.current.pitch + e.movementY * 0.01)); draw.current() }
      return
    }
    let best = null, bd = 18
    for (const p of hov.current) { const d = Math.hypot(p.px - x, p.py - y); if (d < bd) { bd = d; best = p } }
    setTip(best ? { x: best.px, y: best.py, label: best.label } : null)
  }
  const onWheel = (e) => { if (spec.type !== 'surface') return; e.preventDefault(); view.current.zoom = Math.max(0.5, Math.min(3, view.current.zoom * Math.exp(-e.deltaY * 0.001))); draw.current() }
  useEffect(() => { const c = cv.current; if (!c) return undefined; c.addEventListener('wheel', onWheel, { passive: false }); return () => c.removeEventListener('wheel', onWheel) })
  return (
    <div className="glchart" ref={wrap} style={{ height }}>
      <canvas ref={cv} onPointerMove={onMove} onPointerLeave={() => setTip(null)} style={{ cursor: spec.type === 'surface' ? 'grab' : 'crosshair', touchAction: spec.type === 'surface' ? 'none' : 'auto' }} />
      {tip && <div className="glchart-tip" style={{ left: Math.min(tip.x + 10, (wrap.current?.clientWidth ?? 300) - 150), top: Math.max(0, tip.y - 26) }}>{tip.label}</div>}
      {err && <div className="glchart-err">Chart unavailable: {err}</div>}
    </div>
  )
}

/** PNG of a chart canvas inside an element (for export). */
export function chartPng(el) { const c = el?.querySelector('canvas'); return c ? c.toDataURL('image/png') : null }
