import 'antd/dist/antd.less'
import './assets/main.less'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiProvider } from './context/ApiContext'
import LoginPage from './pages/LoginPage/LoginPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ApiProvider>
      <LoginPage />
    </ApiProvider>
  </StrictMode>
)
