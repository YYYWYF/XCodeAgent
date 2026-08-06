import { Fragment, useState } from 'react'
import { Tag, Typography } from 'antd'
import { DownOutlined, FolderOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { PanelRight } from 'lucide-react'
import BrandLogo from './BrandLogo'
import PhaseSwitchConfirmModal from './PhaseSwitchConfirmModal'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import {
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../workbenchPhase'
import './WorkbenchTopBar.less'

const { Text } = Typography
const PHASE_ORDER: WorkbenchPhase[] = ['product', 'development', 'test']

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
            const reached = PHASE_ORDER.indexOf(derivedPhase) >= idx
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
        <PanelRight size={14} />
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
