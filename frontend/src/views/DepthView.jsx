import { useEffect, useRef, useState } from 'react'
import { DepthEngine } from '../engine/depthEngine.js'
import NavBar3D, { HINTS } from '../components/NavBar3D.jsx'

export default function DepthView({ active, stars, landmarks, flattened, onCount, engineOut }) {
  const canvasRef = useRef(null)
  const labelsRef = useRef(null)
  const engineRef = useRef(null)
  const [ready, setReady] = useState(false)
  const [mode, setMode] = useState('orbit')

  useEffect(() => {
    const engine = new DepthEngine(canvasRef.current, labelsRef.current)
    engineRef.current = engine
    if (engineOut) engineOut.current = engine
    if (import.meta.env.DEV) window.__depth = engine // dev-only handle for checking the camera in the browser
    setReady(true)
    return () => {
      engine.dispose()
      engineRef.current = null
      if (engineOut) engineOut.current = null
      setReady(false)
    }
  }, [])

  useEffect(() => {
    if (!ready || !stars?.length) return
    const engine = engineRef.current
    engine.setData(stars, landmarks ?? [])
    onCount?.(engine.count)
  }, [ready, stars, landmarks, onCount])

  useEffect(() => { engineRef.current?.setFlattened(flattened) }, [flattened, ready])
  useEffect(() => { engineRef.current?.setActive(active) }, [active, ready])
  useEffect(() => { engineRef.current?.setMode(mode) }, [mode, ready])

  return (
    <div className="layer" aria-hidden={!active}>
      <canvas ref={canvasRef} className="gl" role="img" aria-label="3D map of bright stars at their true distances. Drag to orbit or look around, scroll to zoom, W A S D to move." />
      <div ref={labelsRef} className="labels" />
      {active && <NavBar3D mode={mode} onMode={setMode} onReset={() => engineRef.current?.resetView()} hint={HINTS[mode]} />}
    </div>
  )
}
