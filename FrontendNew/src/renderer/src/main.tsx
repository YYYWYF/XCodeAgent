import 'antd/dist/antd.less'
import './assets/main.less'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import { ApiProvider } from './context/ApiContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiProvider>
      <HashRouter>
        <App />
      </HashRouter>
    </ApiProvider>
  </StrictMode>
)
