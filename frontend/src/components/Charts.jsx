import { useMemo, useState } from 'react'

const W = 300
const M = { l: 38, r: 8, t: 10, b: 24 }

const nice = (v) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2))

/** Line chart with axes, hover read-out and a draw-in animation. data: [{ [xKey]: number, [yKey]: number }] */
export function LineChart({ data, xKey = 'x', yKey = 'y', xLabel, yLabel, color = 'var(--accent)', height = 130, yDomain, xFormat = nice, yFormat = nice, empty = 'No data yet' }) {
  const [hover, setHover] = useState(null)
  const geometry = useMemo(() => {
    if (!data || data.length < 2) return null
    const xs = data.map((d) => d[xKey])
    const ys = data.map((d) => d[yKey])
    const x0 = Math.min(...xs)
    const x1 = Math.max(...xs)
    let y0 = yDomain ? yDomain[0] : Math.min(...ys)
    let y1 = yDomain ? yDomain[1] : Math.max(...ys)
    if (y1 - y0 < 1e-9) {
      y0 -= 1
      y1 += 1
    }
    const sx = (x) => M.l + ((x - x0) / (x1 - x0 || 1)) * (W - M.l - M.r)
    const sy = (y) => M.t + (1 - (y - y0) / (y1 - y0)) * (height - M.t - M.b)
    const path = data.map((d, i) => `${i ? 'L' : 'M'}${sx(d[xKey]).toFixed(1)},${sy(d[yKey]).toFixed(1)}`).join('')
    return { x0, x1, y0, y1, sx, sy, path, area: `${path}L${sx(x1).toFixed(1)},${sy(y0).toFixed(1)}L${sx(x0).toFixed(1)},${sy(y0).toFixed(1)}Z` }
  }, [data, xKey, yKey, yDomain, height])

  if (!geometry) return <div className="chart-empty" style={{ height }}>{empty}</div>
  const { x0, x1, y0, y1, sx, sy, path, area } = geometry
  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = x0 + (((e.clientX - rect.left) / rect.width) * W - M.l) / (W - M.l - M.r) * (x1 - x0)
    let best = 0
    data.forEach((d, i) => { if (Math.abs(d[xKey] - x) < Math.abs(data[best][xKey] - x)) best = i })
    setHover(best)
  }
  const h = hover !== null ? data[hover] : null
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`${yLabel} versus ${xLabel}`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line className="grid" x1={M.l} x2={W - M.r} y1={sy(y0 + (y1 - y0) * t)} y2={sy(y0 + (y1 - y0) * t)} />
            <text className="tick" x={M.l - 5} y={sy(y0 + (y1 - y0) * t) + 3} textAnchor="end">{yFormat(y0 + (y1 - y0) * t)}</text>
            <text className="tick" x={sx(x0 + (x1 - x0) * t)} y={height - 9} textAnchor={t === 0 ? 'start' : t === 1 ? 'end' : 'middle'}>{xFormat(x0 + (x1 - x0) * t)}</text>
          </g>
        ))}
        <path className="area" d={area} fill={color} />
        <path className="draw" d={path} pathLength="1" stroke={color} />
        {h && (
          <g>
            <line className="cursor" x1={sx(h[xKey])} x2={sx(h[xKey])} y1={M.t} y2={height - M.b} />
            <circle cx={sx(h[xKey])} cy={sy(h[yKey])} r="3.5" fill={color} />
          </g>
        )}
      </svg>
      <figcaption>
        <span>{xLabel}</span>
        <b>{h ? `${xFormat(h[xKey])} → ${yFormat(h[yKey])}` : yLabel}</b>
      </figcaption>
    </figure>
  )
}

/** 256-bin histogram re-binned to 64 bars with black/white-point markers. Log scale keeps faint populations visible. */
export function Histogram({ bins, black = 0, white = 255, height = 90 }) {
  const bars = useMemo(() => {
    if (!bins) return null
    const out = new Array(64).fill(0)
    for (let v = 0; v < 256; v++) out[v >> 2] += bins[v]
    const logs = out.map((c) => Math.log1p(c))
    const max = Math.max(...logs) || 1
    return logs.map((l) => l / max)
  }, [bins])
  if (!bars) return <div className="chart-empty" style={{ height }}>No data yet</div>
  const bw = (W - M.l - M.r) / 64
  const base = height - M.b
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Histogram of pixel brightness">
        {bars.map((b, i) => (
          <rect key={i} className="bar" x={M.l + i * bw + 0.5} width={Math.max(bw - 1, 1)} y={base - b * (base - M.t)} height={Math.max(b * (base - M.t), 0)} style={{ animationDelay: `${i * 6}ms` }} />
        ))}
        <line className="grid" x1={M.l} x2={W - M.r} y1={base} y2={base} />
        <line className="marker-line" x1={M.l + (black / 255) * (W - M.l - M.r)} x2={M.l + (black / 255) * (W - M.l - M.r)} y1={M.t} y2={base} />
        <line className="marker-line" x1={M.l + (white / 255) * (W - M.l - M.r)} x2={M.l + (white / 255) * (W - M.l - M.r)} y1={M.t} y2={base} />
        {[0, 128, 255].map((v) => (
          <text key={v} className="tick" x={M.l + (v / 255) * (W - M.l - M.r)} y={height - 9} textAnchor={v === 0 ? 'start' : v === 255 ? 'end' : 'middle'}>{v}</text>
        ))}
        <text className="tick" x={M.l - 5} y={M.t + 8} textAnchor="end">log</text>
      </svg>
      <figcaption>
        <span>display brightness (0–255)</span>
        <b>pixels per level</b>
      </figcaption>
    </figure>
  )
}
