import { Seg } from './ui.jsx'

const K = ({ children }) => <kbd>{children}</kbd>

/** On-canvas navigation for the 3D views: optional mode switch, reset, and a one-line key hint. */
export default function NavBar3D({ mode, onMode, onReset, hint }) {
  return (
    <div className="nav3d">
      {onMode && (
        <Seg small label="Camera" value={mode} onChange={onMode} options={[
          { id: 'orbit', label: 'Overview', hint: 'Orbit the Sun from outside' },
          { id: 'look', label: 'Look around', hint: 'Stand at the Sun and look out, like the sky map' },
        ]} />
      )}
      <button type="button" className="btn small ghost" onClick={onReset} title="Reset view (Home or R)">Reset view</button>
      <p className="nav3d-hint">{hint}</p>
    </div>
  )
}

export const HINTS = {
  orbit: <>Drag to orbit · Scroll to zoom · <K>W</K><K>A</K><K>S</K><K>D</K> move · <K>Q</K><K>E</K> down/up · arrows turn · <K>Shift</K> faster</>,
  look: <>Drag to look around · Scroll to zoom · <K>W</K><K>A</K><K>S</K><K>D</K> fly · <K>Q</K><K>E</K> down/up · arrows turn · <K>Shift</K> faster</>,
  surface: <>Drag to orbit · Scroll to zoom · <K>W</K><K>A</K><K>S</K><K>D</K> slide across · arrows turn · <K>Shift</K> faster</>,
}
