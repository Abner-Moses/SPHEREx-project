import { useEffect, useRef, useState } from 'react'
import App from './App.jsx'
import DatasetWorkspace from './DatasetWorkspace.jsx'
import Intro from './intro/Intro.jsx'
import { sprite } from './voyager/sprite.js'
import './Presentation.css'

const CHAPTERS = [
  [0, 'Barnard’s Star / sky explorer', 'A calibrated SPHEREx image, placed on the sky at its observation epoch.'],
  [2, 'Capture & identify', 'Frame the star to save a screenshot and identify its real observations.'],
  [5, 'Scientific information', 'Inspect measured spectra, image structure and quality diagnostics.'],
  [10, 'Observation data panel', 'Open the local dataset ledger: dates, wavelengths and contamination flags.'],
  [11, 'Temporal blinking', 'Blink registered epochs. Different wavelengths can mimic temporal change.'],
  [16, 'Native spectral measurements', 'Inspect the 102-channel cube; blank channels remain missing.'],
  [20, 'Other scientific observations', 'Compare Epsilon Indi B and WISE 0855 using their own measured data.'],
  [25, 'Scientific limits & export', 'R Draconis is saturated. Its ring brightness is in MJy/sr; lower limits stay marked.'],
  [27, 'Detector · Depth · Surface', 'Inspect detector pixels, spatial context and the measured image surface.'],
]
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const byText = (text, root = document) => [...root.querySelectorAll('button')].find(el => el.textContent.trim() === text)
const byLabel = label => document.querySelector(`[aria-label="${label}"]`)

export default function PresentationRoot() {
  const [mode, setMode] = useState('choose'), [sky, setSky] = useState(false)
  const [prepared, setPrepared] = useState(false), [status, setStatus] = useState(''), [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0), [chapter, setChapter] = useState(0), [muted, setMuted] = useState(false)
  const [cursor, setCursor] = useState({ x: 180, y: 140 }), [clicking, setClicking] = useState(false), [frame, setFrame] = useState(null)
  const dataset = useRef(null), skyApi = useRef(null), soundtrack = useRef(null), clickSound = useRef(null)
  const live = useRef(false), run = useRef(0), pointer = useRef({ x: 180, y: 140 }), timers = useRef([]), actions = useRef([])
  const avatar = useRef(null), tourStart = useRef(0), introStarted = useRef(0), introDuration = useRef(0)
  useEffect(() => { if (avatar.current) avatar.current.src = sprite('deadpan').toDataURL() }, [mode])
  useEffect(() => () => { live.current = false; run.current++; soundtrack.current?.pause(); timers.current.forEach(clearTimeout) }, [])
  const delay = (ms, fn) => { const id = setTimeout(() => { if (live.current) fn() }, ms); timers.current.push(id) }
  function stop() { live.current = false; run.current++; timers.current.forEach(clearTimeout); timers.current = []; soundtrack.current?.pause(); dataset.current?.play(false); skyApi.current?.closeCapture(); setFrame(null) }
  useEffect(() => { const key = e => { if (e.key === 'Escape' && mode === 'playing') { stop(); setMode('done') } }; window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key) }, [mode])
  function sound() { if (clickSound.current && !clickSound.current.muted) { clickSound.current.currentTime = 0; clickSound.current.play().catch(() => {}) } }
  async function move(x, y, duration = 620) {
    const token = run.current, start = performance.now(), from = { ...pointer.current }
    return new Promise(resolve => {
      const tick = now => { if (!live.current || token !== run.current) { resolve(); return }
        const t = Math.min(1, (now - start) / duration), e = t * t * (3 - 2 * t)
        const p = { x: from.x + (x - from.x) * e + Math.sin(t * Math.PI) * 13, y: from.y + (y - from.y) * e - Math.sin(t * Math.PI) * 22 }
        pointer.current = p; setCursor(p); if (t < 1) requestAnimationFrame(tick); else resolve()
      }; requestAnimationFrame(tick)
    })
  }
  async function point(el, { click = true, scroll = true } = {}) {
    if (!el || !live.current) return
    if (scroll) { el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' }); await sleep(380) }
    const r = el.getBoundingClientRect(); await move(r.x + r.width * .53, r.y + r.height * .52)
    if (!live.current) return
    if (click) { setClicking(true); sound(); el.click(); actions.current.push({ at: (performance.now() - tourStart.current) / 1000, text: el.textContent.trim() || el.getAttribute('aria-label') }); delay(190, () => setClicking(false)) }
  }
  async function tab(name) { const el = byText(name); if (el) await point(el) }
  async function waitFor(fn, label, timeout = 45000) {
    const start = performance.now()
    while (!fn()) { if (performance.now() - start > timeout) throw new Error(`${label} did not become ready. Check the local backend and dataset.`); await sleep(120) }
  }
  async function prepare() {
    stop(); const token = run.current; dataset.current?.select('barnards-star'); setError(''); setMode('preparing'); setSky(false); setPrepared(false)
    try {
      setStatus('Loading Barnard’s Star measurements and the sky catalogue…')
      await waitFor(() => dataset.current?.ready && skyApi.current?.ready, 'Local data'); if (token !== run.current) return
      setStatus('Preparing six genuine WCS-aligned epochs and a calibrated sky image…')
      await Promise.all([dataset.current.prepare(), skyApi.current.prepare(), import('./components/Capture.jsx'), import('./components/AnalysisDeck.jsx')])
      await waitFor(() => dataset.current?.sequenceReady && skyApi.current?.imageReady, 'Real image products'); if (token !== run.current) return
      await Promise.all([...document.querySelectorAll('.dw-image-stage img')].map(img => img.decode()))
      const track = new Audio('/demo/audio/presentation.mp3'); track.preload = 'auto'; soundtrack.current = track
      clickSound.current = new Audio('/demo/audio/click.ogg'); clickSound.current.volume = .32
      await new Promise((resolve, reject) => { track.oncanplaythrough = resolve; track.onerror = () => reject(new Error('Presentation soundtrack unavailable.')); track.load() })
      if (token !== run.current) return; dataset.current.tab('photometry'); skyApi.current.focus()
      document.querySelector('.dataset-workspace')?.scrollTo({ top: 0 })
      setStatus('Real data, images and soundtrack are ready.'); setPrepared(true); setMode('ready')
    } catch (e) { if (token === run.current) { setError(e.message); setMode('choose') } }
  }
  function start() {
    if (!prepared) return
    run.current++; live.current = true; actions.current = []; setSky(true); setSeconds(0); setChapter(0); setMode('intro')
    introStarted.current = performance.now()
    const audio = soundtrack.current; audio.currentTime = 0; audio.loop = true; audio.muted = muted; if (clickSound.current) clickSound.current.muted = muted
    audio.play().catch(e => { stop(); setError(e.message); setMode('ready') })
  }
  function beginTour() {
    if (!live.current) return
    setSky(true); setMode('playing'); setSeconds(0); setChapter(0)
    introDuration.current = (performance.now() - introStarted.current) / 1000; tourStart.current = performance.now()
    const origin = tourStart.current, token = run.current
    const clock = now => { if (!live.current || token !== run.current) return
      const time = Math.min(30, (now - origin) / 1000); setSeconds(time)
      setChapter(CHAPTERS.reduce((last, item, i) => time >= item[0] ? i : last, 0))
      if (time < 30) requestAnimationFrame(clock); else { stop(); setSeconds(30); setMode('done') }
    }; requestAnimationFrame(clock)
    delay(300, () => point(byLabel('View'), { click: false, scroll: false }))
    delay(2000, async () => {
      await point(byLabel('Capture a star or body (C)'), { scroll: false }); await sleep(150)
      const rect = document.querySelector('.demo-sky-pane canvas.gl')?.getBoundingClientRect(); if (!rect || !live.current) return
      const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2
      await move(cx - 100, cy - 85, 220); setFrame({ left: cx - 100, top: cy - 85, width: 200, height: 170 }); setClicking(true)
      await move(cx + 100, cy + 85, 400); setClicking(false); sound(); skyApi.current.frame({ x0: cx - 100, y0: cy - 85, x1: cx + 100, y1: cy + 85 }); setFrame(null)
      actions.current.push({ at: (performance.now() - tourStart.current) / 1000, text: 'Capture screenshot' })
      delay(300, () => point(byText('Analyze · science'), { scroll: false }))
    })
    delay(5100, () => {
      const grid = document.querySelector('.da-grid'); if (!grid) return
      const start = performance.now(), token = run.current, distance = Math.min(1050, grid.scrollHeight - grid.clientHeight)
      const scroll = now => { if (!live.current || token !== run.current) return; const t = Math.min(1, (now - start) / 4200); grid.scrollTop = distance * (t * t * (3 - 2 * t)); if (t < 1) requestAnimationFrame(scroll) }
      requestAnimationFrame(scroll); const r = grid.getBoundingClientRect(); move(r.x + r.width * .71, r.y + r.height * .45, 900)
    })
    delay(9800, () => byLabel('Close analysis')?.click())
    delay(10000, async () => { skyApi.current.closeCapture(); await point(byText('← Dataset workspace'), { scroll: false }); setSky(false) })
    delay(11200, async () => { await point(document.querySelector('.dw-player button:nth-child(2)')); dataset.current.play(true) })
    delay(16000, async () => {
      dataset.current.play(false); await tab('Cube spectrum (102)')
      const table = document.querySelector('table[aria-label="102 channel cube spectrum"]')?.parentElement
      if (table) { const start = performance.now(), token = run.current; const scroll = now => { if (!live.current || token !== run.current) return; const t = Math.min(1, (now - start) / 2200); table.scrollTop = 550 * t; if (t < 1) requestAnimationFrame(scroll) }; requestAnimationFrame(scroll) }
    })
    delay(20000, async () => { await point(byText('Epsilon Indi B', document.querySelector('.dw-science-targets')), { scroll: false }); dataset.current.tab('photometry') })
    delay(22500, () => point(byText('WISE 0855-0714', document.querySelector('.dw-science-targets')), { scroll: false }))
    delay(25000, () => point(byText('R Draconis', document.querySelector('.dw-science-targets')), { scroll: false }))
    delay(26000, () => point(byText('Export CSV ↓')))
    delay(27000, () => { setSky(true); skyApi.current.closeCapture(); skyApi.current.view('image'); point(byText('Image', document.querySelector('.topbar') || document), { scroll: false }) })
    delay(28000, () => point(byText('Depth', document.querySelector('.topbar') || document), { scroll: false }))
    delay(29000, () => point(byText('3D', document.querySelector('.topbar') || document), { scroll: false }))
  }

  function explore() { stop(); setSky(false); setMode('explore') }
  const mounted = mode !== 'choose'
  return <>
    {mounted && <><div className={`demo-dataset-pane${mode === 'playing' ? ' presentation-playing' : ''}${sky ? ' hidden' : ''}`}><DatasetWorkspace presentationRef={dataset} onSky={() => setSky(true)} /></div><div className={`demo-sky-pane${mode === 'playing' ? ' presentation-playing' : ''}${sky ? '' : ' hidden'}`}><App presentation={mode !== 'explore'} presentationRef={skyApi} /><button className="dw-return" onClick={() => setSky(false)}>← Dataset workspace</button></div></>}
    {['choose', 'preparing', 'ready', 'done'].includes(mode) && <section className="demo-entry" data-demo-state={mode} role="dialog" aria-label="Choose exploration or presentation">
      <div className="demo-entry-card"><p className="demo-kicker">SPHEREX / MISSION PRESENTATION</p><img ref={avatar} className="demo-entry-voyager" alt="Voyager guide" /><h1>{mode === 'done' ? 'Real data. A clear view.' : 'From the sky to the science.'}</h1><p>{mode === 'done' ? 'The presentation is complete. Continue exploring the measured data.' : 'Watch the original intro at a slightly faster pace, then follow Voyager’s captioned 30-second tour from the sky into measured data and other observations.'}</p>
      <div className="demo-entry-facts"><span>Original intro + 30-second tour</span><span>Real local SPHEREx data</span><span>Music · captions · natural cursor</span></div>
      {error && <p className="demo-entry-error" role="alert">{error}</p>}
      {mode === 'preparing' ? <div><p className="demo-preflight" role="status">{status}</p><button onClick={explore}>Cancel preparation</button></div> : <div className="demo-entry-actions">{mode === 'ready' ? <button className="demo-primary" onClick={start}>Start intro + guided demo</button> : <button className="demo-primary" onClick={prepare}>{mode === 'done' ? 'Replay intro + guided demo' : 'Prepare intro + guided demo'}</button>}<button onClick={explore}>Explore dataset</button></div>}
      <label className="demo-sound-choice"><input type="checkbox" checked={!muted} onChange={e => setMuted(!e.target.checked)} /> Music and interface sounds</label>
      {mode === 'done' && <><a className="demo-video-link" href="/demo/spherex-demo.mp4" download>Download presentation video ↓</a><output className="demo-run-log" hidden>{JSON.stringify({ duration: seconds, introSeconds: introDuration.current, totalSeconds: introDuration.current + seconds, actions: actions.current })}</output></>}
      <small>Scripted presentation of real app controls. The intro plays before the 30-second guided tour. Data preparation happens first. Esc exits.</small></div>
    </section>}
    {mode === 'intro' && <><div data-demo-state="intro" /><Intro autoStart timeScale={1.2} onDone={beginTour} /><button className="demo-intro-exit" onClick={explore}>Exit demo</button></>}
    {mode === 'explore' && <button className="demo-launch" onClick={prepare}>Demo mode · 30s</button>}
    {mode === 'playing' && <><div className="demo-topline" data-demo-state="playing" data-demo-scene={chapter}><span>VOYAGER / BARNARD’S STAR</span><span>{String(Math.floor(seconds)).padStart(2, '0')} / 30s</span><button onClick={() => { const next = !muted; setMuted(next); if (soundtrack.current) soundtrack.current.muted = next; if (clickSound.current) clickSound.current.muted = next }}>{muted ? 'Unmute' : 'Mute'}</button><button onClick={explore}>Exit demo</button><div className="demo-progress" style={{ width: `${seconds / 30 * 100}%` }} /></div>
      <aside className="demo-narration" aria-live="polite"><img src={sprite('deadpan').toDataURL()} alt="Voyager" /><div><p>VOYAGER <span>{String(chapter + 1).padStart(2, '0')} / {CHAPTERS[chapter][1]}</span></p><strong>{CHAPTERS[chapter][2]}</strong></div><div className="demo-audio-note" aria-hidden="true">♫</div></aside>
      <div className={`demo-cursor${clicking ? ' clicking' : ''}`} style={{ transform: `translate3d(${cursor.x}px,${cursor.y}px,0)` }} aria-hidden="true"><svg width="28" height="34" viewBox="0 0 28 34"><path d="M2 2L24 20L14 21L9 31Z" fill="#f2b705" stroke="#080a0b" strokeWidth="2" /></svg><i /></div>{frame && <div className="demo-capture-frame" style={frame} />}
    </>}
  </>
}
