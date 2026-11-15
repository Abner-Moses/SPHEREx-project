import { useEffect, useRef } from 'react'
import { runIntro } from './intro.js'
import './intro.css'

/** Full-screen intro overlay. The app renders (and loads its data) underneath while this plays. */
export default function Intro({ returning, onDone, autoStart = false, timeScale = 1, ...opts }) {
  const ref = useRef(null)
  const done = useRef(onDone)
  done.current = onDone
  useEffect(() => runIntro(ref.current, { returning, autoStart, timeScale, ...opts, onDone: () => done.current() }), [returning, autoStart, timeScale]) // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="intro" ref={ref} role="dialog" aria-modal="true" aria-label="SPHEREx intro" />
}
