import { FolderOpenOutlined } from '@ant-design/icons'
import { Button, Empty, List, message, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import {
  canListSessionWorkspaces,
  listSessionWorkspaces,
  type SessionWorkspaceSummary
} from '../../service/chatSessions'
import { loadStoredApplications } from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'
import { cx } from '../../utils'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import { saveAndOpenApplication } from './applicationService'
import { initialApplicationDraft } from './constants'
import './WelcomeModal.less'
import './WorkspaceHistoryModal.less'
import {
  buildApplicationSchema,
  createApplicationId,
  formatError,
  formatHistoryTime,
  pathBasename,
  pathDirname
} from './utils'

const { Text } = Typography

type Props = {
  compact?: boolean
  onOpenApplication: (application: ApplicationConfig) => void
  theme: 'dark' | 'light'
}

type WorkspaceHistoryEntry = SessionWorkspaceSummary & {
  application?: ApplicationConfig
}

export default function OpenWorkspaceAction({ compact, onOpenApplication, theme }: Props): JSX.Element {
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [workspaceHistory, setWorkspaceHistory] = useState<WorkspaceHistoryEntry[]>([])
  const [openingWorkspaceRoot, setOpeningWorkspaceRoot] = useState<string>()

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
          latestUpdatedAt: application.createdAt,
          latestTitle: '已保存项目'
        })
      })

      sessionWorkspaces.forEach((workspace) => {
        const existing = entries.get(workspace.workspaceRoot)
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
      if (workspace.application) {
        onOpenApplication(workspace.application)
        setHistoryOpen(false)
        return
      }

      const workspaceName = workspace.name || pathBasename(workspace.workspaceRoot)
      const schema = buildApplicationSchema({
        ...initialApplicationDraft,
        appName: workspaceName,
        projectPath: workspace.workspaceRoot
      })
      const application: ApplicationConfig = {
        ...schema,
        id: createApplicationId(),
        name: workspaceName,
        workspaceRoot: workspace.workspaceRoot,
        projectParentPath: pathDirname(workspace.workspaceRoot),
        projectDirectoryName: workspaceName,
        source: 'existing-workspace',
        audience: 'developer',
        enableAuth: schema.auth.enable,
        enableTracking: schema.track.enable || schema.apiTrack.enable,
        legacyTheme: 'light',
        legacyLayout: 'login-admin',
        enableTabs: false,
        pages: ['工作台'],
        defaultPage: '工作台',
        hasDynamicRoutes: false,
        schema,
        createdAt: Date.now()
      }
      await saveAndOpenApplication(application, onOpenApplication)
      setHistoryOpen(false)
    } catch (error) {
      message.error(formatError(error, '打开历史工作目录失败'))
    } finally {
      setOpeningWorkspaceRoot(undefined)
    }
  }

  return (
    <>
      {compact ? (
        <Button
          className={cx('welcome-view-all')}
          loading={loadingHistory}
          onClick={handleOpenHistory}
          type="text"
        >
          查看全部
        </Button>
      ) : (
        <WelcomeActionCard
          buttonIcon={<FolderOpenOutlined />}
          buttonLabel="打开工作目录"
          description="从已保存项目或历史会话中选择工作目录，继续之前的工作。"
          icon={<FolderOpenOutlined />}
          iconVariant="folder"
          loading={loadingHistory}
          onClick={handleOpenHistory}
          title="打开工作目录"
        />
      )}

      <Modal
        destroyOnClose
        footer={null}
        onCancel={() => setHistoryOpen(false)}
        open={historyOpen}
        title={
          <WelcomeModalTitle
            description={
              compact
                ? '查看已保存的应用并继续之前的工作'
                : '从已保存项目和最近会话中选择一个工作目录'
            }
            icon={<FolderOpenOutlined />}
            title={compact ? '全部项目' : '打开工作目录'}
          />
        }
        width={820}
        wrapClassName={cx('welcome-modal', 'open-workspace-modal', `theme-${theme}`)}
      >
        {workspaceHistory.length === 0 ? (
          <Empty
            className={cx('workspace-history-empty')}
            description="暂无历史工作目录"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <>
            <div className={cx('workspace-history-summary')}>
              <strong>最近使用</strong>
              <span>{workspaceHistory.length} 个工作目录</span>
            </div>
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
