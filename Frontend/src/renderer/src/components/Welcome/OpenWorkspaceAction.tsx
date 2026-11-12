import { FolderAddOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Button, Empty, List, message, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import {
  canListSessionWorkspaces,
  listSessionWorkspaces,
  type SessionWorkspaceSummary
} from '../../service/chatSessions'
import {
  loadStoredApplications,
  loadWorkspaceApplicationConfig,
  applicationIndexOf,
  saveStoredApplications
} from '../../service/applicationStorage'
import { ApplicationLifecycleActionError, getApplicationLifecycleSummary } from '../../service/applicationLifecycle'
import type { ApplicationConfig, ApplicationSchemaConfig } from '../../typings'
import { cx } from '../../utils'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import './WelcomeModal.less'
import './WorkspaceHistoryModal.less'
import {
  createApplicationId,
  formatError,
  formatHistoryTime,
  pathBasename,
  pathDirname
} from './utils'

const { Text } = Typography

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
  theme: 'dark' | 'light'
}

type WorkspaceHistoryEntry = SessionWorkspaceSummary & {
  application: ApplicationConfig
}

/** 将工作区内的正式 application.json 恢复为可写入首页索引的应用记录。 */
function applicationFromWorkspace(
  schema: ApplicationSchemaConfig,
  workspaceRoot: string
): ApplicationConfig {
  const workspaceName = pathBasename(workspaceRoot)
  const applicationName = schema.appName.trim() || workspaceName
  return {
    ...schema,
    id: createApplicationId(),
    name: applicationName,
    workspaceRoot,
    projectParentPath: pathDirname(workspaceRoot),
    projectDirectoryName: workspaceName,
    source: 'existing-workspace',
    audience: 'developer',
    legacyTheme: 'light',
    legacyLayout: 'login-admin',
    enableTabs: false,
    pages: ['工作台'],
    defaultPage: '工作台',
    hasDynamicRoutes: false,
    lastOpenedAt: Date.now()
  }
}

export default function OpenWorkspaceAction({ onOpenApplication, theme }: Props): JSX.Element {
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [workspaceHistory, setWorkspaceHistory] = useState<WorkspaceHistoryEntry[]>([])
  const [openingWorkspaceRoot, setOpeningWorkspaceRoot] = useState<string>()
  const [addingWorkspace, setAddingWorkspace] = useState(false)

  const handleOpenHistory = async (): Promise<void> => {
    setLoadingHistory(true)
    try {
      const [applications, sessionWorkspaces] = await Promise.all([
        loadStoredApplications(),
        canListSessionWorkspaces() ? listSessionWorkspaces() : Promise.resolve([])
      ])
      const entries = new Map<string, WorkspaceHistoryEntry>()

      applications.forEach((application) => {
        if (!application.workspaceRoot || entries.has(application.workspaceRoot)) return
        entries.set(application.workspaceRoot, {
          application,
          workspaceRoot: application.workspaceRoot,
          name: application.name || pathBasename(application.workspaceRoot),
          sessionCount: 0,
          frontendCount: 0,
          backendCount: 0,
          latestUpdatedAt: application.lastOpenedAt,
          latestTitle: '已保存项目'
        })
      })

      sessionWorkspaces.forEach((workspace) => {
        const existing = entries.get(workspace.workspaceRoot)
        if (!existing) return
        entries.set(workspace.workspaceRoot, { ...existing, ...workspace })
      })

      setWorkspaceHistory(
        Array.from(entries.values()).sort((a, b) => b.latestUpdatedAt - a.latestUpdatedAt)
      )
      setHistoryOpen(true)
    } catch (error) {
      message.error(formatError(error, '读取历史工作目录失败'))
    } finally {
      setLoadingHistory(false)
    }
  }

  const openWorkspace = async (workspace: WorkspaceHistoryEntry): Promise<void> => {
    setOpeningWorkspaceRoot(workspace.workspaceRoot)
    try {
      onOpenApplication(workspace.application)
      setHistoryOpen(false)
    } catch (error) {
      message.error(formatError(error, '打开历史工作目录失败'))
    } finally {
      setOpeningWorkspaceRoot(undefined)
    }
  }

  /** 选择本地项目，核对持久化应用身份后登记本机索引并打开。 */
  const addLocalWorkspace = async (): Promise<void> => {
    const workspaceApi = window.devAgentStudio?.workspace
    if (!workspaceApi?.selectDirectory) {
      message.warning('当前环境不能打开系统目录选择器，请在桌面客户端中使用。')
      return
    }

    setAddingWorkspace(true)
    try {
      const selected = await workspaceApi.selectDirectory({ title: '选择要添加的 DevAgent Studio 项目' })
      if (selected.canceled || !selected.path) return

      const applications = await loadStoredApplications()
      const indexedApplication = applications.find(
        (application) => application.workspaceRoot === selected.path
      )
      const schema = await loadWorkspaceApplicationConfig(selected.path)
      const application = applicationFromWorkspace(schema, selected.path)
      // 项目身份随生命周期持久化；另一台电脑的本机索引不能随机分配新身份与其冲突。
      try {
        const lifecycle = await getApplicationLifecycleSummary({ workspaceRoot: selected.path })
        application.id = lifecycle.application.id
      } catch (error) {
        if (!(error instanceof ApplicationLifecycleActionError) || error.errorType !== 'ApplicationLifecycleMissingError') throw error
        application.id = indexedApplication?.id || application.id
      }
      // 导入只登记本机索引，不重写 application.json、分支配置或文档资产。
      await saveStoredApplications([applicationIndexOf(application), ...applications
        .filter((item) => item.workspaceRoot !== selected.path && item.id !== application.id).map(applicationIndexOf)])
      onOpenApplication(application)
      setHistoryOpen(false)
      message.success('本地项目已添加到首页索引')
    } catch (error) {
      message.error(formatError(error, '添加本地项目失败'))
    } finally {
      setAddingWorkspace(false)
    }
  }

  return (
    <>
      <WelcomeActionCard
        buttonIcon={<FolderOpenOutlined />}
        buttonLabel="打开工作目录"
        description="打开已保存项目，或添加带 .devagentstudio 目录的本地项目。"
        icon={<FolderOpenOutlined />}
        iconVariant="folder"
        loading={loadingHistory}
        onClick={handleOpenHistory}
        title="打开工作目录"
      />

      <Modal
        destroyOnClose
        footer={null}
        onCancel={() => setHistoryOpen(false)}
        open={historyOpen}
        title={
          <WelcomeModalTitle
            description="仅已保存项目可直接打开，也可从本地文件夹添加"
            icon={<FolderOpenOutlined />}
            title="打开工作目录"
          />
        }
        width={820}
        wrapClassName={cx('welcome-modal', 'open-workspace-modal', `theme-${theme}`)}
      >
        <div className={cx('workspace-history-toolbar')}>
          <div>
            <strong>已保存项目</strong>
            <span>{workspaceHistory.length} 个项目</span>
          </div>
          <Button
            icon={<FolderAddOutlined />}
            loading={addingWorkspace}
            onClick={addLocalWorkspace}
            type="primary"
          >
            添加本地项目
          </Button>
        </div>
        {workspaceHistory.length === 0 ? (
          <Empty
            className={cx('workspace-history-empty')}
            description="暂无已保存项目，请从本地文件夹添加"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <>
            <List
              className={cx('workspace-history-list')}
              dataSource={workspaceHistory}
              renderItem={(workspace) => (
                <List.Item
                  actions={[
                    <Button
                      key="open"
                      loading={openingWorkspaceRoot === workspace.workspaceRoot}
                      onClick={() => openWorkspace(workspace)}
                      type="primary"
                    >
                      进入
                    </Button>
                  ]}
                  className={cx('workspace-history-item')}
                >
                  <List.Item.Meta
                    avatar={<FolderOpenOutlined className={cx('workspace-history-icon')} />}
                    description={
                      <div className={cx('workspace-history-description')}>
                        <Text
                          className={cx('workspace-history-path')}
                          title={workspace.workspaceRoot}
                        >
                          {workspace.workspaceRoot}
                        </Text>
                        <Space className={cx('workspace-history-meta')} size={[8, 6]} wrap>
                          {workspace.sessionCount > 0 ? <Tag>共 {workspace.sessionCount} 条</Tag> : null}
                          {workspace.frontendCount > 0 ? <Tag>前端 {workspace.frontendCount}</Tag> : null}
                          {workspace.backendCount > 0 ? (
                            <Tag>后端 {workspace.backendCount}</Tag>
                          ) : null}
                          <Text type="secondary">
                            最近 {formatHistoryTime(workspace.latestUpdatedAt)}
                          </Text>
                        </Space>
                        <Text className={cx('workspace-history-latest')} type="secondary">
                          {workspace.sessionCount > 0
                            ? `最近会话：${workspace.latestTitle}`
                            : '已保存项目 · 暂无历史会话'}
                        </Text>
                      </div>
                    }
                    title={<Text strong>{workspace.name}</Text>}
                  />
                </List.Item>
              )}
            />
          </>
        )}
      </Modal>
    </>
  )
}
