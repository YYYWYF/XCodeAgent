import {
  CheckOutlined,
  CopyOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  LockOutlined
} from '@ant-design/icons'
import { Alert, Button, Input, Spin, Tag } from 'antd'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { requestEntityDdl } from '../../../../service/entityTables'
import type { EntityDdlPreview as DdlPreview } from '../../../../service/entityTables'
import { cx } from '../../../../utils'
import './EntityDdlPreview.less'

hljs.registerLanguage('sql', sql)

type Props = { workspaceRoot?: string; entityId: string; onConfirmed?: (draftId: string) => void }

/** 审阅、修订和确认当前实体的建表语句，并提供原生复制。 */
export default function EntityDdlPreview({ workspaceRoot, entityId, onConfirmed }: Props): ReactElement {
  const [preview, setPreview] = useState<DdlPreview>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryKey, setRetryKey] = useState(0)
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [pending, setPending] = useState<'revise_ddl' | 'confirm_ddl'>()
  const [actionError, setActionError] = useState('')
  const inFlight = useRef(false)
  const mounted = useRef(false)

  // 切换实体、工作区或重试时清空旧结果，迟到的响应不能覆盖新实体。
  useEffect(() => {
    let active = true
    setPreview(undefined)
    setCopied(false)
    setCopyError('')
    setError('')
    setActionError('')
    if (!workspaceRoot) {
      setLoading(false)
      setError('请先打开当前应用工作区，再查看建表 DDL。')
      return undefined
    }
    setLoading(true)
    requestEntityDdl(workspaceRoot, entityId)
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

  // 切换页签或实体后忽略旧组件的修订和确认回执。
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

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

  /** 提交当前版本的审批动作，失败时保留草稿和用户意见。 */
  async function reviewDdl(action: 'revise_ddl' | 'confirm_ddl'): Promise<void> {
    if (!workspaceRoot || preview?.confirmationStatus !== 'draft' || inFlight.current) return
    const note = feedback.trim()
    if (action === 'revise_ddl' ? !note : Boolean(note)) return
    inFlight.current = true
    setPending(action)
    setActionError('')
    try {
      const result = await requestEntityDdl(
        workspaceRoot,
        entityId,
        action === 'revise_ddl'
          ? { action, draftId: preview.draftId, feedback: note }
          : { action, draftId: preview.draftId, confirmed: true }
      )
      if (!mounted.current) return
      setPreview(result)
      if (result.confirmationStatus === 'confirmed') onConfirmed?.(result.draftId)
      setFeedback('')
      setCopied(false)
      setCopyError('')
    } catch (caught: unknown) {
      if (mounted.current) setActionError(caught instanceof Error ? caught.message : 'DDL 操作失败。')
    } finally {
      inFlight.current = false
      if (mounted.current) setPending(undefined)
    }
  }

  return (
    <section className={cx('entity-ddl-preview')} aria-label="建表 DDL">
      {loading ? (
        <div className={cx('entity-ddl-loading')} role="status">
          <Spin size="small" />
          <span>正在加载建表 DDL…</span>
        </div>
      ) : error ? (
        <Alert
          showIcon
          type="error"
          message="DDL 加载失败"
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
              type={preview.confirmationStatus === 'confirmed' ? 'primary' : 'default'}
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              disabled={Boolean(pending)}
              onClick={() => void copyDdl()}
            >
              {copied ? '已复制' : preview.confirmationStatus === 'confirmed' ? '复制 DDL' : '复制草稿'}
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
              <span>AI 根据已确认的技术规划生成</span>
              <span role="status">
                {preview.confirmationStatus === 'confirmed' ? <CheckOutlined /> : <LockOutlined />}
                {preview.confirmationStatus === 'confirmed' ? '已保存' : '待确认'}
              </span>
            </div>
          </div>
          {preview.confirmationStatus === 'draft' ? (
            <div className={cx('entity-ddl-review')} aria-busy={Boolean(pending)}>
              <label>
                修改意见
                <Input.TextArea
                  value={feedback}
                  maxLength={2000}
                  autoSize={{ minRows: 2, maxRows: 5 }}
                  disabled={Boolean(pending)}
                  placeholder="例如：将商品名称的长度调整为 128 个字符"
                  onChange={(event) => setFeedback(event.target.value)}
                />
              </label>
              <div className={cx('entity-ddl-actions')}>
                <Button
                  disabled={Boolean(pending) || !feedback.trim()}
                  loading={pending === 'revise_ddl'}
                  onClick={() => void reviewDdl('revise_ddl')}
                >
                  按意见重新生成
                </Button>
                <Button
                  type="primary"
                  disabled={Boolean(pending) || Boolean(feedback.trim())}
                  loading={pending === 'confirm_ddl'}
                  onClick={() => void reviewDdl('confirm_ddl')}
                >
                  确认并保存
                </Button>
              </div>
            </div>
          ) : (
            <p className={cx('entity-ddl-note')}>
              <InfoCircleOutlined />
              <span>复制 DDL，在你的数据库工具中执行建表。</span>
            </p>
          )}
          {actionError ? (
            <Alert
              showIcon
              type="error"
              message={actionError}
              action={
                <Button size="small" onClick={() => setRetryKey((key) => key + 1)}>
                  重新读取
                </Button>
              }
            />
          ) : null}
          {copyError ? <Alert showIcon type="error" message={copyError} /> : null}
          <span className={cx('entity-ddl-copy-status')} role="status" aria-live="polite">
            {copied ? 'DDL 已复制' : ''}
          </span>
        </>
      ) : null}
    </section>
  )
}
