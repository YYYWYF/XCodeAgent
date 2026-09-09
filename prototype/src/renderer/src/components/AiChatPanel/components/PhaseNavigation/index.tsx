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
  /** 打开任务管理抽屉：统一管理当前阶段任务与临时问答。 */
  onOpenConversationManagement: () => void
  /** 任务管理抽屉是否展开；用于菜单激活态。 */
  conversationDrawerOpen?: boolean
  /** 打开指定任务系统的队列抽屉；两套任务系统各有独立入口。 */
  onOpenBackgroundTasks: (system: BackgroundTaskSystem) => void
  /** 当前展开的任务系统抽屉；用于菜单激活态。 */
  backgroundTasksDrawer?: BackgroundTaskSystem | null
  /** 各系统是否有任务正在执行；为真时对应入口显示运行特效引导用户点开查看。 */
  backgroundTasksRunning?: Record<BackgroundTaskSystem, boolean>
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
  conversationDrawerOpen = false,
  onOpenConversationManagement,
  onOpenBackgroundTasks,
  onShowFiles,
  onShowSettings,
  onShowSkills
}: Props): ReactElement {
  return (
    <aside aria-label="工作台功能导航" className={cx('phase-navigation')}>
      <nav aria-label="快捷功能" className={cx('phase-navigation-tools')}>
        {/* 任务管理是第一入口：统一承载阶段任务切换、新建与临时问答。 */}
        <RailButton
          active={conversationDrawerOpen}
          ariaLabel="任务管理"
          onClick={onOpenConversationManagement}
          title="任务管理"
        >
          <SidebarAssetIcon source={freeChatIcon} />
        </RailButton>
        {/* 异步/潮汐是两套独立任务系统：入口从菜单层就拆开，交互结构保持一致降低认知成本。 */}
        {(['async', 'tide'] as const).map((system) => {
          const running = backgroundTasksRunning[system]
          // 左侧只提醒真正运行中的后台任务；待继续任务统一收敛到输入区工作流入口。
          const title =
            running
              ? `${BACKGROUND_TASK_SYSTEM_LABEL[system]}（有任务正在执行）`
              : BACKGROUND_TASK_SYSTEM_LABEL[system]
          return (
            <RailButton
              active={backgroundTasksDrawer === system}
              ariaLabel={BACKGROUND_TASK_SYSTEM_LABEL[system]}
              indicator={running}
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
