import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import PresentationRoot from './Presentation.jsx'
import './styles.css'
import './shell.css'
import './deck.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <PresentationRoot />
  </React.StrictMode>,
)
