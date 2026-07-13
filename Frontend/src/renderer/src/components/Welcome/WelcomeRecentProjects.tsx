import { AppstoreOutlined, CodeOutlined, GlobalOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import { loadStoredApplications } from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'
import { cx } from '../../utils'
import './WelcomeRecentProjects.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
}

const projectIcons = [AppstoreOutlined, CodeOutlined, GlobalOutlined]

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

  useEffect(() => {
    let active = true
    void loadStoredApplications()
      .then((storedApplications) => {
        if (active) setApplications(storedApplications)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

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
              <button
                className={cx('welcome-project-row')}
                key={application.id}
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
            )
          })
        ) : (
          <div className={cx('welcome-project-empty')}>
            <CodeOutlined />
            <span>新建应用或打开工作目录后，最近项目会显示在这里。</span>
          </div>
        )}
      </div>
    </section>
  )
}
