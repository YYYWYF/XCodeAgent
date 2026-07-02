import {
  CaretDownOutlined,
  CaretRightOutlined,
  CodeOutlined,
  DeleteOutlined,
  DeploymentUnitOutlined,
  FileOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoreOutlined,
  PlusOutlined,
  SearchOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Button, Dropdown, Menu, Modal } from 'antd'
import type { MenuProps } from 'antd'
import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useApi } from '../../context/ApiContext'
import type { TaskMenuItem } from '../../types/task'
import './Sidebar.less'

type RouteMenuItem = {
  key: string
  icon: React.ReactNode
  label: string
}

type SidebarProps = {
  collapsed: boolean
  onToggleCollapse: () => void
}

const topMenuItems: RouteMenuItem[] = [
  {
    key: '/new-task',
    icon: <PlusOutlined />,
    label: '新建任务'
  },
  {
    key: '/agents',
    icon: <CodeOutlined />,
    label: 'Agent 指令'
  },
  {
    key: '/files',
    icon: <FileOutlined />,
    label: '文件'
  },
  {
    key: '/sub-agent',
    icon: <DeploymentUnitOutlined />,
    label: 'SubAgent'
  },
  {
    key: '/skill',
    icon: <ThunderboltOutlined />,
    label: 'Skill'
  }
]

const searchMenuItems: RouteMenuItem[] = [
  {
    key: '/search',
    icon: <SearchOutlined />,
    label: '搜索任务'
  }
]

const toMenuItems = (items: RouteMenuItem[]): MenuProps['items'] =>
  items.map((item) => ({
    key: item.key,
    icon: item.icon,
    label: item.label
  }))

function Sidebar({ collapsed, onToggleCollapse }: SidebarProps): React.JSX.Element {
  const api = useApi()
  const location = useLocation()
  const navigate = useNavigate()
  const [defaultProjectExpanded, setDefaultProjectExpanded] = useState(true)
  const [projectTasks, setProjectTasks] = useState<TaskMenuItem[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [tasksError, setTasksError] = useState('')
  const selectedKey = location.pathname.startsWith('/task/') ? '' : location.pathname

  useEffect(() => {
    let isActive = true

    const loadProjectTasks = async (): Promise<void> => {
      setTasksLoading(true)
      setTasksError('')

      try {
        const tasks = await api.tasks.list()

        if (isActive) {
          setProjectTasks(tasks)
        }
      } catch (error) {
        console.error(error)

        if (isActive) {
          setTasksError('任务加载失败')
          setProjectTasks([])
        }
      } finally {
        if (isActive) {
          setTasksLoading(false)
        }
      }
    }

    void loadProjectTasks()

    return () => {
      isActive = false
    }
  }, [api])

  const handleMenuClick: MenuProps['onClick'] = ({ key }) => {
    navigate(String(key))
  }

  const toggleDefaultProject = (): void => {
    setDefaultProjectExpanded((currentExpanded) => !currentExpanded)
  }

  const deleteProjectTask = (taskId: string): void => {
    setProjectTasks((currentTasks) => currentTasks.filter((task) => task.id !== taskId))

    if (location.pathname === `/task/${taskId}`) {
      navigate('/new-task')
    }
  }

  const confirmDeleteProjectTask = (task: TaskMenuItem): void => {
    Modal.confirm({
      title: '确认删除项目？',
      content: `删除后将从默认项目列表中移除「${task.title}」。`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteProjectTask(task.id)
    })
  }

  const getTaskActionMenu = (task: TaskMenuItem): MenuProps => ({
    items: [
      {
        danger: true,
        icon: <DeleteOutlined />,
        key: 'delete',
        label: '删除项目'
      }
    ],
    onClick: ({ domEvent }) => {
      domEvent.stopPropagation()
      confirmDeleteProjectTask(task)
    }
  })

  return (
    <aside className="sidebar-layout">
      <div className="sidebar-brand">{collapsed ? 'XA' : 'XcodeAgent'}</div>
      <div className="sidebar-scroll">
        <Menu
          className="sidebar-menu"
          inlineCollapsed={collapsed}
          items={toMenuItems(topMenuItems)}
          mode="inline"
          selectedKeys={selectedKey ? [selectedKey] : []}
          onClick={handleMenuClick}
        />
        <div className="sidebar-divider" />
        <Menu
          className="sidebar-menu"
          inlineCollapsed={collapsed}
          items={toMenuItems(searchMenuItems)}
          mode="inline"
          selectedKeys={selectedKey === '/search' ? ['/search'] : []}
          onClick={handleMenuClick}
        />
        <button
          aria-expanded={defaultProjectExpanded}
          className="project-header"
          type="button"
          onClick={toggleDefaultProject}
        >
          <span className="project-title">
            {defaultProjectExpanded ? <CaretDownOutlined /> : <CaretRightOutlined />}
            {!collapsed && '默认项目'}
          </span>
        </button>
        {defaultProjectExpanded ? (
          <nav aria-label="默认项目任务" className="task-list">
            {tasksLoading ? <div className="task-list-state">加载中...</div> : null}
            {!tasksLoading && tasksError ? (
              <div className="task-list-state task-list-state--error">{tasksError}</div>
            ) : null}
            {!tasksLoading && !tasksError && projectTasks.length === 0 ? (
              <div className="task-list-state">暂无任务</div>
            ) : null}
            {!tasksLoading && !tasksError
              ? projectTasks.map((task) => (
                  <div className="task-item" key={task.id}>
                    <NavLink className="task-link" to={`/task/${task.id}`}>
                      <span className="task-title" title={task.title}>
                        {task.title}
                      </span>
                      <span aria-hidden className={`task-dot ${task.statusTone}`} />
                    </NavLink>
                    <Dropdown
                      menu={getTaskActionMenu(task)}
                      overlayClassName="task-action-dropdown"
                      placement="bottomRight"
                      trigger={['click']}
                    >
                      <Button
                        aria-label={`打开${task.title}操作菜单`}
                        className="task-action-button"
                        icon={<MoreOutlined />}
                        type="text"
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                        }}
                      />
                    </Dropdown>
                  </div>
                ))
              : null}
          </nav>
        ) : null}
      </div>
      <div className="sidebar-collapse-button">
        <Button
          aria-label={collapsed ? '展开菜单' : '收起菜单'}
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          title={collapsed ? '展开菜单' : '收起菜单'}
          type="text"
          onClick={onToggleCollapse}
        />
      </div>
    </aside>
  )
}

export default Sidebar
