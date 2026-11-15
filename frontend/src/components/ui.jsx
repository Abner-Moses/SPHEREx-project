import { useId } from 'react'
import { GLOSSARY } from '../content.js'

/** Segmented control. options: [{ id, label, hint? }] */
export function Seg({ value, onChange, options, label, small }) {
  return (
    <div className={`seg${small ? ' small' : ''}`} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} className={value === o.id ? 'on' : ''} title={o.hint} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Section({ title, children, aside }) {
  return (
    <section className="section">
      <header>
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  )
}

export function Slider({ label, value, min, max, step = 1, onChange, format = (v) => v }) {
  const id = useId()
  return (
    <div className="slider">
      <label htmlFor={id}>
        <span>{label}</span>
        <output>{format(value)}</output>
      </label>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  )
}

export function Check({ label, checked, onChange }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="box" aria-hidden="true" />
      {label}
    </label>
  )
}

/** Inline jargon with a hover/focus explanation (accessible: focusable, described by the tip). */
export function Term({ k, children }) {
  const id = useId()
  const tip = GLOSSARY[k]
  if (!tip) return children
  return (
    <span className="term" tabIndex={0} aria-describedby={id}>
      {children}
      <span role="tooltip" id={id} className="tip">{tip}</span>
    </span>
  )
}

export function Stat({ label, value, unit, wide }) {
  return (
    <div className={`stat${wide ? ' wide' : ''}`}>
      <dt>{label}</dt>
      <dd>
        {value}
        {unit && <small>{unit}</small>}
      </dd>
    </div>
  )
}

export function Badge({ tone, children, title }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>
}

export const Icon = {
  sky: <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c3 2.6 3 14.4 0 17M12 3.5c-3 2.6-3 14.4 0 17" /></svg>,
  depth: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12l8-4.5M12 12L4 7.5M12 12v9" /></svg>,
  detector: <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M4 9.5h16M4 14.5h16M9.5 4v16M14.5 4v16" /></svg>,
  surface: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 16l5-6 4 4 4-8 5 7" /><path d="M3 20h18" /></svg>,
  close: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>,
  target: <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7" /><path d="M12 2v5M12 17v5M2 12h5M17 12h5" /></svg>,
  info: <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></svg>,
  chart: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20V4M4 20h16" /><path d="M8 16l3-5 3 3 4-7" /></svg>,
  chat: <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z" /></svg>,
}
