import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import '@react-sigma/core/lib/style.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
