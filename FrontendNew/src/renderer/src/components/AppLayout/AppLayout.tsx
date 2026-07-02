import { Layout } from 'antd'
import { useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from '../Sidebar/Sidebar'
import TopBar from '../TopBar/TopBar'
import './AppLayout.less'

const { Content, Sider } = Layout

function AppLayout(): React.JSX.Element {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const isTaskRoute = location.pathname.startsWith('/task/')
  const isNewTaskRoute = location.pathname === '/new-task'
  const contentClassName = [
    'app-content',
    isTaskRoute ? 'app-content--workspace' : '',
    isNewTaskRoute ? 'app-content--new-task' : ''
  ]
    .filter(Boolean)
    .join(' ')

  const toggleCollapsed = (): void => {
    setCollapsed((currentCollapsed) => !currentCollapsed)
  }

  return (
    <Layout className="app-shell">
      <Sider
        className="app-sidebar"
        collapsed={collapsed}
        collapsedWidth={72}
        collapsible
        theme="light"
        trigger={null}
        width={250}
      >
        <Sidebar collapsed={collapsed} onToggleCollapse={toggleCollapsed} />
      </Sider>
      <Layout className="app-main">
        <TopBar />
        <Content className={contentClassName}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}

export default AppLayout
