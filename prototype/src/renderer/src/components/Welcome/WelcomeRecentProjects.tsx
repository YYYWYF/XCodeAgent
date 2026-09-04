import { AppstoreOutlined, CodeOutlined, DeleteOutlined, GlobalOutlined } from '@ant-design/icons'
import { Button, message, Modal, Radio } from 'antd'
import { useEffect, useState } from 'react'
import {
  subscribeApplicationsChanged,
  deleteStoredProject,
  loadStoredApplications,
  removeStoredApplication
} from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'
import { purgeApplicationTasks } from '../../backgroundTasks'
import { currentVersion } from '../../service/applicationVersions'
import { cx } from '../../utils'
import './WelcomeModal.less'
import './WelcomeRecentProjects.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
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

export default function WelcomeRecentProjects({ onOpenApplication }: Props): JSX.Element {
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
        // 规划在需求分析/项目规划阶段的工作台内完成：所有应用都可直接进入工作台，最近项目全量展示。
        if (active && currentRefreshId === refreshId) {
          setApplications(storedApplications)
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

  // 根据用户选项移除首页索引，必要时先由主进程安全删除真实目录。
  const confirmDelete = async (): Promise<void> => {
    if (!applicationToDelete) return
    setDeleting(true)
    try {
      if (deleteMode === 'project') {
        if (!applicationToDelete.workspaceRoot) {
          throw new Error('该项目没有可删除的本地目录')
        }
        await deleteStoredProject(applicationToDelete.workspaceRoot)
      }
      await removeStoredApplication(applicationToDelete.id)
      // 任务流水跟着应用走：应用删除时同步清空它的后台任务记录。
      purgeApplicationTasks(applicationToDelete.id)
      setApplications((current) =>
        current.filter((application) => application.id !== applicationToDelete.id)
      )
      message.success(deleteMode === 'project' ? '本地项目及其首页索引已删除' : '项目已从首页移除')
      setApplicationToDelete(undefined)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除项目失败')
    } finally {
      setDeleting(false)
    }
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
            const version = currentVersion(application)
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
                    <span className={cx('welcome-project-title')}>
                      <strong>{application.name}</strong>
                      {version ? (
                        <span className={cx('welcome-project-version')}>
                          {version.versionLabel}
                          {version.status === 'released' ? ' · 已生成版本' : ''}
                        </span>
                      ) : null}
                    </span>
                    <code>
                      {application.workspaceRoot || application.projectDirectoryName || '本地应用'}
                    </code>
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
        className={cx('welcome-modal', 'welcome-project-delete-modal', 'theme-light')}
        confirmLoading={deleting}
        okButtonProps={{ danger: deleteMode === 'project' }}
        okText={deleteMode === 'project' ? '删除本地项目' : '移除索引'}
        onCancel={() => !deleting && setApplicationToDelete(undefined)}
        onOk={() => void confirmDelete()}
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
            删除本地项目
            <span>
              永久删除 {applicationToDelete?.workspaceRoot || '项目目录'}{' '}
              及其中所有文件，且无法恢复。
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
