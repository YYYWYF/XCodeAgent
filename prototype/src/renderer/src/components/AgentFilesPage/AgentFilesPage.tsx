import { CloseOutlined, FileTextOutlined, SaveOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Spin, Typography, message } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { isAuthenticationFailure } from '../../service/authentication'
import { requestAgentFile, saveAgentFile } from '../../service/agentFiles'
import type { AgentFile } from '../../typings'
import { cx } from '../../utils'
import folderFilledIcon from '../../assets/icons/folder-filled.svg'
import './AgentFilesPage.less'

const { Text, Title } = Typography

type Props = {
  /** 关闭承载本页的抽屉；页面顶栏右侧的关闭按钮直接复用抽屉的收起动作。 */
  onClose?: () => void
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} kB`
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatUpdatedAt(value: string): string {
  const updatedAt = Date.parse(value)
  if (!Number.isFinite(updatedAt)) return '未知时间'

  const elapsedMinutes = Math.floor(Math.max(0, Date.now() - updatedAt) / 60_000)
  if (elapsedMinutes < 1) return '刚刚更新'
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前`

  const elapsedHours = Math.floor(elapsedMinutes / 60)
  if (elapsedHours < 24) return `${elapsedHours} 小时前`

  const elapsedDays = Math.floor(elapsedHours / 24)
  if (elapsedDays < 30) return `${elapsedDays} 天前`

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
  }).format(updatedAt)
}

export default function AgentFilesPage({ onClose }: Props): ReactElement {
  const [agentFile, setAgentFile] = useState<AgentFile>()
  const [content, setContent] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const loadFile = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError('')
    try {
      const result = await requestAgentFile()
      setAgentFile(result)
      setContent(result.document.content)
    } catch (caughtError) {
      setError(
        isAuthenticationFailure(caughtError)
          ? '请重新登录后重试。'
          : caughtError instanceof Error
            ? caughtError.message
            : 'AGENTS.md 读取失败。'
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadFile()
  }, [loadFile])

  const hasChanges = Boolean(agentFile && content !== agentFile.document.content)
  const pathLabel = useMemo(() => {
    if (!agentFile) return '环境文件'
    return `${agentFile.root}/${agentFile.document.relativePath}`
  }, [agentFile])

  const handleSave = async (): Promise<void> => {
    if (!agentFile || !hasChanges || saving) return

    setSaving(true)
    setError('')
    try {
      const result = await saveAgentFile({
        content,
        expectedRevision: agentFile.document.revision
      })
      setAgentFile(result)
      setContent(result.document.content)
      message.success('AGENTS.md 已保存')
    } catch (caughtError) {
      if (!isAuthenticationFailure(caughtError)) {
        setError(caughtError instanceof Error ? caughtError.message : 'AGENTS.md 保存失败。')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className={cx('agent-files-page')} aria-label="文件">
      {/* 页面顶栏即功能抽屉的抽屉头：徽标+标题+关闭，与对话管理等抽屉同一套语言。 */}
      <header className={cx('agent-files-topbar')}>
        <span aria-hidden="true" className={cx('auxiliary-drawer-badge')}>
          <span
            aria-hidden="true"
            className={cx('auxiliary-drawer-badge-icon')}
            style={{ '--auxiliary-drawer-badge-source': `url("${folderFilledIcon}")` } as CSSProperties}
          />
        </span>
        <div className={cx('functional-drawer-heading')}>
          <strong>文件</strong>
          <small>管理 Agent 的角色与工作方式</small>
        </div>
        {onClose ? (
          <button
            aria-label="关闭辅助抽屉"
            className={cx('drawer-close-btn')}
            onClick={onClose}
            title="关闭辅助抽屉"
            type="button"
          >
            <CloseOutlined />
          </button>
        ) : null}
      </header>
      <div className={cx('agent-files-body')}>
      <aside className={cx('agent-files-list')} aria-label="核心文件">
        <Text className={cx('agent-files-list-caption')}>核心文件</Text>
        <button aria-current="page" className={cx('agent-file-item', 'active')} type="button">
          <span className={cx('agent-file-item-icon')} aria-hidden="true">
            <FileTextOutlined />
          </span>
          <span className={cx('agent-file-item-copy')}>
            <Text strong>AGENTS.md</Text>
            <Text className={cx('agent-file-item-meta')}>
              {agentFile
                ? `${formatFileSize(agentFile.document.sizeBytes)} · ${formatUpdatedAt(agentFile.document.updatedAt)}`
                : '正在读取文件'}
            </Text>
          </span>
        </button>
      </aside>

      <div className={cx('agent-files-editor')}>
        <header className={cx('agent-files-header')}>
          <div className={cx('agent-files-title')}>
            <span className={cx('agent-files-title-icon')} aria-hidden="true">
              <FileTextOutlined />
            </span>
            <div>
              <Title level={4}>AGENTS.md</Title>
              <Text title={pathLabel}>{pathLabel}</Text>
            </div>
          </div>
          <Button
            className={cx('agent-files-save-button')}
            disabled={!hasChanges || loading}
            icon={<SaveOutlined />}
            loading={saving}
            onClick={() => void handleSave()}
            type="primary"
          >
            保存
          </Button>
        </header>

        <div className={cx('agent-files-content')}>
          <Text className={cx('agent-files-content-label')}>内容</Text>
          {loading ? (
            <div className={cx('agent-files-state')}>
              <Spin />
              <Text type="secondary">正在读取 AGENTS.md...</Text>
            </div>
          ) : error && !agentFile ? (
            <div className={cx('agent-files-state')}>
              <Alert description={error} message="无法读取 AGENTS.md" showIcon type="error" />
              <Button onClick={() => void loadFile()}>重试</Button>
            </div>
          ) : agentFile ? (
            <>
              {error && (
                <Alert className={cx('agent-files-error')} message={error} showIcon type="error" />
              )}
              <Input.TextArea
                aria-label="AGENTS.md 内容"
                className={cx('agent-files-textarea')}
                disabled={saving}
                onChange={(event) => setContent(event.target.value)}
                spellCheck={false}
                value={content}
              />
            </>
          ) : (
            <div className={cx('agent-files-state')}>
              <Empty description="暂无可读取的文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          )}
        </div>
      </div>
      </div>
    </section>
  )
}
