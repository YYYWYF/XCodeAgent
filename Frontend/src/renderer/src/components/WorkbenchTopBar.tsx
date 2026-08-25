import { Fragment, useState } from 'react'
import { Tag } from 'antd'
import { BlockOutlined, DownOutlined, FolderOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import BrandLogo from './BrandLogo'
import PhaseSwitchConfirmModal from './PhaseSwitchConfirmModal'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import {
  markApplicationEnteredDevelopment,
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../workbenchPhase'
import './WorkbenchTopBar.less'

const PHASE_ORDER: WorkbenchPhase[] = ['product', 'development', 'test', 'review']

type Props = {
  application: ApplicationConfig
  workspaceRoot: string
  theme: 'light' | 'dark'
  onThemeChange: (theme: 'light' | 'dark') => void
  onReturnWelcome: () => void
  lifecycle?: ApplicationLifecycle
  rightPanelOpen: boolean
  onToggleRightPanel: () => void
}

/**
 * 工作台顶部单条：左 = Logo(XCodeAgent)，分隔线后 = 应用卡 + 阶段横排 stepper，
 * 右侧 = 状态提示（当前 Agent + 跟随旅程）+ 主题切换（不相关功能放最右上角）。
 */
export default function WorkbenchTopBar({
  application,
  workspaceRoot,
  theme,
  onThemeChange,
  onReturnWelcome,
  rightPanelOpen,
  onToggleRightPanel
}: Props): JSX.Element {
  const { phase, derivedPhase, manualOverride, switchPhase, agent } = useWorkbenchPhase()
  const following = manualOverride === null
  // 回退切阶段（切到旅程上游 = 增量迭代）需二次确认；向前推进 / 同级直接切。
  const [confirmPhase, setConfirmPhase] = useState<WorkbenchPhase | null>(null)
  const handlePhaseClick = (phaseKey: WorkbenchPhase): void => {
    if (PHASE_ORDER.indexOf(phaseKey) < PHASE_ORDER.indexOf(derivedPhase)) {
      setConfirmPhase(phaseKey)
      return
    }
    // 用户主动切到开发阶段时，标记已确认进入开发（与对话区"进入开发"按钮一致），
    // 避免重挂载后 planningConfirmedAt effect 再次锁回 product。
    if (phaseKey === 'development') {
      markApplicationEnteredDevelopment(application.id)
    }
    switchPhase(phaseKey)
  }

  return (
    <div className={cx('workbench-topbar')}>
      <button
        className={cx('workbench-topbar-logo')}
        onClick={onReturnWelcome}
        title="返回欢迎页"
        type="button"
      >
        <BrandLogo size={22} />
      </button>

      <span className={cx('workbench-topbar-divider')} aria-hidden="true" />

      <button
        className={cx('workbench-topbar-app')}
        onClick={onReturnWelcome}
        title={workspaceRoot}
        type="button"
      >
        <FolderOutlined />
        <span className={cx('workbench-topbar-app-name')}>{application.name}</span>
        <DownOutlined rotate={-90} />
      </button>

      <div className={cx('workbench-topbar-phase')}>
        <div className={cx('workbench-topbar-stepper')} role="tablist" aria-label="阶段">
          {PHASE_ORDER.map((phaseKey, idx) => {
            const isActive = phase === phaseKey
            // 测试确认成功后先由会话切换立即设置当前阶段，生命周期异步回传前也不能把当前按钮置灰。
            const reached = Math.max(PHASE_ORDER.indexOf(derivedPhase), PHASE_ORDER.indexOf(phase)) >= idx
            return (
              <Fragment key={phaseKey}>
                {idx > 0 ? (
                  <span className={cx('workbench-topbar-arrow')} aria-hidden="true">
                    →
                  </span>
                ) : null}
                <button
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  className={cx(
                    'workbench-topbar-phase-item',
                    isActive && 'active',
                    reached && !isActive && 'reached'
                  )}
                  disabled={!reached}
                  onClick={() => handlePhaseClick(phaseKey)}
                >
                  <span className={cx('workbench-topbar-phase-dot')} aria-hidden="true" />
                  {WORKBENCH_PHASE_AGENTS[phaseKey].label}阶段
                </button>
              </Fragment>
            )
          })}
        </div>
      </div>

      <div className={cx('workbench-topbar-tail')}>
        <span className={cx('workbench-topbar-agent')}>{agent.role}</span>
        <Tag
          className={cx('workbench-topbar-follow')}
          color={following ? undefined : 'processing'}
          onClick={following ? undefined : () => switchPhase(null)}
        >
          {following ? '跟随旅程' : '恢复自动'}
        </Tag>
      </div>

      <button
        className={cx('workbench-topbar-preview-toggle', rightPanelOpen && 'active')}
        onClick={onToggleRightPanel}
        title={rightPanelOpen ? '隐藏右侧预览' : '显示右侧预览'}
        type="button"
        aria-label="切换右侧预览"
        aria-pressed={rightPanelOpen}
      >
        <BlockOutlined />
      </button>

      <button
        className={cx('workbench-topbar-theme')}
        onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
        title={theme === 'dark' ? '浅色' : '深色'}
        type="button"
      >
        {theme === 'dark' ? <SunOutlined /> : <MoonOutlined />}
      </button>

      <PhaseSwitchConfirmModal
        fromPhase={derivedPhase}
        onCancel={() => setConfirmPhase(null)}
        onConfirm={(next) => {
          switchPhase(next)
          setConfirmPhase(null)
        }}
        open={confirmPhase !== null}
        toPhase={confirmPhase}
      />
    </div>
  )
}
