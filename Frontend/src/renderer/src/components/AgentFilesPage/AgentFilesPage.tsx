import { FileTextOutlined, SaveOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Spin, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { requestAgentFile, saveAgentFile } from '../../service/agentFiles'
import type { AgentFile } from '../../typings'
import { cx } from '../../utils'
import './AgentFilesPage.less'

const { Text, Title } = Typography

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

export default function AgentFilesPage(): ReactElement {
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
      setError(caughtError instanceof Error ? caughtError.message : 'AGENTS.md 读取失败。')
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
      setError(caughtError instanceof Error ? caughtError.message : 'AGENTS.md 保存失败。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className={cx('agent-files-page')} aria-label="文件">
      <aside className={cx('agent-files-list')} aria-label="核心文件">
        <div className={cx('agent-files-list-heading')}>
          <Title level={4}>核心文件</Title>
          <Text>管理 Agent 的角色与工作方式</Text>
        </div>
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
    </section>
  )
}
