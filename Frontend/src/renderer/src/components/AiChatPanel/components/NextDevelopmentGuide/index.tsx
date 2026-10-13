import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  DownOutlined,
  InfoCircleOutlined
} from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useRef, useState, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  developmentTargetTaskId,
  nextDevelopmentTask,
  type DevelopmentNextStep
} from '../../developmentNextStep'
import type { QuickTaskItem } from '../QuickTaskGuide/quickTasks'
import DevelopmentTargetPicker from './DevelopmentTargetPicker'
import { allDevelopmentTargetsCompleted } from './pickerTasks'
import './NextDevelopmentGuide.less'

const { Text } = Typography

type Props = {
  step: DevelopmentNextStep
  tasks: QuickTaskItem[]
  disabled: boolean
  disabledHint?: string
  loading: boolean
  catalogError?: string | null
  onStart: (task: QuickTaskItem) => Promise<void>
}

/** 在当前对话末尾提供开发完成/主动结束后的下一步，并让用户显式选择后才启动新任务。 */
export default function NextDevelopmentGuide({
  step,
  tasks,
  disabled,
  disabledHint,
  loading,
  catalogError,
  onStart
}: Props): ReactElement | null {
  const [expanded, setExpanded] = useState(false)
  const [startingId, setStartingId] = useState('')
  const startingRef = useRef(false)
  const currentId = developmentTargetTaskId(step.target)
  const currentTask = tasks.find((task) => task.id === currentId)
  const nextTask = nextDevelopmentTask(tasks, step.target)
  const targets = tasks.filter((task) => task.kind !== 'entity')
  const remaining = targets.filter((task) => task.progress?.initialDevelopmentStatus !== 'completed')
  const pendingPages = remaining.filter((task) => task.kind === 'page').length
  const pendingApis = remaining.filter((task) => task.kind === 'endpoint').length
  const statusLabel =
    step.reason === 'completed'
      ? `${currentTask?.title || step.target?.label || '当前产物'}初次开发已完成`
      : '本次开发已结束'
  const blocked = disabled || loading || Boolean(catalogError) || Boolean(startingId)
  /** 即时锁住双击；启动失败时保留原引导卡，成功后由新 Workflow 取代旧完成事实。 */
  const handleStart = async (task: QuickTaskItem): Promise<void> => {
    if (blocked || startingRef.current || task.progress?.initialDevelopmentStatus !== 'pending')
      return
    startingRef.current = true
    setStartingId(task.id)
    try {
      await onStart(task)
    } finally {
      startingRef.current = false
      setStartingId('')
    }
  }

  // 不把加载中、目录异常或空目录当成全部完成；完成后由既有测试确认卡负责下一步。
  if (allDevelopmentTargetsCompleted(tasks, loading, catalogError)) return null

  return (
    <section className={cx('next-development-guide')} aria-label="继续开发引导" aria-live="polite">
      <header className={cx('next-development-guide-heading')}>
        <Text>继续完善应用</Text>
        <span className={cx('next-development-guide-status')}>
          {step.reason === 'completed' ? <CheckCircleOutlined aria-hidden="true" /> : null}
          <span>{statusLabel}</span>
        </span>
      </header>
      {loading || catalogError ? (
        <div className={cx('next-development-guide-notice')}>
          <Text type={catalogError ? 'danger' : 'secondary'}>
            {catalogError || '正在读取页面与 API…'}
          </Text>
        </div>
      ) : nextTask ? (
        <div className={cx('next-development-guide-recommendation')}>
          <div className={cx('next-development-guide-target')}>
            <Text type="secondary">推荐下一项 · {nextTask.kind === 'page' ? '页面' : 'API'}</Text>
            <Text strong>{nextTask.title}</Text>
          </div>
          <Button
            className={cx('next-development-guide-start')}
            type="primary"
            aria-label={`开发「${nextTask.title}」`}
            disabled={blocked}
            loading={startingId === nextTask.id}
            onClick={() => void handleStart(nextTask)}
          >
            开始开发 <ArrowRightOutlined />
          </Button>
        </div>
      ) : (
        <div className={cx('next-development-guide-notice')}>
          <Text>{targets.length ? '选择一个目标，继续完善应用' : '暂无可开发的页面或 API'}</Text>
        </div>
      )}
      <footer className={cx('next-development-guide-footer')}>
        <span className={cx('next-development-guide-counts')}>
          {loading || catalogError
            ? '页面与 API'
            : !remaining.length
              ? '暂无开发目标'
              : [
                  pendingPages ? `${pendingPages} 个页面` : '',
                  pendingApis ? `${pendingApis} 个 API` : ''
                ]
                  .filter(Boolean)
                  .join(' · ') + '未完成'}
        </span>
        <Button
          type="text"
          className={cx('next-development-guide-picker-toggle')}
          disabled={loading || Boolean(startingId)}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? '收起全部目标' : '查看全部页面 / API'}
          <DownOutlined rotate={expanded ? 180 : 0} />
        </Button>
      </footer>
      {disabled && disabledHint ? (
        <div className={cx('next-development-guide-lock')}>
          <InfoCircleOutlined aria-hidden="true" />
          <Text type="secondary">{disabledHint}</Text>
        </div>
      ) : null}
      {expanded ? (
        <DevelopmentTargetPicker
          tasks={tasks}
          blocked={blocked}
          startingId={startingId}
          onStart={handleStart}
        />
      ) : null}
    </section>
  )
}
