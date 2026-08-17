import { AppstoreOutlined, CodeOutlined, DeleteOutlined, GlobalOutlined } from '@ant-design/icons'
import { Button, message, Modal, Radio } from 'antd'
import { useEffect, useState } from 'react'
import {
  canOpenApplicationWorkbench,
  subscribeApplicationsChanged,
  deleteStoredProject,
  loadStoredApplications,
  removeStoredApplication
} from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'
import { cx } from '../../utils'
import { useSessionRuntimeStore } from '../AiChatPanel/hooks/useSessionRuntimeStore'
import './WelcomeModal.less'
import './WelcomeRecentProjects.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
  theme: 'dark' | 'light'
}

const projectIcons = [AppstoreOutlined, CodeOutlined, GlobalOutlined]

type DeleteMode = 'index' | 'project'

function formatRecentTime(value: number): string {
  const elapsed = Math.max(0, Date.now() - value)
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return days < 30 ? `${days} 天前` : new Intl.DateTimeFormat('zh-CN').format(value)
}

export default function WelcomeRecentProjects({ onOpenApplication, theme }: Props): JSX.Element {
  const { clearWorkspace } = useSessionRuntimeStore()
  const [applications, setApplications] = useState<ApplicationConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [applicationToDelete, setApplicationToDelete] = useState<ApplicationConfig>()
  const [deleteMode, setDeleteMode] = useState<DeleteMode>('index')
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    let active = true
    let refreshId = 0

    // 首次挂载和索引变化时读取最新列表，较慢的旧请求不得覆盖较新的结果。
    const refreshApplications = async (): Promise<void> => {
      const currentRefreshId = ++refreshId
      try {
        const storedApplications = await loadStoredApplications()
        if (active && currentRefreshId === refreshId) {
          // 未完成初始化的新应用只保留在上方计划入口，最近项目仅展示可进入工作台的应用。
          setApplications(
            storedApplications.filter((application) => canOpenApplicationWorkbench(application))
          )
        }
      } finally {
        if (active && currentRefreshId === refreshId) setLoading(false)
      }
    }

    // 将持久化层的保存/删除通知转换为最近项目列表刷新。
    const handleApplicationsChanged = (): void => {
      void refreshApplications()
    }

    void refreshApplications()
    const unsubscribe = subscribeApplicationsChanged(handleApplicationsChanged)
    return () => {
      active = false
      unsubscribe()
    }
  }, [])

  // 打开删除选择框，并默认采用不会触碰真实项目目录的安全方式。
  const openDeleteDialog = (application: ApplicationConfig): void => {
    setApplicationToDelete(application)
    setDeleteMode('index')
  }

  // 根据已确认的删除方式移除首页索引，必要时先由主进程安全转移真实目录。
  const confirmDelete = async (
    application: ApplicationConfig,
    confirmedDeleteMode: DeleteMode
  ): Promise<void> => {
    setDeleting(true)
    try {
      if (confirmedDeleteMode === 'project') {
        if (!application.workspaceRoot) {
          throw new Error('该项目没有可删除的本地目录')
        }
        await clearWorkspace(application.workspaceRoot)
        await deleteStoredProject(application.workspaceRoot)
      }
      await removeStoredApplication(application.id)
      setApplications((current) =>
        current.filter((candidate) => candidate.id !== application.id)
      )
      message.success(
        confirmedDeleteMode === 'project'
          ? '本地项目和聊天历史已移至系统回收站，首页索引已移除'
          : '项目已从首页移除'
      )
      setApplicationToDelete(undefined)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除项目失败')
    } finally {
      setDeleting(false)
    }
  }

  // 删除真实目录前展示完整路径并要求二次确认，索引移除仍保持单次确认。
  const requestDelete = (): void => {
    if (!applicationToDelete) return
    if (deleteMode === 'index') {
      void confirmDelete(applicationToDelete, 'index')
      return
    }

    const workspaceRoot = applicationToDelete.workspaceRoot
    if (!workspaceRoot) {
      message.error('该项目没有可删除的本地目录')
      return
    }

    const application = applicationToDelete
    Modal.confirm({
      cancelText: '取消',
      centered: true,
      content: (
        <div className={cx('welcome-project-delete-confirmation')}>
          <p>此操作会删除整个文件夹及其中的所有文件：</p>
          <code>{workspaceRoot}</code>
          <p>文件会移到系统回收站，同时移走该项目的聊天历史并删除首页索引。</p>
        </div>
      ),
      okButtonProps: { danger: true },
      okText: '确认移到回收站',
      onOk: () => confirmDelete(application, 'project'),
      title: `再次确认删除「${application.name}」？`,
      wrapClassName: cx('welcome-modal', `theme-${theme}`)
    })
  }

  const projectDirectoryCanBeDeleted = Boolean(
    applicationToDelete?.workspaceRoot && window.xcodeAgent?.applications.deleteProject
  )

  return (
    <section className={cx('welcome-recents')} aria-labelledby="welcome-recents-title">
      <div className={cx('welcome-section-heading')}>
        <h2 id="welcome-recents-title">最近项目</h2>
      </div>

      <div className={cx('welcome-project-list')}>
        {loading ? (
          Array.from({ length: 3 }, (_, index) => (
            <div className={cx('welcome-project-row', 'loading')} key={index} />
          ))
        ) : applications.length > 0 ? (
          applications.map((application, index) => {
            const ProjectIcon = projectIcons[index % projectIcons.length]
            return (
              <div className={cx('welcome-project-row')} key={application.id}>
                <button
                  className={cx('welcome-project-open')}
                  onClick={() => onOpenApplication(application)}
                  type="button"
                >
                  <span className={cx('welcome-project-icon', `tone-${index % 3}`)}>
                    <ProjectIcon />
                  </span>
                  <span className={cx('welcome-project-main')}>
                    <strong>{application.name}</strong>
                    <code>
                      {application.workspaceRoot || application.projectDirectoryName || '本地应用'}
                    </code>
                  </span>
                  <span className={cx('welcome-project-description')}>
                    {application.senario || '继续上一次开发会话'}
                  </span>
                  <time dateTime={new Date(application.createdAt).toISOString()}>
                    {formatRecentTime(application.createdAt)}
                  </time>
                </button>
                <Button
                  aria-label={`删除 ${application.name}`}
                  className={cx('welcome-project-delete')}
                  icon={<DeleteOutlined />}
                  onClick={() => openDeleteDialog(application)}
                  title={`删除 ${application.name}`}
                  type="text"
                />
              </div>
            )
          })
        ) : (
          <div className={cx('welcome-project-empty')}>
            <CodeOutlined />
            <span>完成应用初始化或打开工作目录后，最近项目会显示在这里。</span>
          </div>
        )}
      </div>

      <Modal
        cancelText="取消"
        centered
        className={cx('welcome-modal', 'welcome-project-delete-modal', `theme-${theme}`)}
        confirmLoading={deleting}
        okButtonProps={{ danger: deleteMode === 'project' }}
        okText={deleteMode === 'project' ? '继续' : '移除索引'}
        onCancel={() => !deleting && setApplicationToDelete(undefined)}
        onOk={requestDelete}
        open={Boolean(applicationToDelete)}
        title={`删除「${applicationToDelete?.name || ''}」`}
      >
        <Radio.Group
          className={cx('welcome-project-delete-options')}
          onChange={(event) => setDeleteMode(event.target.value as DeleteMode)}
          value={deleteMode}
        >
          <Radio value="index">
            仅删除索引
            <span>保留本地项目目录和其中的所有文件。</span>
          </Radio>
          <Radio disabled={!projectDirectoryCanBeDeleted} value="project">
            移到系统回收站
            <span>
              将 {applicationToDelete?.workspaceRoot || '项目目录'} 及其中所有文件移到系统回收站，
              同时移走该项目的全部聊天历史；清空回收站前仍可找回文件。
            </span>
          </Radio>
        </Radio.Group>
        {!projectDirectoryCanBeDeleted ? (
          <p className={cx('welcome-project-delete-hint')}>
            仅 XCodeAgent 创建且带有项目标识的本地目录可以在此删除。
          </p>
        ) : null}
      </Modal>
    </section>
  )
}
