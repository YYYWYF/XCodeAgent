import { Fragment, useState } from 'react'
import { AppstoreOutlined } from '@ant-design/icons'
import { MonitorPlay, PanelRight } from 'lucide-react'
import BrandLogo from './BrandLogo'
import PhaseSwitchConfirmModal from './PhaseSwitchConfirmModal'
import VersionActions from './VersionActions'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import {
  WORKBENCH_PHASE_AGENTS,
  WORKBENCH_PHASE_ORDER,
  type WorkbenchPhase
} from '../workbenchPhase'
import './WorkbenchTopBar.less'

type Props = {
  application: ApplicationConfig
  workspaceRoot: string
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
  /** 测试阶段是否具备进入条件（全部开发产物已完成；“允许进入”不等于“已进入”）。 */
  canEnterTestingStage?: boolean
  /** 用户点击具备进入条件的测试阶段节点时，发起进入测试确认。 */
  onRequestEnterTesting?: () => void
  /** 测试报告通过后是否具备进入审查阶段条件。 */
  canEnterReviewStage?: boolean
  /** 用户点击具备进入条件的审查阶段节点时，发起进入审查确认。 */
  onRequestEnterReview?: () => void
}

/**
 * 工作台顶部单条：左侧按应用、版本、五阶段和终态动作递进，右侧保留面板与预览配置。
 */
export default function WorkbenchTopBar({
  application,
  workspaceRoot,
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
  canEnterTestingStage,
  onRequestEnterTesting,
  canEnterReviewStage,
  onRequestEnterReview
}: Props): JSX.Element {
  const { viewingPhase, reachedPhase, locked, switchPhase } = useWorkbenchPhase()
  // 回到前序阶段会改变当前执行状态，需要确认；向后续阶段推进不额外打断。
  const [confirmPhase, setConfirmPhase] = useState<WorkbenchPhase | null>(null)
  /** 处理阶段节点点击：未到达且不具备条件的阶段不可点，其余按回退/进入分流。 */
  const handlePhaseClick = (phaseKey: WorkbenchPhase): void => {
    if (locked || phaseKey === viewingPhase) return
    const targetIndex = WORKBENCH_PHASE_ORDER.indexOf(phaseKey)
    // 旅程尚未到达测试、但开发产物已全部完成：允许进入，点击先走确认弹框而不是直接切视图。
    if (
      targetIndex > WORKBENCH_PHASE_ORDER.indexOf(reachedPhase) &&
      phaseKey === 'testing' &&
      canEnterTestingStage
    ) {
      onRequestEnterTesting?.()
      return
    }
    if (
      targetIndex > WORKBENCH_PHASE_ORDER.indexOf(reachedPhase) &&
      phaseKey === 'review' &&
      canEnterReviewStage
    ) {
      onRequestEnterReview?.()
      return
    }
    if (targetIndex < WORKBENCH_PHASE_ORDER.indexOf(viewingPhase)) {
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

      <VersionActions
        activeVersionId={activeVersionId}
        application={application}
        lifecycle={lifecycle}
        onPublish={onPublishVersion}
        onRollback={onRollbackVersion}
        onStartIteration={onStartIteration}
        onVersionSelect={onVersionSelect}
        part="selector"
      />

      <div
        className={cx('workbench-topbar-phase', locked && 'locked')}
        title={locked ? '该版本已生成，阶段和 Agent 调度均已锁定' : undefined}
      >
        <div className={cx('workbench-topbar-stepper')} role="tablist" aria-label="阶段">
          {WORKBENCH_PHASE_ORDER.map((phaseKey, idx) => {
            // 锁定版本只展示阶段旅程，不保留任何选中态；选中态仅属于可编辑版本。
            const isActive = !locked && viewingPhase === phaseKey
            const reached = WORKBENCH_PHASE_ORDER.indexOf(reachedPhase) >= idx
            // 测试阶段具备进入条件时视同“已到达”：沿用可点击的未选中样式，不新增视觉状态。
            const reachable =
              reached ||
              (!locked && phaseKey === 'testing' && Boolean(canEnterTestingStage)) ||
              (!locked && phaseKey === 'review' && Boolean(canEnterReviewStage))
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
                  aria-disabled={locked || !reachable ? true : undefined}
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
      </div>

      <span
        aria-hidden="true"
        className={cx('workbench-topbar-arrow', 'workbench-topbar-terminal-arrow')}
      >
        →
      </span>

      <VersionActions
        activeVersionId={activeVersionId}
        application={application}
        lifecycle={lifecycle}
        onPublish={onPublishVersion}
        onRollback={onRollbackVersion}
        onStartIteration={onStartIteration}
        onVersionSelect={onVersionSelect}
        part="terminal"
      />

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

      <PhaseSwitchConfirmModal
        fromPhase={viewingPhase}
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
