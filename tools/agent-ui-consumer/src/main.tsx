import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles.css'
import './business.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing consumer root')
createRoot(root).render(<App />)
