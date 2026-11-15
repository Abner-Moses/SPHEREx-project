import { useCallback, useEffect, useRef, useState } from 'react'
import { logEvent, voyagerBus } from '../store.js'
import { askVoyager, offlineLine } from './api.js'
import { VoyagerEngine } from './engine.js'
import { rare } from '../eggs.js'

const GREETING = "Everything's up and running. Click me, or press V, when you want something explained."
// Los Palomos: asked about pigeons, Voyager tells this one itself (no model call, works offline)
const PIGEON_Q = /\b(pigeons?|palom(o|os|a|as)|doves?|columba)\b/i
const PIGEON_STORY = "Pigeons have a small but honest place in astronomy. In 1964, Penzias and Wilson at Bell Labs couldn't get rid of a hiss in their radio antenna. They blamed the pigeons nesting inside it, cleaned up after them and evicted them. The hiss stayed. It was the afterglow of the Big Bang, and it won them a Nobel Prize. The pigeons got nothing."
const PIGEON_LINE = "It's not mine."
// how long a line stays up on its own: time to type it, then ~0.5 s a word to read it (never under 10 s)
const readMs = (text) => Math.max(10000, text.length * 24 + 3000 + text.split(/\s+/).length * 500)
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/**
 * Voyager, the on-screen guide.
 *   getTargets()      -> [{ id, label }]   things on screen Voyager may fly to or point at
 *   resolveTarget(id) -> { x, y } | null   their current screen position
 *   getContext()      -> {...}             a compact snapshot of what the user is looking at
 * The model replies with one JSON command: { say, move, point, face }.
 */
export default function Voyager({ ready, humor, detail, getTargets, resolveTarget, getContext }) {
  const canvasRef = useRef(null)
  const bubbleRef = useRef(null)
  const hitRef = useRef(null)
  const textRef = useRef(null)
  const inputRef = useRef(null)
  const engine = useRef(null)
  const history = useRef([])
  const abort = useRef(null)
  const typer = useRef(0)
  const live = useRef({})
  live.current = { humor, detail, getTargets, resolveTarget, getContext }
  const [open, setOpen] = useState(false)
  const [shown, setShown] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [value, setValue] = useState('')
  const busy = useRef(false) // the user has engaged (asked or opened the box): don't auto-hide
  busy.current = open || thinking

  // keep the bubble and the click target next to the sprite without re-rendering React every frame
  const follow = useCallback((p) => {
    const b = bubbleRef.current
    const h = hitRef.current
    if (h) h.style.transform = `translate(${p.x - 30}px, ${p.y - 32}px)`
    if (!b) return
    const w = b.offsetWidth || 300
    const left = Math.max(12, Math.min(innerWidth - w - 12, p.x - w + 50))
    const hb = b.offsetHeight || 80
    // open upward in the lower part of the screen (where the clocks and toolbar live), downward elsewhere
    const top = p.y > innerHeight * 0.55 || p.y + 44 + hb > innerHeight - 12 ? p.y - 44 - hb : p.y + 44
    b.style.transform = `translate(${left}px, ${Math.max(60, top)}px)`
  }, [])

  useEffect(() => {
    const e = new VoyagerEngine(canvasRef.current, { onMove: follow, reduced: reducedMotion() })
    engine.current = e
    if (import.meta.env.DEV) window.__voyager = e
    return () => { e.dispose(); engine.current = null; clearInterval(typer.current); abort.current?.abort() }
  }, [follow])

  /** Type a line into the bubble (instant with reduced motion). */
  const say = useCallback((text) => {
    clearInterval(typer.current)
    setShown(true)
    const el = textRef.current
    if (!el) return
    const e = engine.current
    if (reducedMotion()) { el.textContent = text; return }
    let i = 0
    e?.setTalking(true)
    typer.current = setInterval(() => {
      i += 1
      el.textContent = text.slice(0, i)
      if (i >= text.length) { clearInterval(typer.current); e?.setTalking(false) }
    }, 24) // about 40 characters a second: close to reading pace, so the line never outruns the eye
  }, [])

  const target = (id) => (id ? () => live.current.resolveTarget(id) : null)

  /** Carry out one command from the model (or from a local fallback). */
  const run = useCallback(async ({ say: line, move, point, face = 'deadpan' }) => {
    const e = engine.current
    if (!e) return
    const moveTo = move || point
    if (moveTo && live.current.resolveTarget(moveTo)) await e.flyTo(target(moveTo), { face })
    else e.setFace(face)
    if (point && live.current.resolveTarget(point)) e.point(target(point))
    say(line)
  }, [say])

  const ask = useCallback(async (question, focus = null) => {
    const q = question.trim()
    if (!q) return
    abort.current?.abort()
    const ctl = new AbortController()
    abort.current = ctl
    const e = engine.current
    setShown(true)
    setThinking(true)
    if (textRef.current) textRef.current.textContent = ''
    e?.point(null)
    e?.setFace('side')
    // move toward the subject straight away, so the wait feels like travel rather than lag
    if (focus && live.current.resolveTarget(focus)) e?.flyTo(target(focus))
    const { humor: h, detail: d, getTargets, getContext } = live.current
    if (PIGEON_Q.test(q)) {
      setThinking(false)
      history.current = [...history.current, { role: 'user', content: q }, { role: 'assistant', content: PIGEON_STORY }].slice(-6)
      logEvent('v', 'voyager · pigeon protocol')
      run({ say: PIGEON_STORY, move: getContext()?.selected?.name === 'Columba' ? 'sel' : null, face: 'deadpan' })
      return
    }
    const targets = getTargets()
    let reply
    try {
      reply = await askVoyager({ question: q, humor: h, detail: d, targets, context: getContext(), history: history.current }, ctl.signal)
    } catch { return } // superseded by a newer question
    if (ctl.signal.aborted) return
    setThinking(false)
    if (reply.ok) {
      history.current = [...history.current, { role: 'user', content: q }, { role: 'assistant', content: reply.say }].slice(-6)
      logEvent('v', `voyager ${JSON.stringify({ move: reply.move, point: reply.point, face: reply.face })}`)
      run(reply)
    } else {
      logEvent('r', `voyager · ${reply.status === 503 ? 'model not configured' : reply.status ? `HTTP ${reply.status}` : 'archive offline'}`)
      const fact = focus ? localFact(getContext()) : ''
      run({ say: [fact, offlineLine(reply.status, reply.detail)].filter(Boolean).join(' '), move: focus, point: focus, face: 'unimpressed' })
    }
  }, [run])

  // requests from elsewhere in the UI
  useEffect(() => voyagerBus.subscribe(() => { const r = voyagerBus.get(); if (r) { setOpen(true); ask(r.question, r.focus) } }), [ask])

  // arrive once the intro is out of the way
  useEffect(() => {
    if (!ready || !engine.current) return
    const e = engine.current
    e.place({ x: -80, y: innerHeight * 0.55 })
    let t = 0
    e.flyTo(() => e.home(), { exact: true }).then(() => {
      say(GREETING)
      // the greeting is only a hello: let it go after a while so it never sits on top of cards
      t = setTimeout(() => { if (!busy.current && textRef.current?.textContent === GREETING) setShown(false) }, readMs(GREETING))
    })
    return () => clearTimeout(t)
  }, [ready, say])

  // Easter egg: very rarely, while Voyager is idle at home, a pigeon lands on its dish
  const visit = useCallback(async () => {
    const e = engine.current
    if (!e || !(await e.pigeonVisit())) return
    await new Promise((r) => setTimeout(r, 1100))
    if (!e.visitor) return
    e.setFace('unimpressed')
    if (!busy.current) say(PIGEON_LINE)
    await new Promise((r) => setTimeout(r, 4200))
    await e.pigeonLeave()
    e.setFace('deadpan')
    setTimeout(() => { if (!busy.current && textRef.current?.textContent === PIGEON_LINE) setShown(false) }, 1500)
  }, [say])
  useEffect(() => {
    if (!ready) return undefined
    if (import.meta.env.DEV) window.__pigeon = visit
    const iv = setInterval(() => {
      const e = engine.current
      if (!e || busy.current || e.flight || e.anchor || e.boom || document.hidden || !rare(0.03)) return
      if (textRef.current?.textContent && bubbleRef.current?.classList.contains('on')) return
      visit()
    }, 60000)
    return () => clearInterval(iv)
  }, [ready, visit])

  // V opens the ask box; Esc closes it
  useEffect(() => {
    const onKey = (ev) => {
      const tag = ev.target.tagName
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return
      if (ev.key.toLowerCase() === 'v' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') { ev.preventDefault(); setOpen(true); setShown(true) }
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 30) }, [open])
  useEffect(() => { if (engine.current) follow(engine.current.pos) }, [shown, open, thinking, follow])

  const close = () => { abort.current?.abort(); setOpen(false); setShown(false); setThinking(false); clearInterval(typer.current); engine.current?.setTalking(false); engine.current?.flyHome() }

  return (
    <>
      <canvas ref={canvasRef} className="voyager-canvas" aria-hidden="true" />
      <button ref={hitRef} type="button" className="voyager-hit" aria-label="Ask Voyager (V)" title="Ask Voyager (V)" onClick={() => { setOpen((o) => !o); setShown(true) }} />
      <div ref={bubbleRef} className={`voyager-bubble${shown ? ' on' : ''}`} role="dialog" aria-label="Voyager" aria-live="polite">
        <div className="vb-head"><span>Voyager</span><span>Humor {humor}%</span><button type="button" aria-label="Close Voyager" onClick={close}>×</button></div>
        <p className="vb-text">{thinking && <span className="vb-think">Thinking</span>}<span ref={textRef} /></p>
        {open && (
          <form className="vb-ask" onSubmit={(ev) => { ev.preventDefault(); ask(value); setValue('') }}>
            <span aria-hidden="true">›</span>
            <input ref={inputRef} value={value} onChange={(ev) => setValue(ev.target.value)} onKeyDown={(ev) => { if (ev.key === 'Escape') close() }} placeholder="ask about anything on screen" aria-label="Ask Voyager" maxLength={600} />
          </form>
        )}
      </div>
    </>
  )
}

/** One honest line from the context when the model is unreachable. */
function localFact(ctx) {
  const s = ctx?.selected
  if (!s) return ''
  const bits = [s.type ? `${s.name}, ${s.type.toLowerCase()}` : s.name]
  if (s.distance_ly) bits.push(`about ${s.distance_ly < 100 ? s.distance_ly.toFixed(1) : Math.round(s.distance_ly).toLocaleString()} light-years away`)
  return `${bits.join(', ')}.`
}
