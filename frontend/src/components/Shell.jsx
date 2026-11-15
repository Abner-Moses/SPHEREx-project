import { useEffect, useMemo, useState } from 'react'
import { COLUMBA_NOTE, pigeonSvg } from '../eggs.js'
import { DEG, equatorialToGalactic, fmtDec, fmtRA, prettyStarName, spellGreek } from '../lib/astro.js'
import { PROJECTIONS, fmtFov, mjdToMs, propagate, yearsSinceJ2000 } from '../lib/skyview.js'
import { flightLog, useStore, viewStore } from '../store.js'
import { totalPm, typeLabel } from '../explorer.js'
import { Badge, Check, Section, Seg, Slider } from './ui.jsx'

const I = (d, extra) => <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{d}{extra}</svg>
export const ICONS = {
  menu: I(<path d="M4 7h16M4 12h16M4 17h16" />),
  search: I(<><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></>),
  equ: I(<><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c3 2.6 3 14.4 0 17M12 3.5c-3 2.6-3 14.4 0 17" /></>),
  gal: I(<><circle cx="12" cy="12" r="8.5" strokeDasharray="2 2.5" /><path d="M3.5 14c4-5 13-5 17-4M5 9c4 4 11 5 15 2" /></>),
  ecl: I(<><circle cx="12" cy="12" r="8.5" /><path d="M3.8 15c5 2 11-1 16.4-6" stroke="currentColor" /></>),
  plane: I(<><path d="M3 15c4-8 14-8 18-2" /><path d="M3 18c4-6 14-6 18-1" opacity=".5" /></>),
  labels: I(<><path d="M5 7h9M5 12h14M5 17h7" /></>),
  deep: I(<><circle cx="8" cy="8" r="1.4" /><circle cx="16" cy="9" r="1" /><circle cx="12" cy="15" r="1.6" /><circle cx="6" cy="16" r=".8" /><circle cx="18" cy="17" r=".8" /></>),
  foot: I(<><path d="M5 8l9-2 5 6-8 6z" /></>),
  overlay: I(<><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M4 14l4-3 4 3 4-4 4 3" /></>),
  night: I(<path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" />),
  full: I(<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />),
  eye: I(<><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>),
  proj: I(<><ellipse cx="12" cy="12" rx="9" ry="6" /><path d="M12 6v12M3 12h18" /></>),
  clock: I(<><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>),
  close: I(<path d="M6 6l12 12M18 6L6 18" />),
  sparkle: I(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM18 16l.8 2.2L21 19l-2.2.8L18 22l-.8-2.2L15 19l2.2-.8z" />),
  pin: I(<><circle cx="12" cy="10" r="3" /><path d="M12 21s7-6 7-11a7 7 0 10-14 0c0 5 7 11 7 11z" /></>),
  cons: I(<><path d="M4 17l5-6 4 3 7-8" /><circle cx="4" cy="17" r="1.4" /><circle cx="9" cy="11" r="1.4" /><circle cx="13" cy="14" r="1.4" /><circle cx="20" cy="6" r="1.4" /></>),
  capture: I(<><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4" /><circle cx="12" cy="12" r="3.2" /></>),
}

const TOOLBAR = [
  { id: 'equGrid', icon: 'equ', label: 'Equatorial grid', key: 'G' },
  { id: 'galGrid', icon: 'gal', label: 'Galactic grid', key: 'H' },
  { id: 'ecliptic', icon: 'ecl', label: 'Ecliptic', key: 'E' },
  { id: 'constellations', icon: 'cons', label: 'Constellations', key: 'K' },
  { id: 'galPlane', icon: 'plane', label: 'Milky Way plane', key: 'P' },
  { id: 'labels', icon: 'labels', label: 'Labels', key: 'L' },
  { id: 'deep', icon: 'deep', label: 'Fainter stars (SIMBAD, when zoomed in)', key: 'D' },
  { id: 'footprints', icon: 'foot', label: 'SPHEREx coverage outlines', key: 'F' },
  { id: 'overlay', icon: 'overlay', label: 'Show loaded image on the sky', key: 'I' },
]

export function Toolbar({ layers, setLayers, projection, setProjection, night, setNight, onFullscreen, onSettings }) {
  const [projOpen, setProjOpen] = useState(false)
  return (
    <nav className="toolbar" aria-label="Sky layers">
      {TOOLBAR.map((t) => (
        <button key={t.id} type="button" className={layers[t.id] ? 'on' : ''} aria-pressed={!!layers[t.id]} onClick={() => setLayers({ [t.id]: !layers[t.id] })} data-tip={`${t.label} (${t.key})`} aria-label={t.label}>{ICONS[t.icon]}</button>
      ))}
      <span className="sep" />
      <div className="menu-wrap">
        <button type="button" className={projOpen ? 'on' : ''} onClick={() => setProjOpen((o) => !o)} data-tip="Projection" aria-label="Projection">{ICONS.proj}</button>
        {projOpen && (
          <ul className="popmenu" role="menu">
            {Object.entries(PROJECTIONS).map(([id, p]) => <li key={id}><button type="button" role="menuitemradio" aria-checked={projection === id} className={projection === id ? 'on' : ''} onClick={() => { setProjection(id); setProjOpen(false) }}>{p.label}</button></li>)}
          </ul>
        )}
      </div>
      <button type="button" className={night ? 'on' : ''} aria-pressed={night} onClick={() => setNight(!night)} data-tip="Night mode (N)" aria-label="Night mode">{ICONS.night}</button>
      <button type="button" onClick={onFullscreen} data-tip="Fullscreen" aria-label="Fullscreen">{ICONS.full}</button>
      <button type="button" onClick={onSettings} data-tip="Menu and settings" aria-label="Menu and settings">{ICONS.eye}</button>
    </nav>
  )
}

export function SearchBar({ search, setSearch, onSubmit, inputRef, backendUp }) {
  const [focus, setFocus] = useState(false)
  const [hi, setHi] = useState(-1)
  const list = search.suggestions
  const pick = (o) => { setSearch((s) => ({ ...s, text: o.name, suggestions: [] })); onSubmit(o.name) }
  return (
    <div className="search" role="search" onClick={() => inputRef.current?.focus()}>
      <span className="prompt" aria-hidden="true">›</span>
      <input
        ref={inputRef} value={search.text} placeholder={backendUp === false ? 'goto coordinates (archive offline)' : 'goto an object, name or coordinates'} spellCheck="false" autoComplete="off"
        aria-label="Search" onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
        onChange={(e) => { setHi(-1); setSearch((s) => ({ ...s, text: e.target.value, error: null })) }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(list.length - 1, h + 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(-1, h - 1)) }
          else if (e.key === 'Enter') { e.preventDefault(); if (hi >= 0 && list[hi]) pick(list[hi]); else onSubmit() }
          else if (e.key === 'Escape') { setSearch((s) => ({ ...s, text: '', suggestions: [], error: null })); e.target.blur() }
        }}
      />
      {search.busy && <span className="spinner" />}
      {focus && list.length > 0 && (
        <ul className="suggest" role="listbox">
          {list.map((o, i) => (
            <li key={o.name} role="option" aria-selected={i === hi}>
              <button type="button" className={i === hi ? 'on' : ''} onMouseDown={(e) => { e.preventDefault(); pick(o) }}>
                <b>{prettyStarName(o.name)}</b><small>{typeLabel(o.otype)}{o.vmag != null ? ` · V ${o.vmag.toFixed(1)}` : ''}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
      {search.error && <div className="search-error" role="alert">{search.error}</div>}
    </div>
  )
}

/** Attitude dial: which way the view faces (RA ring) and how much sky fits on screen (cream arc). */
export function ContextPill({ selected, projection }) {
  const v = useStore(viewStore) // the only component that re-renders as the sky moves
  const ra = (((v.lon0 / DEG) % 360) + 360) % 360
  const S = 112, c = S / 2, r = c - 6
  const pt = (deg, rad) => [c + Math.sin((deg * Math.PI) / 180) * rad, c - Math.cos((deg * Math.PI) / 180) * rad]
  const span = Math.min(170, Math.max(4, v.fov))
  const [ax, ay] = pt(ra - span / 2, r + 4)
  const [bx, by] = pt(ra + span / 2, r + 4)
  const [nx, ny] = pt(ra, r - 12)
  return (
    <div className="attitude" title="View direction and field of view">
      <svg viewBox={`0 0 ${S} ${S}`} aria-hidden="true">
        <circle cx={c} cy={c} r={r} fill="rgba(10,11,12,.6)" stroke="#252a2f" />
        {Array.from({ length: 72 }, (_, i) => { const big = i % 6 === 0; const [x1, y1] = pt(i * 5, r); const [x2, y2] = pt(i * 5, r - (big ? 7 : 3)); return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={big ? '#7f888f' : '#333a40'} /> })}
        {[0, 90, 180, 270].map((d) => { const [x, y] = pt(d, r - 17); return <text key={d} x={x} y={y + 3} textAnchor="middle" fontFamily="IBM Plex Mono" fontSize="8" fill="#525a61">{d}</text> })}
        <path d={`M${ax} ${ay} A${r + 4} ${r + 4} 0 ${span > 180 ? 1 : 0} 1 ${bx} ${by}`} stroke="#e6dfcc" strokeWidth="2" fill="none" />
        <line x1={c} y1={c} x2={nx} y2={ny} stroke="#f2b705" strokeWidth="1.5" />
        <circle cx={c} cy={c} r="2.6" fill="#f2b705" />
      </svg>
      <div className="read">
        <small>Attitude</small>
        <span>{selected ? prettyStarName(selected.name) : `${fmtRA(ra).replace(/\.\d/, '')} ${fmtDec(v.lat0 / DEG)}`}</span>
        <span>FOV {fmtFov(v.fov)}{projection === 'map' ? ' · map' : ''}</span>
      </div>
    </div>
  )
}

/** Flight log: the last few real events. */
export function FlightLog() {
  const items = useStore(flightLog)
  if (!items.length) return null
  return (
    <div className="flightlog" aria-live="polite">
      <div className="hd">Flight log</div>
      {items.map((e) => <p key={e.id}><s>{e.t}</s><span className={e.kind}>{e.kind === 'ok' ? '✓ ' : e.kind === 'r' ? '● ' : e.kind === 'v' ? '◆ ' : '▸ '}</span>{e.text}</p>)}
    </div>
  )
}

function YourTime() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  return <b>{new Date(now).toISOString().slice(11, 19)}</b>
}

function fmtGap(ms) {
  const d = ms / 86400000
  const a = Math.abs(d)
  const sign = d < 0 ? '−' : '+'
  if (a < 1) return 'Δt 0 d'
  if (a < 730) return `Δt ${sign}${Math.round(a)} d`
  return `Δt ${sign}${Math.round(a / 365.25).toLocaleString()} y`
}

const RATES = [
  { id: 1, label: 'Real time', v: 1 }, { id: 'd', label: '1 day / s', v: 86400 }, { id: 'm', label: '1 month / s', v: 2629800 }, { id: 'y', label: '1 year / s', v: 31557600 }, { id: 'dec', label: '10 years / s', v: 315576000 },
]

export function TimePill({ epochMs, setEpoch, timeRate, setTimeRate, productMjd }) {
  const [open, setOpen] = useState(false)
  const [rate, setRate] = useState('y')
  const d = new Date(epochMs)
  const iso = d.toISOString()
  const step = (ms) => { setTimeRate(0); setEpoch(epochMs + ms) }
  const dir = Math.sign(timeRate) || 1
  const rv = RATES.find((r) => r.id === rate).v
  return (
    <div className="timewrap">
      {open && (
        <div className="timepanel" role="dialog" aria-label="Date and time">
          <div className="tp-head"><b>Sky date</b><button type="button" className="icon-btn subtle" aria-label="Close" onClick={() => setOpen(false)}>{ICONS.close}</button></div>
          <p className="hint">Stars are moved by their catalogued proper motion to this date.</p>
          <div className="tp-row">
            <button type="button" onClick={() => step(-365.25 * 86400000)}>−1 y</button><button type="button" onClick={() => step(-86400000)}>−1 d</button>
            <button type="button" className={timeRate ? 'on' : ''} aria-label={timeRate ? 'Pause' : 'Play'} onClick={() => setTimeRate(timeRate ? 0 : rv * dir)}>{timeRate ? '❚❚' : '▶'}</button>
            <button type="button" onClick={() => step(86400000)}>+1 d</button><button type="button" onClick={() => step(365.25 * 86400000)}>+1 y</button>
          </div>
          <label className="select"><span>Speed</span><select value={rate} onChange={(e) => { setRate(e.target.value); if (timeRate) setTimeRate(RATES.find((r) => String(r.id) === e.target.value).v * dir) }}>{RATES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select></label>
          <div className="inline">
            <button type="button" className="btn small ghost" onClick={() => { setTimeRate(0); setEpoch(Date.now()) }}>Now</button>
            <button type="button" className="btn small ghost" onClick={() => { setTimeRate(0); setEpoch(Date.UTC(2000, 0, 1, 12)) }}>J2000.0</button>
            {productMjd && <button type="button" className="btn small ghost" onClick={() => { setTimeRate(0); setEpoch(mjdToMs(productMjd)) }}>Image date</button>}
            <button type="button" className="btn small ghost" onClick={() => setTimeRate(-timeRate || -rv)}>⇄ Reverse</button>
          </div>
        </div>
      )}
      <button type="button" className="deck-clocks" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Change the date the sky is drawn at">
        <span className="clk"><small>Sky epoch</small><b className="amber">{iso.slice(0, 10).replace(/-/g, '·')}</b></span>
        {timeRate !== 0 && <span className="run">{timeRate > 0 ? '▶' : '◀'}</span>}
        <span className={`dt${Math.abs(epochMs - Date.now()) < 86400000 ? ' zero' : ''}`}>{fmtGap(epochMs - Date.now())}</span>
        <span className="clk mine"><small>Your time · UTC</small><YourTime /></span>
      </button>
    </div>
  )
}

export function InfoCard({ selected, epochMs, onClose, onZoom, onPreview, onAskVoyager, onObservations, productMeta, loadingProduct, isBarnard }) {
  const info = useMemo(() => {
    if (!selected) return null
    const years = yearsSinceJ2000(epochMs)
    const p = propagate(selected.ra, selected.dec, selected.pmra, selected.pmdec, years)
    const ra = (((p.lon / DEG) % 360) + 360) % 360
    return { now: { ra, dec: p.lat / DEG }, gal: equatorialToGalactic(ra, p.lat / DEG), pm: totalPm(selected) }
  }, [selected, epochMs])
  if (!selected) return null
  return (
    <aside className="infocard focus" aria-label="Selected object">
      <div className="target-tag"><span>Target</span><span className="lamp">Focus</span></div>
      <header>
        <div><h2>{spellGreek(prettyStarName(selected.name))}</h2><p>{selected.egg === 'columba' ? 'Constellation' : selected.point ? 'Chosen position' : typeLabel(selected.otype)}</p></div>
        <button type="button" className="icon-btn subtle" onClick={onClose} aria-label="Deselect">{ICONS.close}</button>
      </header>
      <dl>
        <div><dt>RA</dt><dd>{fmtRA(info.now.ra)}</dd></div>
        <div><dt>Dec</dt><dd>{fmtDec(info.now.dec)}</dd></div>
        <div><dt>Galactic</dt><dd>l {info.gal.l.toFixed(3)}° b {info.gal.b.toFixed(3)}°</dd></div>
        {selected.vmag != null && <div><dt>Magnitude</dt><dd>V {Number(selected.vmag).toFixed(2)}</dd></div>}
        {selected.distance_ly != null && <div><dt>Distance</dt><dd>{selected.distance_ly < 100 ? selected.distance_ly.toFixed(2) : Math.round(selected.distance_ly)} light-years</dd></div>}
        {info.pm && <div><dt>Proper motion</dt><dd>{(info.pm.mas / 1000).toFixed(3)}″/yr toward {info.pm.pa.toFixed(0)}°</dd></div>}
      </dl>
      {selected.egg === 'columba' ? <p className="egg-note">{COLUMBA_NOTE}</p> : <p className="fine">Position for the date shown (J2000 catalogue position moved by proper motion).</p>}
      <div className="inline">
        <button type="button" className="btn small" onClick={onZoom}>Zoom to</button>
        <button type="button" className="btn small" onClick={onObservations}>SPHEREx images</button>
        {onAskVoyager && <button type="button" className="btn small ghost" onClick={onAskVoyager}>Ask Voyager</button>}
        {isBarnard && <button type="button" className="btn small primary" disabled={loadingProduct} onClick={onPreview}>{productMeta ? 'Reload preview' : 'Open SPHEREx preview'}</button>}
      </div>
    </aside>
  )
}

export function MenuDrawer({ open, onClose, onReplayIntro, voyager, setVoyager, landmarks, onLandmark, settings, backendUp, projection, setProjection, layers, setLayers, night, setNight }) {
  if (!open) return null
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" aria-label="Menu">
        <header><b>SPHEREx <span>Sky Explorer</span></b><button type="button" className="icon-btn subtle" aria-label="Close menu" onClick={onClose}>{ICONS.close}</button></header>
        <div className="scroll">
          <Section title="Experience"><Seg label="Level" value={settings.level} onChange={settings.setLevel} options={[{ id: 'explorer', label: 'Explorer' }, { id: 'astronomer', label: 'Astronomer' }]} /></Section>
          <Section title="Landmarks"><ul className="chips">{landmarks.map((l) => <li key={l.label}><button type="button" onClick={() => { onLandmark(l); onClose() }}>{l.label}</button></li>)}</ul></Section>
          <Section title="View settings">
            <Seg small label="Projection" value={projection} onChange={setProjection} options={Object.entries(PROJECTIONS).map(([id, p]) => ({ id, label: p.label.split(' ')[0] }))} />
            <div className="checks">
              <Check label="Coordinate grid" checked={layers.equGrid} onChange={(equGrid) => setLayers({ equGrid })} />
              <Check label="Galactic grid" checked={layers.galGrid} onChange={(galGrid) => setLayers({ galGrid })} />
              <Check label="Ecliptic" checked={layers.ecliptic} onChange={(ecliptic) => setLayers({ ecliptic })} />
              <Check label="Milky Way plane" checked={layers.galPlane} onChange={(galPlane) => setLayers({ galPlane })} />
              <Check label="Labels" checked={layers.labels} onChange={(labels) => setLayers({ labels })} />
              <Check label="Landmark markers" checked={layers.markers} onChange={(markers) => setLayers({ markers })} />
              <Check label="Night mode" checked={night} onChange={setNight} />
            </div>
          </Section>
          <Section title="Status"><p className="hint">Backend: <Badge tone={backendUp ? 'live' : 'sim'}>{backendUp === null ? 'checking' : backendUp ? 'online' : 'offline'}</Badge></p></Section>
          {voyager && (
            <Section title="Voyager">
              <Check label="Show Voyager" checked={voyager.show} onChange={(show) => setVoyager({ show })} />
              <Slider label="Humor" value={voyager.humor} min={0} max={100} step={10} onChange={(humor) => setVoyager({ humor })} format={(v) => `${v}%`} />
              <Slider label="Detail" value={voyager.detail} min={20} max={100} step={20} onChange={(detail) => setVoyager({ detail })} format={(v) => `${v}%`} />
              <p className="fine">Humor above 50% gets noticeably sillier. Press <b>V</b> to ask Voyager about anything on screen.</p>
            </Section>
          )}
          <Section title="Intro"><button type="button" className="btn small ghost" onClick={onReplayIntro}>Replay the intro film</button></Section>
          <Section title="Crew">
            <p className="crew"><span className="crew-pigeon" dangerouslySetInnerHTML={{ __html: pigeonSvg('perched', 3) }} />Built by <b>Los Palomos</b></p>
          </Section>
          <Section title="Data credits">
            <p className="fine">Images: NASA SPHEREx via NASA/IPAC Infrared Science Archive (IRSA). Stars, names, proper motions and parallaxes: SIMBAD, CDS Strasbourg. Coordinates are ICRS; star positions are propagated from J2000.0 with linear proper motion.</p>
            <p className="fine">Sky view: 41,487 stars from the HYG Database v4.1 (David Nash / Astronexus, CC BY-SA 4.0). Constellations: d3-celestial (Olaf Frohn, BSD-3-Clause). Background imagery: 2MASS (UMass / IPAC-Caltech, NASA, NSF) and DSS (STScI) colour HiPS, served by CDS Strasbourg, IRSA and STScI. Planets: JPL approximate elements. Rendering adapted from the team&apos;s Stellarium-style explorer. Licences in /licenses.</p>
          </Section>
          <Section title="Keys"><p className="fine"><b>G</b> grid · <b>H</b> galactic grid · <b>E</b> ecliptic · <b>P</b> Milky Way · <b>L</b> labels · <b>D</b> faint stars · <b>F</b> coverage · <b>I</b> image · <b>N</b> night · <b>K</b> constellations · <b>B</b> sky imagery · <b>+ / −</b> zoom · <b>arrows</b> pan · <b>/</b> search · <b>C</b> capture · <b>V</b> ask Voyager · <b>1–4</b> views · <b>Esc</b> deselect</p></Section>
        </div>
      </aside>
    </>
  )
}
