import { useMemo } from 'react'
import { COLORMAPS, cssGradient } from '../lib/colormaps.js'
import { apertureStats, histogram, lineProfile, radialProfile, summarize } from '../lib/stats.js'
import { pixelHover, useStore } from '../store.js'
import { Histogram, LineChart } from './Charts.jsx'
import { Check, Section, Seg, Slider, Stat, Term } from './ui.jsx'

const f = (v, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—')

export function ColormapPicker({ value, onChange }) {
  return (
    <div className="cmaps" role="radiogroup" aria-label="Colour scheme">
      {COLORMAPS.map((c) => (
        <button key={c.id} type="button" role="radio" aria-checked={value === c.id} className={value === c.id ? 'on' : ''} onClick={() => onChange(c.id)}>
          <i style={{ background: cssGradient(c.id) }} />
          {c.label}
        </button>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- detector
function Inspector({ image, level }) {
  const hover = useStore(pixelHover)
  const expert = level === 'astronomer'
  const x = hover?.inside ? Math.floor(hover.x) : null
  const y = hover?.inside ? Math.floor(hover.y) : null
  const value = x === null ? null : image.gray[y * image.width + x]
  const cells = []
  if (expert && x !== null) {
    for (let j = -4; j <= 4; j++) for (let i = -4; i <= 4; i++) {
      const xx = x + i
      const yy = y + j
      cells.push(xx >= 0 && yy >= 0 && xx < image.width && yy < image.height ? image.gray[yy * image.width + xx] : null)
    }
  }
  return (
    <Section title="Pixel under the cursor">
      <dl className="stats">
        <Stat label="x, y" value={x === null ? '—' : `${x}, ${y}`} />
        <Stat label="Brightness" value={value === null ? '—' : value} unit=" / 255" />
      </dl>
      <div className="meter" aria-hidden="true"><i style={{ width: `${((value ?? 0) / 255) * 100}%` }} /></div>
      {expert && x !== null && (
        <div className="neighbourhood" aria-label="Values of the 9 by 9 pixels around the cursor">
          {cells.map((c, i) => <i key={i} className={i === 40 ? 'center' : ''} style={{ background: c === null ? 'transparent' : `rgb(${c},${c},${c})`, color: c !== null && c > 140 ? '#000' : '#fff' }}>{c ?? ''}</i>)}
        </div>
      )}
    </Section>
  )
}

export function DetectorTools({ probe, look, setLook, tool, setTool, aperture, setAperture, line, onFit, level }) {
  const image = probe?.image
  const expert = level === 'astronomer'
  const bins = useMemo(() => (image ? histogram(image.gray) : null), [image])
  const summary = useMemo(() => (bins ? summarize(bins) : null), [bins])
  const ap = useMemo(() => {
    if (!image || !aperture) return null
    const inner = apertureStats(image.gray, image.width, image.height, aperture.x, aperture.y, aperture.r)
    const r2 = apertureStats(image.gray, image.width, image.height, aperture.x, aperture.y, aperture.r * 2)
    const r3 = apertureStats(image.gray, image.width, image.height, aperture.x, aperture.y, aperture.r * 3)
    const bgN = r3.n - r2.n
    const bg = bgN > 0 ? (r3.sum - r2.sum) / bgN : NaN
    return { inner, bg, contrast: inner.mean - bg }
  }, [image, aperture])
  const radial = useMemo(() => {
    if (!image || !aperture) return null
    const max = Math.max(8, Math.round(aperture.r * 3))
    return radialProfile(image.gray, image.width, image.height, aperture.x, aperture.y, max, Math.max(1, Math.round(max / 40)))
  }, [image, aperture])
  const lineData = useMemo(() => (image && line ? lineProfile(image.gray, image.width, image.height, line.x0, line.y0, line.x1, line.y1) : null), [image, line])

  if (!image) {
    return <p className="hint pad">Observe a target and open its image to use these tools.</p>
  }

  const autoStretch = () => {
    let acc = 0
    let lo = 0
    let hi = 255
    const lowCut = summary.n * 0.005
    const highCut = summary.n * 0.995
    let loFound = false
    for (let v = 0; v < 256; v++) {
      acc += bins[v]
      if (!loFound && acc >= lowCut) { lo = v; loFound = true }
      if (acc >= highCut) { hi = v; break }
    }
    setLook({ ...look, black: lo, white: Math.max(hi, lo + 2) })
  }

  return (
    <>
      <Section title="Tool">
        <Seg label="Tool" value={tool} onChange={setTool} options={[
          { id: 'inspect', label: 'Inspect', hint: 'Drag to pan, scroll to zoom, hover to read pixels' },
          { id: 'aperture', label: 'Circle', hint: 'Click to measure brightness inside a circle' },
          { id: 'line', label: 'Line', hint: 'Drag to draw a cut across the image (right-drag pans)' },
        ]} />
        <p className="hint">
          {tool === 'inspect' && 'Drag to pan · scroll to zoom · hover for pixel values.'}
          {tool === 'aperture' && 'Click anywhere on the image to place a circle and measure it.'}
          {tool === 'line' && 'Drag across the image to cut a brightness profile. Right-drag pans.'}
        </p>
        <button type="button" className="btn small ghost" onClick={onFit}>Fit image to screen</button>
      </Section>

      <Section title="Appearance">
        <ColormapPicker value={look.cmap} onChange={(cmap) => setLook({ ...look, cmap })} />
        <Seg small label="Stretch" value={look.stretch} onChange={(stretch) => setLook({ ...look, stretch })} options={[
          { id: 'linear', label: 'Linear' }, { id: 'sqrt', label: 'Sqrt', hint: 'Lifts faint detail' }, { id: 'log', label: 'Log', hint: 'Strongly lifts faint detail' }, { id: 'asinh', label: 'Asinh', hint: 'Astronomers’ favourite: linear when dim, log when bright' },
        ]} />
        <Slider label="Black point" value={look.black} min={0} max={254} onChange={(black) => setLook({ ...look, black, white: Math.max(look.white, black + 1) })} />
        <Slider label="White point" value={look.white} min={1} max={255} onChange={(white) => setLook({ ...look, white, black: Math.min(look.black, white - 1) })} />
        <div className="inline">
          <button type="button" className="btn small ghost" onClick={autoStretch}>Auto</button>
          <button type="button" className="btn small ghost" onClick={() => setLook({ ...look, black: 0, white: 255 })}>Full range</button>
          <Check label="Invert" checked={look.invert} onChange={(invert) => setLook({ ...look, invert })} />
          <Check label="Smooth" checked={look.interpolate} onChange={(interpolate) => setLook({ ...look, interpolate })} />
        </div>
      </Section>

      <Inspector image={image} level={level} />

      <Section title="Circle measurement" aside={aperture && <button className="link" onClick={() => setAperture(null)}>clear</button>}>
        {!aperture ? <p className="hint">Pick the <b>Circle</b> tool, then click a star or a blank patch.</p> : (
          <>
            <Slider label="Radius" value={aperture.r} min={2} max={150} onChange={(r) => setAperture({ ...aperture, r })} format={(v) => `${v} px`} />
            <dl className="stats">
              <Stat label="Pixels inside" value={ap.inner.n} />
              <Stat label="Average" value={f(ap.inner.mean)} unit=" /255" />
              <Stat label="Brightest" value={ap.inner.max} unit=" /255" />
              <Stat label="Above surroundings" value={Number.isFinite(ap.contrast) ? `${ap.contrast >= 0 ? '+' : ''}${f(ap.contrast)}` : '—'} unit=" units" />
              {expert && <Stat label="Median" value={f(ap.inner.median, 0)} />}
              {expert && <Stat label="Std dev" value={f(ap.inner.std)} />}
              {expert && <Stat label="Peak at" value={`${ap.inner.peakX}, ${ap.inner.peakY}`} />}
              {expert && <Stat label="Local background" value={f(ap.bg)} />}
            </dl>
            <LineChart data={radial} xKey="r" yKey="mean" xLabel="distance from centre (px)" yLabel="average brightness" yDomain={[0, 255]} />
            <p className="hint">“Surroundings” is the ring between 2× and 3× the radius (dashed on the image).</p>
          </>
        )}
      </Section>

      <Section title="Line profile" aside={line && <button className="link" onClick={() => setTool('line')}>redraw</button>}>
        {!line ? <p className="hint">Pick the <b>Line</b> tool and drag across a star to see how its brightness rises and falls.</p> : (
          <LineChart data={lineData} xKey="d" yKey="v" xLabel="distance along the line (px)" yLabel="brightness" color="#4de3c2" yDomain={[0, 255]} />
        )}
      </Section>

      <Section title="Brightness histogram">
        <Histogram bins={bins} black={look.black} white={look.white} />
        {expert && summary && (
          <dl className="stats">
            <Stat label="Mean" value={f(summary.mean, 2)} />
            <Stat label="Median" value={summary.median} />
            <Stat label="Std dev" value={f(summary.std, 2)} />
            <Stat label="Pixels" value={summary.n.toLocaleString()} />
          </dl>
        )}
        <p className="hint">Every number here is in <Term k="units">display units</Term> (0–255), not physical flux.</p>
      </Section>
    </>
  )
}

// --------------------------------------------------------------------------- surface
export function SurfaceTools({ image, surface, setSurface, cmap, onCmap, hasCenter }) {
  if (!image) return <p className="hint pad">Observe a target and open its image to use these tools.</p>
  return (
    <>
      <Section title="Region">
        <Seg label="Region" value={surface.region} onChange={(region) => setSurface({ ...surface, region })} options={[
          { id: 'crop', label: '256 px crop', hint: 'Full resolution around the circle (or the image centre)' },
          { id: 'full', label: 'Whole detector', hint: 'Averaged down to fit' },
        ]} />
        <p className="hint">{surface.region === 'crop' ? (hasCenter ? 'Centred on your circle from the Detector view.' : 'Centred on the image. Place a circle in the Detector view to choose another spot.') : 'The full detector, averaged into blocks.'}</p>
      </Section>
      <Section title="Look">
        <ColormapPicker value={cmap} onChange={onCmap} />
        <Slider label="Height" value={surface.exaggeration} min={0.1} max={1.5} step={0.05} onChange={(exaggeration) => setSurface({ ...surface, exaggeration })} format={(v) => `${v.toFixed(2)}×`} />
        <div className="inline">
          <Check label="Contour lines" checked={surface.contours} onChange={(contours) => setSurface({ ...surface, contours })} />
          <button type="button" className="btn small ghost" onClick={() => setSurface({ ...surface, replay: surface.replay + 1 })}>Replay rise</button>
        </div>
      </Section>
    </>
  )
}

// --------------------------------------------------------------------------- depth
export function DepthTools({ nearest, flattened, onFlat, count, total }) {
  return (
    <>
      <Section title="From sphere to space">
        <Seg label="Arrangement" value={flattened ? 'sky' : 'space'} onChange={(v) => onFlat(v === 'sky')} options={[
          { id: 'space', label: 'True distances' }, { id: 'sky', label: 'Painted on the sky' },
        ]} />
        <p className="hint">Switch back and forth: the stars slide between where they really are and where they appear to be. On the sky you only see direction, never depth.</p>
      </Section>
      <Section title="Closest of these stars">
        <ul className="list">
          {nearest.map((s) => <li key={s.name}><span>{s.name}</span><small>mag {s.v.toFixed(1)}</small><b>{s.ly.toFixed(1)} ly</b></li>)}
        </ul>
        <p className="hint">{count} of the {total} brightest stars have a <Term k="parallax">parallax</Term> large enough to trust (distance under ~650 light-years). Rings are drawn every 100 light-years in the plane of the Milky Way.</p>
      </Section>
    </>
  )
}
