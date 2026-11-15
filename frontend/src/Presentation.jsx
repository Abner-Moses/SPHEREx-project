import { useEffect, useRef, useState } from 'react'
import App from './App.jsx'
import DatasetWorkspace from './DatasetWorkspace.jsx'
import Intro from './intro/Intro.jsx'
import { drawPigeon } from './eggs.js'
import { sprite } from './voyager/sprite.js'
import './Presentation.css'

// The 30-second submission film:
//   title  8.4 s  original intro, film seconds 24.0–32.4 (SPHEREx title lands, holds; no pigeon here)
//   boot  ~2.1 s  essential boot lines only, at 1.5x
//   tour   16 s   sky → capture → science graphs → dataset table → temporal blink → 102-channel spectrum
//   end   ~3.4 s  four pigeons (the four Los Palomos) land on the title rule
const INTRO_CUT = { from: 24.0, to: 32.4 }
const BOOT_SCALE = 1.5
const TOUR = 16
const SIGNOFF = 3.4
const FILM = 30
const CHAPTERS = [
  [0, 'Barnard’s Star · infrared sky', 'A calibrated SPHEREx image, placed on the real sky.'],
  [1.8, 'Capture', 'Frame any star. It is identified from real catalogues.'],
  [3.2, 'Science', 'SPHEREx measurements become interactive WebGL graphs.'],
  [6.8, 'Dataset', 'Every local SPHEREx observation and spectral cube, per target.'],
  [9.0, 'Temporal blink', 'Real observations of the same field, aligned and blinked.'],
  [12.8, '102-channel spectrum', 'Every channel of the spectral cube, exactly as measured.'],
]
const RECORDING = typeof location !== 'undefined' && new URLSearchParams(location.search).has('record')
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const byText = (text, root = document) => [...(root || document).querySelectorAll('button')].find((el) => el.textContent.trim() === text)
const byLabel = (label) => document.querySelector(`[aria-label="${label}"]`)

/** One audio graph for the whole film: intro synth, music bed, clicks and sign-off all play into `master`,
 *  which feeds the speakers and a MediaStreamDestination that MediaRecorder captures (for the MP4). */
function makeGraph() {
  const ctx = new (window.AudioContext || window.webkitAudioContext)()
  const master = ctx.createGain()
  master.connect(ctx.destination)
  const dest = ctx.createMediaStreamDestination()
  master.connect(dest)
  const music = ctx.createGain()
  music.gain.value = 0
  music.connect(master)
  return { ctx, master, dest, music, noise: null }
}
function flutter(g, t, pan = 0, vol = 0.06) {
  const { ctx } = g
  if (!g.noise) { const b = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.06), ctx.sampleRate), d = b.getChannelData(0); for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * Math.sin((Math.PI * i) / d.length) ** 2; g.noise = b }
  for (let i = 0; i < 6; i++) {
    const s = ctx.createBufferSource(), bp = ctx.createBiquadFilter(), gain = ctx.createGain(), p = ctx.createStereoPanner()
    s.buffer = g.noise; bp.type = 'bandpass'; bp.frequency.value = 900 + Math.random() * 700; bp.Q.value = 0.9
    gain.gain.value = vol * (1 - i / 8); p.pan.value = Math.max(-1, Math.min(1, pan + i * 0.05))
    s.connect(bp).connect(gain).connect(p).connect(g.master); s.start(t + i * (0.09 - i * 0.004))
  }
}
function organ(g, freqs, t, dur, vol = 0.1) {
  const { ctx } = g
  for (const f of freqs) for (const [m, a] of [[1, 1], [2, 0.5], [3, 0.22], [4, 0.14]]) {
    const o = ctx.createOscillator(), gain = ctx.createGain()
    o.frequency.value = f * m
    gain.gain.setValueAtTime(0, t); gain.gain.linearRampToValueAtTime((vol * a) / freqs.length, t + 0.5); gain.gain.setValueAtTime((vol * a) / freqs.length, t + dur - 0.9); gain.gain.linearRampToValueAtTime(0, t + dur)
    o.connect(gain).connect(g.master); o.start(t); o.stop(t + dur + 0.05)
  }
}

/** Four pigeons — the four team members — land one after another on the amber rule. */
function SignOff({ graph, onDone }) {
  const cv = useRef(null)
  const [text, setText] = useState(false)
  useEffect(() => {
    const c = cv.current, dpr = Math.min(2, devicePixelRatio || 1), W = innerWidth, H = innerHeight
    c.width = W * dpr; c.height = H * dpr
    const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.imageSmoothingEnabled = false
    const S = Math.max(4, Math.round(W / 260)), ruleY = Math.round(H * 0.46), ruleW = Math.round(W * 0.34)
    const slots = [-1.5, -0.5, 0.5, 1.5].map((k) => Math.round(W / 2 + k * ruleW * 0.24 - 4.5 * S))
    const t0 = performance.now()
    if (graph) { const n = graph.ctx.currentTime; slots.forEach((_, i) => flutter(graph, n + 0.05 + i * 0.28, -0.6 + i * 0.4)); organ(graph, [110, 164.81, 220, 261.63], n + 0.35, SIGNOFF - 0.2, 0.12) }
    let raf = 0
    const draw = (now) => {
      const k = (now - t0) / 1000
      g.fillStyle = '#050608'; g.fillRect(0, 0, W, H)
      const grow = Math.min(1, k / 0.5)
      g.fillStyle = '#f2b705'; g.fillRect(W / 2 - (ruleW * grow) / 2, ruleY, ruleW * grow, Math.max(2, S / 2))
      slots.forEach((x, i) => {
        const land = (k - i * 0.28) / 0.6
        if (land < 0) return
        const y0 = ruleY - 6 * S
        if (land < 1) { const e = 1 - (1 - land) ** 3; drawPigeon(g, Math.floor(k * 12) % 2 ? 'up' : 'down', x + (1 - e) * (W * 0.55 + i * 40), y0 - (1 - e) * H * 0.5 - Math.sin(Math.PI * e) * 30, S, i % 2 === 1) }
        else drawPigeon(g, Math.floor((k - i * 0.4) * 2.4) % 3 === 1 ? 'bob' : 'perched', x, y0, S, i % 2 === 1)
      })
      if (k > 1.4 && !text) setText(true)
      if (k < SIGNOFF) raf = requestAnimationFrame(draw); else onDone()
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="demo-signoff" data-demo-state="signoff"><canvas ref={cv} style={{ width: '100vw', height: '100vh' }} /><p className={text ? 'on' : ''}>A LOS PALOMOS TRANSMISSION</p></div>
}

export default function PresentationRoot() {
  const [mode, setMode] = useState('choose'), [sky, setSky] = useState(false)
  const [prepared, setPrepared] = useState(false), [status, setStatus] = useState(''), [error, setError] = useState('')
  const [seconds, setSeconds] = useState(0), [chapter, setChapter] = useState(0), [muted, setMuted] = useState(false)
  const [cursor, setCursor] = useState({ x: 180, y: 140 }), [clicking, setClicking] = useState(false), [frame, setFrame] = useState(null)
  const dataset = useRef(null), skyApi = useRef(null), soundtrack = useRef(null), clickSound = useRef(null), graph = useRef(null), recorder = useRef(null)
  const live = useRef(false), run = useRef(0), pointer = useRef({ x: 180, y: 140 }), timers = useRef([]), actions = useRef([])
  const avatar = useRef(null), tourStart = useRef(0), filmStart = useRef(0), introDuration = useRef(0)
  useEffect(() => { if (avatar.current) avatar.current.src = sprite('deadpan').toDataURL() }, [mode])
  useEffect(() => () => { live.current = false; run.current++; soundtrack.current?.pause(); timers.current.forEach(clearTimeout) }, [])
  const delay = (ms, fn) => { const id = setTimeout(() => { if (live.current) fn() }, ms); timers.current.push(id) }
  function fadeMusic(to, sec) { const g = graph.current; if (!g) return; const n = g.ctx.currentTime; g.music.gain.cancelScheduledValues(n); g.music.gain.setValueAtTime(g.music.gain.value, n); g.music.gain.linearRampToValueAtTime(to, n + sec) }
  function stopRecording() { const r = recorder.current; if (r && r.state === 'recording') { window.__demoMarks = { ...(window.__demoMarks || {}), endMs: Date.now() }; r.stop() } }
  function stop() { live.current = false; run.current++; timers.current.forEach(clearTimeout); timers.current = []; fadeMusic(0, 0.3); setTimeout(() => soundtrack.current?.pause(), 350); dataset.current?.play(false); skyApi.current?.closeCapture(); setFrame(null); stopRecording() }
  useEffect(() => { const key = (e) => { if (e.key === 'Escape' && ['intro', 'playing', 'signoff'].includes(mode)) { stop(); setMode('done') } }; window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key) }, [mode])
  function sound() { if (clickSound.current && !muted) { clickSound.current.currentTime = 0; clickSound.current.play().catch(() => {}) } }
  async function move(x, y, duration = 620) {
    const token = run.current, start = performance.now(), from = { ...pointer.current }
    return new Promise((resolve) => {
      const tick = (now) => { if (!live.current || token !== run.current) { resolve(); return }
        const t = Math.min(1, (now - start) / duration), e = t * t * (3 - 2 * t)
        const p = { x: from.x + (x - from.x) * e + Math.sin(t * Math.PI) * 13, y: from.y + (y - from.y) * e - Math.sin(t * Math.PI) * 22 }
        pointer.current = p; setCursor(p); if (t < 1) requestAnimationFrame(tick); else resolve()
      }; requestAnimationFrame(tick)
    })
  }
  async function point(el, { click = true, scroll = true, duration } = {}) {
    if (!el || !live.current) return
    if (scroll) { el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' }); await sleep(380) }
    const r = el.getBoundingClientRect(); await move(r.x + r.width * 0.53, r.y + r.height * 0.52, duration)
    if (!live.current) return
    if (click) { setClicking(true); sound(); el.click(); actions.current.push({ at: (performance.now() - tourStart.current) / 1000, text: el.textContent.trim() || el.getAttribute('aria-label') }); delay(190, () => setClicking(false)) }
  }
  function smoothScroll(el, to, ms) { if (!el) return; const from = el.scrollTop, start = performance.now(), token = run.current; const f = (now) => { if (!live.current || token !== run.current) return; const t = Math.min(1, (now - start) / ms); el.scrollTop = from + (to - from) * (t * t * (3 - 2 * t)); if (t < 1) requestAnimationFrame(f) }; requestAnimationFrame(f) }
  async function waitFor(fn, label, timeout = 45000) {
    const start = performance.now()
    while (!fn()) { if (performance.now() - start > timeout) throw new Error(`${label} did not become ready. Check the local backend and dataset.`); await sleep(120) }
  }
  async function prepare() {
    stop(); const token = run.current; dataset.current?.select('barnards-star'); setError(''); setMode('preparing'); setSky(false); setPrepared(false)
    try {
      setStatus('Loading Barnard’s Star measurements and the sky catalogue…')
      await waitFor(() => dataset.current?.ready && skyApi.current?.ready, 'Local data'); if (token !== run.current) return
      setStatus('Preparing genuine WCS-aligned epochs and a calibrated sky image…')
      await Promise.all([dataset.current.prepare(), skyApi.current.prepare(), import('./components/Capture.jsx'), import('./components/AnalysisDeck.jsx')])
      await waitFor(() => dataset.current?.sequenceReady && skyApi.current?.imageReady, 'Real image products'); if (token !== run.current) return
      await Promise.all([...document.querySelectorAll('.dw-image-stage img')].map((img) => img.decode().catch(() => {})))
      const track = new Audio('/demo/audio/presentation.mp3'); track.preload = 'auto'; soundtrack.current = track
      clickSound.current = new Audio('/demo/audio/click.ogg')
      await new Promise((resolve, reject) => { track.oncanplaythrough = resolve; track.onerror = () => reject(new Error('Presentation soundtrack unavailable.')); track.load() })
      if (token !== run.current) return; dataset.current.tab('photometry'); skyApi.current.focus()
      document.querySelector('.dataset-workspace')?.scrollTo({ top: 0 })
      setStatus('Real data, images and soundtrack are ready.'); setPrepared(true); setMode('ready')
    } catch (e) { if (token === run.current) { setError(e.message); setMode('choose') } }
  }
  function start() {
    if (!prepared) return
    run.current++; live.current = true; actions.current = []; setSky(true); setSeconds(0); setChapter(0)
    // the audio graph must be created inside this click so the browser lets it play
    if (!graph.current) {
      const g = graph.current = makeGraph()
      const src = g.ctx.createMediaElementSource(soundtrack.current); src.connect(g.music)
      const cs = g.ctx.createMediaElementSource(clickSound.current); const cg = g.ctx.createGain(); cg.gain.value = 0.32; cs.connect(cg).connect(g.master)
    }
    const g = graph.current
    g.ctx.resume(); g.master.gain.value = muted ? 0 : 1; g.music.gain.value = 0
    soundtrack.current.currentTime = 0; soundtrack.current.loop = true
    try {
      const rec = new MediaRecorder(g.dest.stream, { mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '' })
      const chunks = []
      rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
      rec.onstop = () => { window.__demoAudio = new Blob(chunks, { type: rec.mimeType }) }
      rec.start(250); recorder.current = rec
    } catch { recorder.current = null }
    window.__demoAudio = null
    window.__demoMarks = { startMs: Date.now() }
    filmStart.current = performance.now()
    setMode('intro')
  }
  function beginTour() {
    if (!live.current) return
    setSky(true); setMode('playing'); setSeconds(0); setChapter(0)
    introDuration.current = (performance.now() - filmStart.current) / 1000; tourStart.current = performance.now()
    window.__demoMarks = { ...window.__demoMarks, tourMs: Date.now() }
    soundtrack.current.play().catch(() => {}); fadeMusic(0.9, 0.6)
    const origin = tourStart.current, token = run.current
    const clock = (now) => { if (!live.current || token !== run.current) return
      const time = Math.min(TOUR, (now - origin) / 1000); setSeconds(time)
      setChapter(CHAPTERS.reduce((last, item, i) => (time >= item[0] ? i : last), 0))
      if (time < TOUR) requestAnimationFrame(clock); else signoff()
    }; requestAnimationFrame(clock)
    // 0–1.8 s  the sky: Barnard's Star with its calibrated SPHEREx image
    delay(200, () => { const c = document.querySelector('.demo-sky-pane canvas.gl')?.getBoundingClientRect(); if (c) move(c.x + c.width * 0.62, c.y + c.height * 0.42, 1400) })
    // 1.8–3.2 s  capture: frame the star, real screenshot, then Analyze
    delay(1800, async () => {
      await point(byLabel('Capture a star or body (C)'), { scroll: false, duration: 420 }); await sleep(120)
      const rect = document.querySelector('.demo-sky-pane canvas.gl')?.getBoundingClientRect(); if (!rect || !live.current) return
      const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2
      await move(cx - 100, cy - 85, 200); setFrame({ left: cx - 100, top: cy - 85, width: 200, height: 170 }); setClicking(true)
      await move(cx + 100, cy + 85, 340); setClicking(false); sound(); skyApi.current.frame({ x0: cx - 100, y0: cy - 85, x1: cx + 100, y1: cy + 85 }); setFrame(null)
      actions.current.push({ at: (performance.now() - tourStart.current) / 1000, text: 'Capture screenshot' })
    })
    delay(3200, () => point(byText('Analyze · science'), { scroll: false, duration: 380 }))
    // 4.2–6.6 s  science graphs: slow scroll through the analysis deck
    delay(4300, () => { const grid = document.querySelector('.da-grid'); if (!grid) return; smoothScroll(grid, Math.min(900, grid.scrollHeight - grid.clientHeight), 2200); const r = grid.getBoundingClientRect(); move(r.x + r.width * 0.7, r.y + r.height * 0.45, 900) })
    // 6.8–8.8 s  the dataset: target table with local observation and cube counts
    delay(6700, () => { byLabel('Close analysis')?.click(); skyApi.current.closeCapture(); setSky(false); const ws = document.querySelector('.dataset-workspace'); if (ws) ws.scrollTop = 0 })
    delay(7000, async () => {
      const table = document.querySelector('table[aria-label="Local target datasets"]'); if (!table) return
      table.scrollIntoView({ block: 'center' }); await sleep(60)
      const rows = [...table.querySelectorAll('tbody tr')]
      for (const r of rows.slice(0, 5)) { const b = r.getBoundingClientRect(); await move(b.x + b.width * 0.55, b.y + b.height / 2, 300) }
    })
    // 9.0–12.6 s  temporal blink of real aligned frames
    delay(9000, async () => { const play = document.querySelector('.dw-player button:nth-child(2)'); await point(play, { duration: 420 }); if (live.current && !/Pause/.test(play?.textContent || '')) dataset.current.play(true) })
    // 12.8–16 s  the 102-channel cube table, scrolled slowly
    delay(12800, async () => {
      dataset.current.play(false); await point(byText(`Cube spectrum (${document.querySelector('.dw-tabs button:nth-child(2)')?.textContent.match(/\d+/)?.[0] ?? '102'})`) || document.querySelector('.dw-tabs button:nth-child(2)'), { duration: 380 })
      await sleep(250); const t = document.querySelector('table[aria-label="102 channel cube spectrum"]')?.parentElement; if (t) { smoothScroll(t, 600, 2400); const r = t.getBoundingClientRect(); move(r.x + r.width * 0.5, r.y + r.height * 0.6, 900) }
    })
  }
  function signoff() {
    if (!live.current) return
    dataset.current?.play(false); fadeMusic(0, 1.4); setMode('signoff')
  }
  function finish() {
    window.__demoMarks = { ...window.__demoMarks, totalS: (performance.now() - filmStart.current) / 1000, introS: introDuration.current }
    stop(); setMode('done')
  }

  function explore() { stop(); setSky(false); setMode('explore') }
  const mounted = mode !== 'choose'
  return <>
    {mounted && <><div className={`demo-dataset-pane${mode === 'playing' ? ' presentation-playing' : ''}${sky ? ' hidden' : ''}`}><DatasetWorkspace presentationRef={dataset} onSky={() => setSky(true)} /></div><div className={`demo-sky-pane${mode === 'playing' ? ' presentation-playing' : ''}${sky ? '' : ' hidden'}`}><App presentation={mode !== 'explore'} presentationRef={skyApi} /><button className="dw-return" onClick={() => setSky(false)}>← Dataset workspace</button></div></>}
    {['choose', 'preparing', 'ready', 'done'].includes(mode) && <section className="demo-entry" data-demo-state={mode} role="dialog" aria-label="Choose exploration or presentation">
      <div className="demo-entry-card"><p className="demo-kicker">SPHEREX / MISSION PRESENTATION</p><img ref={avatar} className="demo-entry-voyager" alt="Voyager guide" /><h1>{mode === 'done' ? 'Real data. A clear view.' : 'From the sky to the science.'}</h1><p>{mode === 'done' ? 'The presentation is complete. Continue exploring the measured data.' : 'A 30-second film: the SPHEREx title, a short boot, a guided tour from the sky into measured data and blinking observations, and the Los Palomos sign-off.'}</p>
      <div className="demo-entry-facts"><span>30 seconds in total</span><span>Real local SPHEREx data</span><span>Music · captions · natural cursor</span></div>
      {error && <p className="demo-entry-error" role="alert">{error}</p>}
      {mode === 'preparing' ? <div><p className="demo-preflight" role="status">{status}</p><button onClick={explore}>Cancel preparation</button></div> : <div className="demo-entry-actions">{mode === 'ready' ? <button className="demo-primary" onClick={start}>Start the 30-second film</button> : <button className="demo-primary" onClick={prepare}>{mode === 'done' ? 'Replay the 30-second film' : 'Prepare the 30-second film'}</button>}<button onClick={explore}>Explore dataset</button></div>}
      <label className="demo-sound-choice"><input type="checkbox" checked={!muted} onChange={(e) => setMuted(!e.target.checked)} /> Music and interface sounds</label>
      {mode === 'done' && <><a className="demo-video-link" href="/demo/spherex-demo.mp4" download>Download the film (MP4) ↓</a><output className="demo-run-log" hidden>{JSON.stringify({ tour: seconds, introSeconds: introDuration.current, marks: typeof window !== 'undefined' ? window.__demoMarks : null, actions: actions.current })}</output></>}
      <small>Scripted presentation of real app controls on real data. Preparation happens first. Esc exits.</small></div>
    </section>}
    {mode === 'intro' && <><div data-demo-state="intro" /><Intro autoStart timeScale={1} cut={INTRO_CUT} bootScale={BOOT_SCALE} audioCtx={graph.current?.ctx} audioOut={graph.current?.master} onDone={beginTour} />{!RECORDING && <button className="demo-intro-exit" onClick={explore}>Exit demo</button>}</>}
    {mode === 'signoff' && <SignOff graph={graph.current} onDone={finish} />}
    {mode === 'explore' && <button className="demo-launch" onClick={prepare}>Demo film · 30s</button>}
    {mode === 'playing' && <>{!RECORDING && <div className="demo-topline" data-demo-state="playing" data-demo-scene={chapter}><span>VOYAGER / BARNARD’S STAR</span><span>{String(Math.floor(seconds + introDuration.current)).padStart(2, '0')} / {FILM}s</span><button onClick={() => { const next = !muted; setMuted(next); if (graph.current) graph.current.master.gain.value = next ? 0 : 1 }}>{muted ? 'Unmute' : 'Mute'}</button><button onClick={explore}>Exit demo</button><div className="demo-progress" style={{ width: `${((seconds + introDuration.current) / FILM) * 100}%` }} /></div>}
      {RECORDING && <div data-demo-state="playing" data-demo-scene={chapter} hidden />}
      <aside className="demo-narration" aria-live="polite"><img src={sprite('deadpan').toDataURL()} alt="Voyager" /><div><p>VOYAGER <span>{String(chapter + 1).padStart(2, '0')} / {CHAPTERS[chapter][1]}</span></p><strong>{CHAPTERS[chapter][2]}</strong></div><div className="demo-audio-note" aria-hidden="true">♫</div></aside>
      <div className={`demo-cursor${clicking ? ' clicking' : ''}`} style={{ transform: `translate3d(${cursor.x}px,${cursor.y}px,0)` }} aria-hidden="true"><svg width="28" height="34" viewBox="0 0 28 34"><path d="M2 2L24 20L14 21L9 31Z" fill="#f2b705" stroke="#080a0b" strokeWidth="2" /></svg><i /></div>{frame && <div className="demo-capture-frame" style={frame} />}
    </>}
  </>
}
