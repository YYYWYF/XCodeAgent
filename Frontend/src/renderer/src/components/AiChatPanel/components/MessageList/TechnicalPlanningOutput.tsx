import {
  CheckCircleOutlined, CopyOutlined, DownOutlined, ExclamationCircleOutlined,
  ExpandOutlined, LoadingOutlined, MinusCircleOutlined, UpOutlined
} from '@ant-design/icons'
import { Button, Empty, Modal, Tooltip, message } from 'antd'
import { useId, useState } from 'react'
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

/** 展示本轮技术规划的真实流式正文与工具活动，提供收起、复制和放大查看。 */
export default function TechnicalPlanningOutput({ output, running, runId }: {
  output?: Output
  running: boolean
  runId: string
}): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const [modalOpen, setModalOpen] = useState(false)
  const [copying, setCopying] = useState(false)
  const bodyId = useId()
  const entries = output?.entries || []
  const status = running ? '接收中' : '输出已结束'

  /** 复制当前实际收到的内容，失败时提供明确反馈。 */
  const copyOutput = async (): Promise<void> => {
    setCopying(true)
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('剪贴板不可用')
      await clipboard.writeText(entries.map((entry) => entry.text).join('\n'))
      message.success('实时输出已复制')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '复制失败，请重试')
    } finally {
      setCopying(false)
    }
  }

  /** 两种查看尺寸共用原文，隐藏思考和工具参数由后端事件边界保证。 */
  const renderOutput = (): ReactElement => (
    <div className={cx('technical-planning-output-body')} aria-label="技术规划实时输出内容">
      {output?.truncated ? <div className={cx('technical-planning-output-hint')}>内容较长，仅保留最近的输出。</div> : null}
      {!entries.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={running ? '等待模型返回内容…' : '本次没有收到输出'} />
      ) : entries.map((entry) => entry.kind === 'text' ? (
        <pre key={entry.id} className={cx('technical-planning-output-text')}>{entry.text}</pre>
      ) : (
        <div key={entry.id} className={cx('technical-planning-output-activity', entry.status, entry.kind)}>
          <span aria-hidden="true">{activityIcon(entry, running)}</span>
          <time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleTimeString('zh-CN', { hour12: false })}</time>
          <span>{entry.text}</span>
        </div>
      ))}
      {running && entries.length ? <div className={cx('technical-planning-output-hint')}><LoadingOutlined spin /> 等待新的输出…</div> : null}
    </div>
  )

  return (
    <section className={cx('technical-planning-output')} data-run-id={runId}>
      <div className={cx('technical-planning-output-header')}>
        <button type="button" className={cx('technical-planning-output-toggle')}
          aria-expanded={expanded} aria-controls={bodyId} onClick={() => setExpanded(!expanded)}>
          <strong>实时输出</strong>
          <span className={cx('technical-planning-output-status', running && 'running')}>{status}</span>
        </button>
        <Tooltip title="复制输出"><Button type="text" size="small" aria-label="复制实时输出" icon={<CopyOutlined />}
          disabled={!entries.length} loading={copying} onClick={() => void copyOutput()} /></Tooltip>
        <Tooltip title="放大查看"><Button type="text" size="small" aria-label="放大实时输出" icon={<ExpandOutlined />}
          onClick={() => setModalOpen(true)} /></Tooltip>
        <Button type="text" size="small" aria-label={expanded ? '收起实时输出' : '展开实时输出'}
          icon={expanded ? <UpOutlined /> : <DownOutlined />} onClick={() => setExpanded(!expanded)} />
      </div>
      <div id={bodyId} hidden={!expanded}>{expanded ? renderOutput() : null}</div>
      <Modal title="技术规划 · 实时输出" open={modalOpen} onCancel={() => setModalOpen(false)} footer={null}
        width="min(1100px, 90vw)" className={cx('technical-planning-output-modal')}>
        {renderOutput()}
      </Modal>
    </section>
  )
}
