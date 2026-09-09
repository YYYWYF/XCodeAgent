import { CloseOutlined, InboxOutlined, LoadingOutlined, SwapOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import type { BackgroundTaskStatus } from '../../backgroundTasks'
import './index.less'

export type BackgroundTaskItem = {
  /** 展示主标题，例如页面名称或用例名称。 */
  title: string
  /** 统一后台任务状态；抽屉按「进行中 / 已完成」两段呈现。 */
  status: BackgroundTaskStatus
  /** 本条任务自身的业务类型标签，例如「代码实现」「用例生成」。 */
  kindLabel?: string
  /** 覆盖默认状态文案；运行中传执行阶段文案，完成后可传「已就绪」。 */
  statusText?: string
  /** 完成后后续步骤的入口文案（如「验收」）；存在时条目提供启动后续工作流的按钮。 */
  nextStepLabel?: string
  key: string
  /** 后续步骤入口动作：在阶段主对话启动对应的工作流。 */
  onNextStep?: () => void
  /** 后续步骤入口禁用：验收工作流已在其它入口触发时置真，防止重复发起。 */
  nextStepDisabled?: boolean
  /** 排队中任务切换算力队列的按钮文案（如「转到潮汐任务」）；仅未开始的任务提供。 */
  switchLabel?: string
  /** 切换队列动作：把任务迁到另一条算力队列重新排队。 */
  onSwitchQueue?: () => void
}

type BackgroundTaskDrawerProps = {
  open: boolean
  onClose: () => void
  /** 抽屉标题，例如「异步任务」「潮汐任务」。 */
  title: string
  /** 标题下的一句话说明，解释该队列的算力来源与调度方式。 */
  description?: string
  /** 本套系统的顺序队列条目；宿主按系统过滤后传入。 */
  tasks: BackgroundTaskItem[]
  /** 队列为空时的说明文案，由宿主按业务状态提供。 */
  emptyText?: string
  /** 头部徽标图标；宿主按任务系统传入，与菜单入口保持同一套图标语言。 */
  icon?: ReactElement
}

type DrawerTab = 'pending' | 'done'

/** 各状态的默认文案与样式修饰符；进行中状态由加载动画表达，已结束状态只显示文案。 */
const STATUS_META: Record<
  BackgroundTaskStatus,
  { label: string; modifier: string; executing?: boolean }
> = {
  queued: { label: '排队中', modifier: 'queued' },
  running: { label: '执行中', modifier: 'running', executing: true },
  completed: { label: '已完成', modifier: 'done' },
  failed: { label: '失败', modifier: 'failed' },
  cancelled: { label: '已取消', modifier: 'failed' }
}

/**
 * 单套任务系统的队列抽屉：异步任务与潮汐任务各挂一个实例，结构完全一致以降低认知成本。
 * Tab 分「进行中 / 已完成」两段，序号是各段内的队列位次；已完成任务如带后续步骤
 * （如产物验收）则在条目上提供入口，点击在主会话启动对应工作流。
 */
export default function BackgroundTaskDrawer({
  open,
  onClose,
  title,
  description,
  tasks,
  emptyText,
  icon
}: BackgroundTaskDrawerProps): ReactElement {
  const [activeTab, setActiveTab] = useState<DrawerTab>('pending')

  // 按完成与否拆分两段；序号是各段内的队列位次：进行中段队头恒为 1，做完一个整体前移，
  // 已完成段按完成先后编号，体现沉淀次序。
  const pending = tasks.filter((task) => task.status !== 'completed')
  const done = tasks.filter((task) => task.status === 'completed')

  // 展开时回到默认视图：有进行中看待进行，否则看已完成；抽屉停留期间进行中队列清空时
  // 自动切到「已完成」，避免用户盯着空的进行中列表、错过条目上的验收入口。
  useEffect(() => {
    if (!open) return
    setActiveTab(pending.length > 0 ? 'pending' : 'done')
  }, [done.length, open, pending.length])

  // 打开时支持 Escape 快捷关闭，与关闭按钮、入口芯片共同构成完整的关闭路径。
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose])

  const visible = activeTab === 'pending' ? pending : done
  const tabs = [
    { key: 'pending' as const, label: '进行中', count: pending.length },
    { key: 'done' as const, label: '已完成', count: done.length }
  ]

  return (
    <aside
      aria-hidden={!open}
      aria-label={title}
      className={cx('background-task-drawer', open && 'open')}
      role="dialog"
    >
      <header className={cx('background-task-drawer-header')}>
        <span className={cx('background-task-drawer-badge')} aria-hidden="true">
          {icon || <InboxOutlined />}
        </span>
        <div className={cx('background-task-drawer-titles')}>
          <div className={cx('background-task-drawer-title-row')}>
            <strong>{title}</strong>
          </div>
          {description ? <small>{description}</small> : null}
        </div>
        <button
          type="button"
          aria-label="关闭"
          className={cx('background-task-drawer-close')}
          onClick={onClose}
        >
          <CloseOutlined />
        </button>
      </header>

      {tasks.length === 0 ? (
        <p className={cx('background-task-drawer-empty')}>{emptyText || '当前暂无后台任务。'}</p>
      ) : (
        <>
          <nav className={cx('background-task-drawer-tabs')} aria-label="队列分段">
            {tabs.map((tab) => (
              <button
                type="button"
                key={tab.key}
                className={cx('background-task-drawer-tab', activeTab === tab.key && 'active')}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
                <em>{tab.count}</em>
              </button>
            ))}
          </nav>
          {visible.length === 0 ? (
            <p className={cx('background-task-drawer-empty')}>
              {activeTab === 'pending' ? '当前没有进行中的任务。' : '还没有已完成的任务。'}
            </p>
          ) : (
            <ol
              className={cx('background-task-drawer-queue', activeTab === 'done' && 'is-done')}
            >
              {visible.map((task, index) => {
                const meta = STATUS_META[task.status]
                return (
                  <li className={cx('background-task-drawer-item', meta.modifier)} key={task.key}>
                    <span className={cx('background-task-drawer-order')} aria-hidden="true">
                      {index + 1}
                    </span>
                    {task.kindLabel ? (
                      <span className={cx('background-task-drawer-task-kind')}>
                        {task.kindLabel}
                      </span>
                    ) : null}
                    <span className={cx('background-task-drawer-item-title')}>{task.title}</span>
                    {task.status === 'queued' || task.status === 'running' ? (
                      <span className={cx('background-task-drawer-status')}>
                        {/* 状态文案统一在前，加载动画/切换入口等图标统一在后，保证一列对齐。 */}
                        {task.statusText || meta.label}
                        {meta.executing ? <LoadingOutlined spin aria-hidden="true" /> : null}
                        {task.switchLabel && task.onSwitchQueue ? (
                          <button
                            type="button"
                            aria-label={task.switchLabel}
                            title={task.switchLabel}
                            className={cx('background-task-drawer-switch')}
                            onClick={task.onSwitchQueue}
                          >
                            <SwapOutlined aria-hidden="true" />
                          </button>
                        ) : null}
                      </span>
                    ) : (
                      <span className={cx('background-task-drawer-status')}>
                        {/* 已完成分栏本身就是默认完成态，不重复展示；仅保留“已就绪”等额外业务状态。 */}
                        {task.statusText || null}
                        {task.nextStepLabel && task.onNextStep ? (
                          <button
                            type="button"
                            className={cx('background-task-drawer-accept')}
                            disabled={task.nextStepDisabled}
                            onClick={task.onNextStep}
                          >
                            {/* 禁用时保留原文案（仅置灰），保证各条目按钮纵向视觉对齐。 */}
                            {task.nextStepLabel}
                          </button>
                        ) : null}
                      </span>
                    )}
                  </li>
                )
              })}
            </ol>
          )}
        </>
      )}
    </aside>
  )
}
