import { useContext, type ReactElement } from 'react'
import { BrowserRouter, Link, Navigate, Route, Routes } from 'react-router-dom'
import { AppProviders } from '@/providers/AppProviders'
import { IdentityContext } from '@/platform/identity/IdentityContext'
import Home from './pages/Home'
import Chat from './pages/Chat'
import './g4.css'

/** 验收样例使用真实身份入口，登录按钮仅提供隔离测试资料，不代表服务端授权。 */
function Session(): ReactElement {
  const { identity, setIdentity } = useContext(IdentityContext)
  return <header><strong>G4 隔离验收 · 官方 Mock</strong>
    <nav><Link to="/page/home">业务首页</Link><Link to="/page/chat">独立助手</Link></nav>
    <span>{identity?.userName ?? '未登录'}</span>
    <button onClick={() => setIdentity({ userId: 'g4-a', userName: '测试用户 A' })}>登录 A</button>
    <button onClick={() => setIdentity({ userId: 'g4-b', userName: '测试用户 B' })}>登录 B</button>
    <button onClick={() => setIdentity(null)}>退出登录</button>
  </header>
}

/** 使用当前模板的真实 AppProviders，路由变化不重建其扩展 Provider。 */
export function App(): ReactElement {
  return <BrowserRouter><AppProviders><Session /><Routes>
    <Route path="/page/home" element={<Home />} />
    <Route path="/page/chat" element={<Chat />} />
    <Route path="*" element={<Navigate replace to="/page/home" />} />
  </Routes></AppProviders></BrowserRouter>
}
