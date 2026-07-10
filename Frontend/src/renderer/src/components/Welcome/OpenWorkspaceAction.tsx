import { FolderOpenOutlined } from '@ant-design/icons'
import { Button, Empty, List, message, Modal, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import {
  canListSessionWorkspaces,
  listSessionWorkspaces,
  type SessionWorkspaceSummary
} from '../../service/chatSessions'
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
  onOpenApplication: (application: ApplicationConfig) => void
  theme: 'dark' | 'light'
}

export default function OpenWorkspaceAction({ onOpenApplication, theme }: Props): JSX.Element {
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [workspaceHistory, setWorkspaceHistory] = useState<SessionWorkspaceSummary[]>([])
  const [openingWorkspaceRoot, setOpeningWorkspaceRoot] = useState<string>()

  const handleOpenHistory = async (): Promise<void> => {
    setLoadingHistory(true)
    try {
      if (!canListSessionWorkspaces()) {
        message.warning('当前环境不能读取本地历史工作目录，请在桌面客户端中使用。')
        return
      }

      setWorkspaceHistory(await listSessionWorkspaces())
      setHistoryOpen(true)
    } catch (error) {
      message.error(formatError(error, '读取历史工作目录失败'))
    } finally {
      setLoadingHistory(false)
    }
  }

  const openWorkspace = async (workspace: SessionWorkspaceSummary): Promise<void> => {
    setOpeningWorkspaceRoot(workspace.workspaceRoot)
    try {
      const workspaceName = workspace.name || pathBasename(workspace.workspaceRoot)
      const projectParentPath = pathDirname(workspace.workspaceRoot)
      const schema = buildApplicationSchema({
        ...initialApplicationDraft,
        appName: workspaceName,
        projectParentPath,
        projectDirectoryName: workspaceName
      })
      const application: ApplicationConfig = {
        ...schema,
        id: createApplicationId(),
        name: workspaceName,
        workspaceRoot: workspace.workspaceRoot,
        projectParentPath,
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
      <WelcomeActionCard
        buttonIcon={<FolderOpenOutlined />}
        buttonLabel="打开工作目录"
        description="从历史会话中选择工作目录，直接进入对话和受保护工具工作台。"
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
            description="从最近会话中选择一个目录，继续之前的工作"
            icon={<FolderOpenOutlined />}
            title="打开工作目录"
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
                          <Tag>共 {workspace.sessionCount} 条</Tag>
                          <Tag>前端 {workspace.frontendCount}</Tag>
                          {workspace.backendCount > 0 ? (
                            <Tag>后端 {workspace.backendCount}</Tag>
                          ) : null}
                          <Text type="secondary">
                            最近 {formatHistoryTime(workspace.latestUpdatedAt)}
                          </Text>
                        </Space>
                        <Text className={cx('workspace-history-latest')} type="secondary">
                          最近会话：{workspace.latestTitle}
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
