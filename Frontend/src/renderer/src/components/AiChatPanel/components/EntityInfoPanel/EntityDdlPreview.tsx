import {
  CheckOutlined,
  CopyOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  LockOutlined
} from '@ant-design/icons'
import { Alert, Button, Spin, Tag } from 'antd'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import { useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { requestEntityDdlPreview } from '../../../../service/entityTables'
import type { EntityDdlPreview as DdlPreview } from '../../../../service/entityTables'
import { cx } from '../../../../utils'
import './EntityDdlPreview.less'

hljs.registerLanguage('sql', sql)

type Props = { workspaceRoot?: string; entityId: string }

/** 懒加载当前已确认实体的 DDL，只提供查看和原生复制。 */
export default function EntityDdlPreview({ workspaceRoot, entityId }: Props): ReactElement {
  const [preview, setPreview] = useState<DdlPreview>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')

  // 切换实体、工作区或重试时清空旧结果，迟到的响应不能覆盖新实体。
  useEffect(() => {
    let active = true
    setPreview(undefined)
    setCopied(false)
    setCopyError('')
    setError('')
    if (!workspaceRoot) {
      setLoading(false)
      setError('请先打开当前应用工作区，再查看建表 DDL。')
      return undefined
    }
    setLoading(true)
    requestEntityDdlPreview(workspaceRoot, entityId)
      .then((result) => {
        if (active) setPreview(result)
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : '读取建表 DDL 失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [workspaceRoot, entityId, retryKey])

  // 成功提示短暂显示，组件卸载或切换实体后清理计时器。
  useEffect(() => {
    if (!copied) return undefined
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  // 高亮库负责转义源码；按原始 SQL 行展示，复制时不会包含行号或 HTML。
  const lines = useMemo(
    () =>
      preview?.sql.split('\n').map((line) => hljs.highlight(line, { language: 'sql' }).value) || [],
    [preview?.sql]
  )

  /** 将完整原始 DDL 写入 Electron 剪贴板，只有成功后才显示已复制。 */
  async function copyDdl(): Promise<void> {
    if (!preview) return
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('Native clipboard is unavailable')
      await clipboard.writeText(preview.sql)
      setCopyError('')
      setCopied(true)
    } catch {
      setCopied(false)
      setCopyError('复制失败，请重试或选中 DDL 文本手动复制。')
    }
  }

  return (
    <section className={cx('entity-ddl-preview')} aria-label="建表 DDL">
      {loading ? (
        <div className={cx('entity-ddl-loading')} role="status">
          <Spin size="small" />
          <span>正在生成建表 DDL…</span>
        </div>
      ) : error ? (
        <Alert
          showIcon
          type="error"
          message="DDL 读取失败"
          description={error}
          action={
            <Button
              size="small"
              disabled={!workspaceRoot}
              onClick={() => setRetryKey((key) => key + 1)}
            >
              重试
            </Button>
          }
        />
      ) : preview ? (
        <>
          <div className={cx('entity-ddl-toolbar')}>
            <div className={cx('entity-ddl-file')}>
              <FileTextOutlined />
              <span>{preview.tableName}.sql</span>
              <Tag>MySQL</Tag>
            </div>
            <Button
              type="primary"
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => void copyDdl()}
            >
              {copied ? '已复制' : '复制 DDL'}
            </Button>
          </div>
          <div className={cx('entity-ddl-code-shell')}>
            <ol className={cx('entity-ddl-code')} aria-label="完整建表语句">
              {lines.map((line, index) => (
                <li key={index}>
                  <code dangerouslySetInnerHTML={{ __html: line || ' ' }} />
                </li>
              ))}
            </ol>
            <div className={cx('entity-ddl-footer')}>
              <span>基于已确认的技术规划 · 含系统主键 id</span>
              <span>
                <LockOutlined />
                只读
              </span>
            </div>
          </div>
          <p className={cx('entity-ddl-note')}>
            <InfoCircleOutlined />
            <span>复制 DDL，在你的数据库工具中执行建表。</span>
          </p>
          {copyError ? <Alert showIcon type="error" message={copyError} /> : null}
          <span className={cx('entity-ddl-copy-status')} role="status" aria-live="polite">
            {copied ? 'DDL 已复制' : ''}
          </span>
        </>
      ) : null}
    </section>
  )
}
