import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import PresentationRoot from './Presentation.jsx'
import './styles.css'
import './shell.css'
import './deck.css'

// The website is the Sky Explorer. The 30-second presentation film plays only at ?demo (or ?record for recording).
const params = new URLSearchParams(window.location.search)
const presentation = params.has('demo') || params.has('record')

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {presentation ? <PresentationRoot /> : <App />}
  </React.StrictMode>,
)
