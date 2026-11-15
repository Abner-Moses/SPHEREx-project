import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import DatasetWorkspace from './DatasetWorkspace.jsx'
import './styles.css'
import './shell.css'
import './deck.css'

function Root() {
  const [sky, setSky] = useState(false)
  return sky ? <><App /><button className="dw-return" onClick={() => setSky(false)}>← Dataset workspace</button></> : <DatasetWorkspace onSky={() => setSky(true)} />
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
