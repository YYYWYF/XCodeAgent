import {
  CheckCircleOutlined, CopyOutlined, DownOutlined, ExclamationCircleOutlined,
  ExpandOutlined, LoadingOutlined, MinusCircleOutlined, UpOutlined
} from '@ant-design/icons'
import { Button, Modal, Tooltip, message } from 'antd'
import { useEffect, useId, useState } from 'react'
import type { ReactElement } from 'react'
import type { TechnicalPlanningOutput as Output, TechnicalPlanningOutputEntry } from '../../../../typings/technicalPlanningOutput'
import { cx } from '../../../../utils'
import './TechnicalPlanningOutput.less'

/** 按真实工具状态显示活动标记，不推测模型是否完成或生成虚假进度。 */
function activityIcon(entry: TechnicalPlanningOutputEntry, running: boolean): ReactElement {
  if (entry.status === 'running') return running ? <LoadingOutlined spin /> : <MinusCircleOutlined />
  if (entry.status === 'failed' || entry.kind === 'retry') return <ExclamationCircleOutlined />
  return <CheckCircleOutlined />
}

/** 将活动后收到的正文归在同一条目内，只改变展示，不改动原始输出。 */
function groupOutputEntries(entries: TechnicalPlanningOutputEntry[]): {
  entry: TechnicalPlanningOutputEntry
  content: TechnicalPlanningOutputEntry[]
}[] {
  const groups: { entry: TechnicalPlanningOutputEntry; content: TechnicalPlanningOutputEntry[] }[] = []
  for (const entry of entries) {
    const last = groups.at(-1)
    if (entry.kind === 'text' && last && last.entry.kind !== 'text') last.content.push(entry)
    else groups.push({ entry, content: [] })
  }
  return groups
}

/** 生成时展示片段正文，成功完成后自动收起，并允许用户再次展开。 */
function OutputEntry({ entry, content, running }: {
  entry: TechnicalPlanningOutputEntry
  content: TechnicalPlanningOutputEntry[]
  running: boolean
}): ReactElement {
  const [expanded, setExpanded] = useState(entry.status !== 'completed')
  const contentId = useId()

  // 仅在状态变为完成时收起，后续输出更新不会打断用户手动展开查看。
  useEffect(() => {
    if (entry.status === 'completed') setExpanded(false)
  }, [entry.status])

  if (entry.kind === 'text') return <pre className={cx('technical-planning-output-text')}>{entry.text}</pre>

  const activity = <>
    <span aria-hidden="true">{activityIcon(entry, running)}</span>
    <time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</time>
    <span>{entry.text}</span>
  </>
  const activityClass = cx('technical-planning-output-activity', entry.status, entry.kind)
  return (
    <div>
      {content.length ? (
        <button type="button" className={`${activityClass} ${cx('technical-planning-output-entry-toggle')}`}
          aria-label={`${expanded ? '收起' : '展开'}片段输出：${entry.text}`}
          aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded(!expanded)}>
          {activity}
          {expanded ? <UpOutlined aria-hidden="true" /> : <DownOutlined aria-hidden="true" />}
        </button>
      ) : <div className={activityClass}>{activity}</div>}
      <div id={contentId} hidden={!expanded}>
        {expanded ? content.map((item) => <pre key={item.id} className={cx('technical-planning-output-text')}>{item.text}</pre>) : null}
      </div>
    </div>
  )
}

/** 展示本轮技术规划的真实流式正文与工具活动，提供收起、复制和放大查看。 */
export default function TechnicalPlanningOutput({ output, running, runId }: {
  output?: Output
  running: boolean
  runId: string
}): ReactElement | null {
  const [expanded, setExpanded] = useState(running)
  const [modalOpen, setModalOpen] = useState(false)
  const [copying, setCopying] = useState(false)
  const bodyId = useId()
  const entries = output?.entries || []
  const status = running ? '接收中' : '输出已结束'

  // 每轮生成默认展开，输出结束时收起；之后手动展开不受后续快照影响。
  useEffect(() => {
    setExpanded(running)
  }, [running, runId])

  /** 复制当前实际收到的内容，失败时提供明确反馈。 */
  const copyOutput = async (): Promise<void> => {
    setCopying(true)
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('剪贴板不可用')
      await clipboard.writeText(entries.map((entry) => entry.text).join('\n'))
      message.success('过程记录已复制')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '复制失败，请重试')
    } finally {
      setCopying(false)
    }
  }

  /** 两种查看尺寸共用原文，隐藏思考和工具参数由后端事件边界保证。 */
  const renderOutput = (): ReactElement => (
    <div className={cx('technical-planning-output-body')} aria-label="技术规划过程记录内容">
      {output?.truncated ? <div className={cx('technical-planning-output-hint')}>内容较长，仅保留最近的输出。</div> : null}
      {groupOutputEntries(entries).map(({ entry, content }) => (
        <OutputEntry key={entry.id} entry={entry} content={content} running={running} />
      ))}
      {running && entries.length ? <div className={cx('technical-planning-output-hint')}><LoadingOutlined spin /> 等待新的输出…</div> : null}
    </div>
  )

  // 过程记录只来自当前内存；重新读取历史后没有记录时，不显示空面板或查看入口。
  if (!entries.length) return null

  return (
    <section className={cx('technical-planning-output')} data-run-id={runId}>
      <div className={cx('technical-planning-output-header')}>
        <button type="button" className={cx('technical-planning-output-toggle')}
          aria-expanded={expanded} aria-controls={bodyId} onClick={() => setExpanded(!expanded)}>
          <strong>查看过程记录</strong>
          <span className={cx('technical-planning-output-status', running && 'running')}>{status}</span>
        </button>
        <Tooltip title="复制过程记录"><Button type="text" size="small" aria-label="复制过程记录" icon={<CopyOutlined />}
          disabled={!entries.length} loading={copying} onClick={() => void copyOutput()} /></Tooltip>
        <Tooltip title="放大查看"><Button type="text" size="small" aria-label="放大过程记录" icon={<ExpandOutlined />}
          onClick={() => setModalOpen(true)} /></Tooltip>
        <Button type="text" size="small" aria-label={expanded ? '收起过程记录' : '展开过程记录'}
          icon={expanded ? <UpOutlined /> : <DownOutlined />} onClick={() => setExpanded(!expanded)} />
      </div>
      <div id={bodyId} hidden={!expanded}>{expanded ? renderOutput() : null}</div>
      <Modal title="技术规划 · 过程记录" open={modalOpen} onCancel={() => setModalOpen(false)} footer={null}
        width="min(1100px, 90vw)" className={cx('technical-planning-output-modal')}>
        {renderOutput()}
      </Modal>
    </section>
  )
}
