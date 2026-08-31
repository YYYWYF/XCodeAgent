import { EditOutlined, RetweetOutlined, UndoOutlined } from '@ant-design/icons'
import { Fragment, useState } from 'react'
import { AppstoreOutlined } from '@ant-design/icons'
import BrandLogo from './BrandLogo'
import PhaseGateModal from './PhaseGateModal'
import VersionActions from './VersionActions'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import {
  WORKBENCH_PHASE_AGENTS,
  WORKBENCH_PHASE_ORDER,
  type WorkbenchPhase
} from '../workbenchPhase'
import type { TestCasePreparationSnapshot } from '../testCasePreparation'
import type { WorkbenchArtifactProgress } from '../workbenchDomain'
import './WorkbenchTopBar.less'

/** 回退上游阶段确认弹框的固定要点说明，与门禁弹框外壳一起构成统一交互。 */
const PHASE_SWITCH_POINTS = [
  {
    icon: <EditOutlined />,
    title: '可编辑上游产物',
    desc: '回到该阶段后，需求文档 / 项目计划等上游产物可重新调整。'
  },
  {
    icon: <RetweetOutlined />,
    title: 'scoped 增量重建',
    desc: '重新进入下游时只重算受影响对象，已完成的部分不会被推倒重来。'
  }
]

type Props = {
  application: ApplicationConfig
  workspaceRoot: string
  onReturnWelcome: () => void
  lifecycle?: ApplicationLifecycle
  activeVersionId?: string
  onPublishVersion: () => void
  onRollbackVersion: (versionId: string) => void
  onStartIteration: () => void
  onVersionSelect: (versionId: string) => void
  /** 测试阶段是否具备进入条件（全部开发产物已完成；“允许进入”不等于“已进入”）。 */
  canEnterTestingStage?: boolean
  /** 用户点击具备进入条件的测试阶段节点时，发起进入测试确认。 */
  onRequestEnterTesting?: () => void
  /** 开发准入门是否待处理（项目计划确认后的进入开发弹框未完成选择）。 */
  canEnterDevelopmentStage?: boolean
  /** 用户点击具备进入条件的开发阶段节点时，再次唤起开发准入门弹框。 */
  onRequestEnterDevelopment?: () => void
  /** 当前版本开发产物完成进度，显示在开发阶段旁。 */
  developmentArtifactProgress?: WorkbenchArtifactProgress
  /** 全部业务用例通过后是否具备进入审查阶段条件。 */
  canEnterReviewStage?: boolean
  /** 用户点击具备进入条件的审查阶段节点时，发起进入审查确认。 */
  onRequestEnterReview?: () => void
  /** 当前版本后台测试用例准备状态。 */
  testCasePreparation?: TestCasePreparationSnapshot
  /** 计划确认后才展示开发产物与测试用例数量。 */
  planConfirmed?: boolean
  /** 打开测试用例工作台。 */
  onOpenTestPreparation?: () => void
  /** 点击测试用例数量芯片时切换后台生成队列抽屉；该入口独立于阶段切换。 */
  onOpenTestCaseQueue?: () => void
  /** 生成队列抽屉当前是否展开，用于芯片的展开态语义。 */
  testCaseQueueOpen?: boolean
}

/**
 * 工作台顶部单条：左侧按应用、版本、六阶段和终态动作递进，右侧保留面板与预览配置。
 */
export default function WorkbenchTopBar({
  application,
  workspaceRoot,
  onReturnWelcome,
  lifecycle,
  activeVersionId,
  onPublishVersion,
  onRollbackVersion,
  onStartIteration,
  onVersionSelect,
  canEnterTestingStage,
  onRequestEnterTesting,
  canEnterDevelopmentStage,
  onRequestEnterDevelopment,
  developmentArtifactProgress,
  canEnterReviewStage,
  onRequestEnterReview,
  testCasePreparation,
  planConfirmed = false,
  onOpenTestPreparation,
  onOpenTestCaseQueue,
  testCaseQueueOpen
}: Props): JSX.Element {
  const { viewingPhase, reachedPhase, locked, switchPhase } = useWorkbenchPhase()
  // 回到前序阶段会改变当前执行状态，需要确认；向后续阶段推进不额外打断。
  const [confirmPhase, setConfirmPhase] = useState<WorkbenchPhase | null>(null)
  /** 处理阶段节点点击：未到达且不具备条件的阶段不可点，其余按回退/进入分流。 */
  const handlePhaseClick = (phaseKey: WorkbenchPhase): void => {
    if (locked) return
    if (phaseKey === viewingPhase) {
      // 当前测试阶段再次点击数字/阶段标签时打开用例明细，不改变阶段位置。
      if (phaseKey === 'testing') onOpenTestPreparation?.()
      return
    }
    const targetIndex = WORKBENCH_PHASE_ORDER.indexOf(phaseKey)
    // 旅程尚未到达开发、但开发准入门已就绪（计划确认后等待选择任务类型）：
    // 点击开发阶段再次唤起准入门弹框，而不是直接切换。
    if (
      targetIndex > WORKBENCH_PHASE_ORDER.indexOf(reachedPhase) &&
      phaseKey === 'development' &&
      canEnterDevelopmentStage
    ) {
      onRequestEnterDevelopment?.()
      return
    }
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

  // 回退确认弹框的展示内容；confirmPhase 为 null（弹框关闭）时使用占位值，弹框不渲染正文影响。
  const confirmToAgent = WORKBENCH_PHASE_AGENTS[confirmPhase ?? 'analysis']
  const confirmFromAgent = WORKBENCH_PHASE_AGENTS[viewingPhase]
  const confirmReturningUpstream =
    WORKBENCH_PHASE_ORDER.indexOf(confirmPhase ?? viewingPhase) <
    WORKBENCH_PHASE_ORDER.indexOf(viewingPhase)

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
              (!locked && phaseKey === 'development' && Boolean(canEnterDevelopmentStage)) ||
              (!locked && phaseKey === 'testing' && Boolean(canEnterTestingStage)) ||
              (!locked && phaseKey === 'review' && Boolean(canEnterReviewStage))
            // 测试用例数量是独立的后台任务入口：即使阶段未到达或按钮禁用，异步生成仍在进行，
            // 因此芯片必须渲染在阶段按钮之外，点击只打开生成队列抽屉，不触发阶段切换。
            const queueChip =
              planConfirmed && phaseKey === 'testing' && testCasePreparation
                ? {
                    label: `${testCasePreparation.generated}/${testCasePreparation.total}`,
                    aria: `测试用例 ${testCasePreparation.generated}/${testCasePreparation.total}，查看生成队列`
                  }
                : null
            const phaseButton = (
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                title={
                  planConfirmed && phaseKey === 'development' && developmentArtifactProgress?.total
                    ? `开发产物 ${developmentArtifactProgress.completed}/${developmentArtifactProgress.total}`
                    : planConfirmed && phaseKey === 'testing' && testCasePreparation
                      ? `测试用例 ${testCasePreparation.generated}/${testCasePreparation.total}`
                      : undefined
                }
                className={cx(
                  'workbench-topbar-phase-item',
                  isActive && 'active',
                  reachable && !isActive && 'reached',
                  queueChip && 'has-queue-chip'
                )}
                aria-disabled={locked || !reachable ? true : undefined}
                disabled={locked || !reachable}
                onClick={() => handlePhaseClick(phaseKey)}
              >
                {WORKBENCH_PHASE_AGENTS[phaseKey].label}阶段
                {planConfirmed && phaseKey === 'development' && developmentArtifactProgress?.total ? (
                  <small
                    aria-label={`开发产物 ${developmentArtifactProgress.completed}/${developmentArtifactProgress.total}`}
                    className={cx(
                      'workbench-development-artifact-progress',
                      developmentArtifactProgress.completed === developmentArtifactProgress.total &&
                        'completed'
                    )}
                  >
                    {developmentArtifactProgress.completed}/{developmentArtifactProgress.total}
                  </small>
                ) : null}
              </button>
            )
            return (
              <Fragment key={phaseKey}>
                {idx > 0 ? (
                  <span className={cx('workbench-topbar-arrow')} aria-hidden="true">
                    →
                  </span>
                ) : null}
                {queueChip ? (
                  <span className={cx('workbench-topbar-phase-cell')}>
                    {phaseButton}
                    <button
                      type="button"
                      aria-label={queueChip.aria}
                      aria-expanded={testCaseQueueOpen}
                      aria-haspopup="dialog"
                      className={cx(
                        'workbench-test-case-progress',
                        testCaseQueueOpen && 'expanded'
                      )}
                      title="测试用例后台生成进度，点击展开或收起生成队列"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpenTestCaseQueue?.()
                      }}
                    >
                      {queueChip.label}
                    </button>
                  </span>
                ) : (
                  phaseButton
                )}
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

      <PhaseGateModal
        confirmDanger={confirmReturningUpstream}
        confirmText="确认切换"
        icon={<UndoOutlined />}
        lead={
          <>
            当前应用在<strong>{confirmFromAgent.label}</strong>阶段。确认后将进入
            <strong>{confirmToAgent.label}</strong>阶段；查看其它阶段的历史任务请使用左侧任务目录。
          </>
        }
        onCancel={() => setConfirmPhase(null)}
        onConfirm={() => {
          if (confirmPhase) switchPhase(confirmPhase)
          setConfirmPhase(null)
        }}
        open={confirmPhase !== null}
        subtitle={confirmReturningUpstream ? '这将返回上游继续调整' : '这将改变当前执行阶段'}
        title={`切换到${confirmToAgent.label}阶段？`}
      >
        <ul className={cx('phase-gate-points')}>
          {PHASE_SWITCH_POINTS.map((point) => (
            <li key={point.title}>
              <span className={cx('phase-gate-point-icon')} aria-hidden="true">
                {point.icon}
              </span>
              <span className={cx('phase-gate-point-copy')}>
                <strong>{point.title}</strong>
                <span>{point.desc}</span>
              </span>
            </li>
          ))}
        </ul>
      </PhaseGateModal>
    </div>
  )
}
