import {
  DownOutlined,
  FolderOutlined,
  HourglassOutlined,
  MoonOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import type { CSSProperties, ReactElement } from 'react'
import type { BackgroundTaskSystem } from '../../../../backgroundTasks'
import { BACKGROUND_TASK_SYSTEM_LABEL } from '../../../../backgroundTasks'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import { cx } from '../../../../utils'
import './PhaseNavigation.less'

type Props = {
  /** 打开只读临时对话抽屉。 */
  onOpenTemporaryConversation: () => void
  /** 打开指定任务系统的队列抽屉；两套任务系统各有独立入口。 */
  onOpenBackgroundTasks: (system: BackgroundTaskSystem) => void
  /** 当前展开的任务系统抽屉；用于菜单激活态。 */
  backgroundTasksDrawer?: BackgroundTaskSystem | null
  /** 各系统是否有任务正在执行；为真时对应入口显示运行特效引导用户点开查看。 */
  backgroundTasksRunning?: Record<BackgroundTaskSystem, boolean>
  /** 各系统待验收任务数量；大于 0 时悬浮文案直接给出待处理提示。 */
  backgroundTasksAwaiting?: Record<BackgroundTaskSystem, number>
  /** 打开应用文件工作区。 */
  onShowFiles: () => void
  /** 打开应用配置页。 */
  onShowSettings: () => void
  /** 打开技能页。 */
  onShowSkills: () => void
  /** 当前中间工作区视图，用于标记已打开的功能。 */
  activeView?: 'chat' | 'files' | 'settings' | 'skills'
}

/** 渲染窄侧栏功能按钮，使用浏览器原生 title 提供普通悬浮提示；indicator 为真时叠加运行呼吸点。 */
function RailButton({
  active = false,
  ariaLabel,
  children,
  indicator = false,
  onClick,
  title
}: {
  active?: boolean
  ariaLabel: string
  children: ReactElement
  indicator?: boolean
  onClick: () => void
  title: string
}): ReactElement {
  return (
    <button
      aria-label={ariaLabel}
      className={cx('phase-navigation-button', active && 'active')}
      onClick={onClick}
      title={title}
      type="button"
    >
      {children}
      {indicator ? <span aria-hidden="true" className={cx('phase-navigation-task-dot')} /> : null}
    </button>
  )
}

/** 复用真实工程的 SVG 快捷入口图标，并让图标继承当前主题颜色。 */
function SidebarAssetIcon({ source }: { source: string }): ReactElement {
  return (
    <span
      aria-hidden="true"
      className={cx('phase-navigation-asset-icon')}
      style={{ '--phase-navigation-asset-source': `url("${source}")` } as CSSProperties}
    />
  )
}

/** 渲染固定窄侧栏：顶部为抽屉类入口，用户功能入口与头像沉底分组，中间以弹性空白隔开。 */
export default function PhaseNavigation({
  activeView = 'chat',
  backgroundTasksDrawer,
  backgroundTasksRunning = { async: false, tide: false },
  backgroundTasksAwaiting = { async: 0, tide: 0 },
  onOpenTemporaryConversation,
  onOpenBackgroundTasks,
  onShowFiles,
  onShowSettings,
  onShowSkills
}: Props): ReactElement {
  return (
    <aside aria-label="工作台功能导航" className={cx('phase-navigation')}>
      <nav aria-label="快捷功能" className={cx('phase-navigation-tools')}>
        <RailButton ariaLabel="临时对话" onClick={onOpenTemporaryConversation} title="临时对话">
          <SidebarAssetIcon source={freeChatIcon} />
        </RailButton>
        {/* 异步/潮汐是两套独立任务系统：入口从菜单层就拆开，交互结构保持一致降低认知成本。 */}
        {(['async', 'tide'] as const).map((system) => {
          const running = backgroundTasksRunning[system]
          const awaiting = backgroundTasksAwaiting[system]
          // 入口提示按「待验收 > 执行中 > 常态」的优先级组织，把人工处理入口放在最显眼的位置。
          const title =
            awaiting > 0
              ? `${BACKGROUND_TASK_SYSTEM_LABEL[system]}（${awaiting} 项待验收）`
              : running
                ? `${BACKGROUND_TASK_SYSTEM_LABEL[system]}（有任务正在执行）`
                : BACKGROUND_TASK_SYSTEM_LABEL[system]
          return (
            <RailButton
              active={backgroundTasksDrawer === system}
              ariaLabel={BACKGROUND_TASK_SYSTEM_LABEL[system]}
              indicator={running || awaiting > 0}
              key={system}
              onClick={() => onOpenBackgroundTasks(system)}
              title={title}
            >
              {system === 'async' ? <HourglassOutlined /> : <MoonOutlined />}
            </RailButton>
          )
        })}
        <span aria-hidden="true" className={cx('phase-navigation-divider')} />
      </nav>
      <div className={cx('phase-navigation-spacer')} />
      <nav aria-label="用户功能" className={cx('phase-navigation-user-tools')}>
        <RailButton
          active={activeView === 'files'}
          ariaLabel="文件"
          onClick={onShowFiles}
          title="文件"
        >
          <FolderOutlined />
        </RailButton>
        <RailButton
          active={activeView === 'skills'}
          ariaLabel="技能"
          onClick={onShowSkills}
          title="技能"
        >
          <ThunderboltOutlined />
        </RailButton>
        <RailButton
          active={activeView === 'settings'}
          ariaLabel="设置"
          onClick={onShowSettings}
          title="设置"
        >
          <SettingOutlined />
        </RailButton>
      </nav>
      <button
        aria-label="Steve Jobs"
        className={cx('phase-navigation-user')}
        title="Steve Jobs"
        type="button"
      >
        <span className={cx('phase-navigation-user-avatar')}>S</span>
        <span className={cx('phase-navigation-user-name')}>Steve Jobs</span>
        <DownOutlined />
      </button>
    </aside>
  )
}
