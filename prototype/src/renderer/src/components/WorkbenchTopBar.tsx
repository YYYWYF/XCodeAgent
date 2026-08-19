import { Fragment, useState } from 'react'
import { Tag } from 'antd'
import { AppstoreOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { MonitorPlay, PanelRight } from 'lucide-react'
import BrandLogo from './BrandLogo'
import PhaseSwitchConfirmModal from './PhaseSwitchConfirmModal'
import VersionActions from './VersionActions'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import { WORKBENCH_PHASE_AGENTS, type WorkbenchPhase } from '../workbenchPhase'
import './WorkbenchTopBar.less'

const PHASE_ORDER: WorkbenchPhase[] = ['product', 'development', 'test']

type Props = {
  application: ApplicationConfig
  workspaceRoot: string
  theme: 'light' | 'dark'
  onThemeChange: (theme: 'light' | 'dark') => void
  onReturnWelcome: () => void
  lifecycle?: ApplicationLifecycle
  activeVersionId?: string
  applicationPreviewMode: boolean
  onApplicationPreviewModeChange: (open: boolean) => void
  rightPanelOpen: boolean
  onToggleRightPanel: () => void
  onPublishVersion: () => void
  onRollbackVersion: (versionId: string) => void
  onStartIteration: () => void
  onVersionSelect: (versionId: string) => void
  /** 审查阶段是否具备进入条件（全部开发产物已完成；“允许进入”不等于“已进入”）。 */
  canEnterReviewStage?: boolean
  /** 用户点击具备进入条件的审查阶段节点时，发起进入审查确认。 */
  onRequestEnterReview?: () => void
}

/**
 * 工作台顶部单条：左 = Logo(XCodeAgent)，分隔线后 = 应用卡 + 阶段横排 stepper，
 * 右侧 = 状态提示 + 预览 + 主题切换 + 版本选择与发布。
 */
export default function WorkbenchTopBar({
  application,
  workspaceRoot,
  theme,
  onThemeChange,
  onReturnWelcome,
  lifecycle,
  activeVersionId,
  applicationPreviewMode,
  onApplicationPreviewModeChange,
  rightPanelOpen,
  onToggleRightPanel,
  onPublishVersion,
  onRollbackVersion,
  onStartIteration,
  onVersionSelect,
  canEnterReviewStage,
  onRequestEnterReview
}: Props): JSX.Element {
  const { phase, derivedPhase, locked, manualOverride, switchPhase } = useWorkbenchPhase()
  const following = manualOverride === null
  // 回到前序阶段会改变当前执行状态，需要确认；向后续阶段推进不额外打断。
  const [confirmPhase, setConfirmPhase] = useState<WorkbenchPhase | null>(null)
  /** 处理阶段节点点击：未到达且不具备条件的阶段不可点，其余按回退/进入分流。 */
  const handlePhaseClick = (phaseKey: WorkbenchPhase): void => {
    if (locked || phaseKey === phase) return
    const targetIndex = PHASE_ORDER.indexOf(phaseKey)
    // 旅程尚未到达审查、但开发产物已全部完成：允许进入，点击先走确认弹框而不是直接切视图。
    if (targetIndex > PHASE_ORDER.indexOf(derivedPhase) && phaseKey === 'test' && canEnterReviewStage) {
      onRequestEnterReview?.()
      return
    }
    if (targetIndex < PHASE_ORDER.indexOf(phase)) {
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

      <div className={cx('workbench-topbar-app')} title={workspaceRoot}>
        <AppstoreOutlined />
        <span className={cx('workbench-topbar-app-name')}>{application.name}</span>
      </div>
      <div
        className={cx('workbench-topbar-phase', locked && 'locked')}
        title={locked ? '该版本已生成，阶段和 Agent 调度均已锁定' : undefined}
      >
        <div className={cx('workbench-topbar-stepper')} role="tablist" aria-label="阶段">
          {PHASE_ORDER.map((phaseKey, idx) => {
            const isActive = phase === phaseKey
            const reached = PHASE_ORDER.indexOf(derivedPhase) >= idx
            // 审查阶段具备进入条件时视同“已到达”：沿用可点击的未选中样式，不新增视觉状态。
            const reachable =
              reached || (!locked && phaseKey === 'test' && Boolean(canEnterReviewStage))
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
                    reachable && !isActive && 'reached'
                  )}
                  disabled={locked || !reachable}
                  onClick={() => handlePhaseClick(phaseKey)}
                >
                  <span className={cx('workbench-topbar-phase-index')} aria-hidden="true">
                    {idx + 1}
                  </span>
                  {WORKBENCH_PHASE_AGENTS[phaseKey].label}阶段
                </button>
              </Fragment>
            )
          })}
        </div>
        <Tag
          className={cx('workbench-topbar-follow')}
          color={following ? undefined : 'processing'}
          onClick={locked || following ? undefined : () => switchPhase(null)}
        >
          {locked ? '版本已锁定' : following ? '跟随旅程' : '恢复自动'}
        </Tag>
      </div>

      <button
        aria-label={rightPanelOpen ? '收起右侧面板' : '展开右侧面板'}
        aria-pressed={rightPanelOpen}
        className={cx('workbench-topbar-panel-toggle', rightPanelOpen && 'active')}
        disabled={applicationPreviewMode}
        onClick={onToggleRightPanel}
        title={
          applicationPreviewMode
            ? '应用预览模式不使用任务面板'
            : rightPanelOpen
              ? '收起右侧面板'
              : '展开右侧面板'
        }
        type="button"
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

      <VersionActions
        activeVersionId={activeVersionId}
        application={application}
        lifecycle={lifecycle}
        onPublish={onPublishVersion}
        onRollback={onRollbackVersion}
        onStartIteration={onStartIteration}
        onVersionSelect={onVersionSelect}
        previewAction={
          <button
            aria-label={applicationPreviewMode ? '返回任务工作区' : '预览应用'}
            aria-pressed={applicationPreviewMode}
            className={cx('workbench-topbar-preview-toggle', applicationPreviewMode && 'active')}
            onClick={() => onApplicationPreviewModeChange(!applicationPreviewMode)}
            title={applicationPreviewMode ? '返回任务工作区' : '预览完整应用'}
            type="button"
          >
            <MonitorPlay size={14} />
            <span>{applicationPreviewMode ? '返回任务' : '预览应用'}</span>
          </button>
        }
      />

      <PhaseSwitchConfirmModal
        fromPhase={phase}
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
