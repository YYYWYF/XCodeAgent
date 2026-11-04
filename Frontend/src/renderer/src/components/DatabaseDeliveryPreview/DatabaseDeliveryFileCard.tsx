import {
  CheckCircleOutlined,
  CopyOutlined,
  DownOutlined,
  FileTextOutlined,
  ReloadOutlined,
  UpOutlined
} from '@ant-design/icons'
import { Button, Spin } from 'antd'
import hljs from 'highlight.js/lib/core'
import sql from 'highlight.js/lib/languages/sql'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { cx } from '../../utils'
import { readDeliveryFile } from './databaseDeliveryPreview'
import type { DeliveryFileName, DeliveryFileState } from './databaseDeliveryPreview'

hljs.registerLanguage('sql', sql)
type Props = {
  workspaceRoot: string
  path: string
  pathError: string
  name: DeliveryFileName
  onReadyChange: (ready: boolean) => void
}

/** 每张卡片独立读取、复制和折叠脚本；错误始终可见，不以空文件推断失败。 */
export default function DatabaseDeliveryFileCard({
  workspaceRoot,
  path,
  pathError,
  name,
  onReadyChange
}: Props): JSX.Element {
  const [attempt, setAttempt] = useState(0)
  const [result, setResult] = useState<{ key: string; file: DeliveryFileState }>()
  const [expandedKey, setExpandedKey] = useState<string>()
  const [copyStatus, setCopyStatus] = useState('')
  const callback = useRef(onReadyChange)
  callback.current = onReadyChange
  const key = JSON.stringify([workspaceRoot, path, pathError, attempt])
  const file = result?.key === key ? result.file : undefined
  const loading = !file
  const ready = !!file && !file.error
  const empty = ready && name === 'alter.sql' && file.content === ''
  const expanded = expandedKey === key
  const contentId = useId()
  const title = name === 'create.sql' ? '完整建表' : '增量更新'
  const highlighted = useMemo(
    () => (expanded ? hljs.highlight(file?.content || '', { language: 'sql' }).value : ''),
    [expanded, file?.content]
  )

  useEffect(() => {
    let active = true
    callback.current(false)
    setCopyStatus('')
    const request = pathError
      ? Promise.resolve({ content: '', error: pathError })
      : readDeliveryFile(workspaceRoot, path, undefined, () => active)
    void request.then((next) => {
      if (!active) return
      setResult({ key, file: next })
      callback.current(!next.error)
    })
    return () => {
      active = false
    }
    // key 包含所有读取身份；回调仅使用最新父组件闭包，不触发重复读取。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  /** 仅刷新当前卡片，并同步撤销提交资格及当前文件的展开状态。 */
  function retry(): void {
    callback.current(false)
    setAttempt((value) => value + 1)
  }

  /** 复制完整原始 SQL，空增量和未就绪状态均不提供复制操作。 */
  async function copy(): Promise<void> {
    if (!ready || !file.content) return
    try {
      if (!window.devAgentStudio?.clipboard) throw new Error('剪贴板不可用')
      await window.devAgentStudio.clipboard.writeText(file.content)
      setCopyStatus('已复制')
    } catch {
      setCopyStatus('复制失败，请展开后选中文本手动复制。')
    }
  }

  return (
    <article className={cx('database-delivery-card')} aria-label={`${title} ${name}`}>
      <div className={cx('database-delivery-card-header')}>
        <div className={cx('database-delivery-card-title')}>
          <FileTextOutlined aria-hidden="true" />
          <h4>{title}</h4>
          <span>{name}</span>
        </div>
        <span
          className={cx(
            'database-delivery-status',
            ready && !empty && 'is-ready',
            file?.error && 'is-error'
          )}
          role="status"
        >
          {loading ? (
            <>
              <Spin size="small" /> 读取中
            </>
          ) : file.error ? (
            '读取失败'
          ) : empty ? (
            '无增量'
          ) : (
            <>
              <CheckCircleOutlined aria-hidden="true" /> 已读取
            </>
          )}
        </span>
      </div>
      <p className={cx('database-delivery-description')}>
        {empty
          ? '当前版本无数据库结构增量变更。'
          : name === 'create.sql'
            ? '包含当前版本的完整建表语句。'
            : '相对上一交付版本的数据库结构增量变更。'}
      </p>
      {file?.error && (
        <div className={cx('database-delivery-error')} role="alert">
          <p>
            {path || name} 无法完整读取：{file.error}
          </p>
          <p>请先完成项目验收及数据库脚本交付，再重新获取。</p>
        </div>
      )}
      <div className={cx('database-delivery-card-actions')}>
        <Button
          size="small"
          icon={<CopyOutlined />}
          disabled={!ready || !file.content}
          onClick={() => void copy()}
        >
          复制 SQL
        </Button>
        <Button
          size="small"
          icon={<ReloadOutlined spin={loading} />}
          disabled={loading}
          onClick={retry}
        >
          重新获取
        </Button>
        <Button
          className={cx('database-delivery-expand')}
          size="small"
          aria-label={`${expanded ? '收起' : '展开'} ${name}`}
          aria-expanded={expanded}
          aria-controls={contentId}
          disabled={!ready}
          onClick={() => setExpandedKey(expanded ? undefined : key)}
        >
          {expanded ? '收起' : '展开'} {expanded ? <UpOutlined /> : <DownOutlined />}
        </Button>
      </div>
      {expanded && ready && (
        <div id={contentId} className={cx('database-delivery-code')}>
          {empty ? (
            <p>当前版本无数据库增量变更。</p>
          ) : (
            <pre aria-label={name}>
              <code dangerouslySetInnerHTML={{ __html: highlighted }} />
            </pre>
          )}
        </div>
      )}
      {copyStatus && (
        <div className={cx('database-delivery-copy-status')} role="status">
          {copyStatus}
        </div>
      )}
    </article>
  )
}
