import { Fragment, useEffect, useRef, useState } from 'react'
import { message, Tag } from 'antd'
import { BlockOutlined, LeftOutlined, FolderOutlined } from '@ant-design/icons'
import BrandLogo from './BrandLogo'
import PhaseSwitchConfirmModal from './PhaseSwitchConfirmModal'
import VersionActions from './VersionActions'
import { useWorkbenchPhase } from '../context'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import { testEntryGateReason } from '../developmentArtifacts'
import { WORKBENCH_PHASE_ORDER as PHASE_ORDER } from '../workbenchPhaseNavigation'
import {
  markApplicationEnteredDevelopment,
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../workbenchPhase'
import './WorkbenchTopBar.less'

type Props = {
  application: Pick<ApplicationConfig, 'id' | 'name' | 'versions' | 'currentVersionId'>
  workspaceRoot: string
  onReturnWelcome: () => void
  lifecycle?: ApplicationLifecycle
  rightPanelOpen: boolean
  onToggleRightPanel: () => void
  /** 生成新版本：打开生成版本弹框。 */
  onPublishVersion?: () => void
  /** 基于历史版本迭代：打开回退弹框。 */
  onRollbackVersion?: (versionId: string) => void
  /** 发起新迭代：打开迭代弹框。 */
  onStartIteration?: () => void
  /** 切换查看版本。 */
  onVersionSelect?: (versionId: string) => void
  /** 当前查看的版本 id。 */
  viewingVersionId?: string
  /** 正在回看历史版本：右侧的 Agent 身份、跟随开关与预览开关都不适用，整组隐藏。 */
  versionReadOnly?: boolean
}

/**
 * 工作台顶部单条：左 = Logo(DevAgent Studio)，分隔线后 = 应用卡 + 阶段横排 stepper，
 * 右侧 = 状态提示（当前 Agent + 跟随旅程）+ 预览开关，主题入口统一放在左侧快捷栏。
 */
export default function WorkbenchTopBar({
  application,
  lifecycle,
  workspaceRoot,
  onReturnWelcome,
  rightPanelOpen,
  onToggleRightPanel,
  onPublishVersion,
  onRollbackVersion,
  onStartIteration,
  onVersionSelect,
  viewingVersionId,
  versionReadOnly = false
}: Props): JSX.Element {
  const {
    phase,
    derivedPhase,
    reachedPhase,
    manualOverride,
    switchPhase,
    agent,
    testEntryGate,
    locked
  } = useWorkbenchPhase()
  const following = manualOverride === null
  const previousPhaseRef = useRef<WorkbenchPhase | null>(null)

  // 进入开发阶段时提醒用户通过服务状态抽屉手动启动前后端预览服务。
  useEffect(() => {
    // lifecycle 未同步完成时阶段默认为 development，等待权威状态避免误提示。
    if (!lifecycle && manualOverride === null) return
    if (phase === 'development' && previousPhaseRef.current !== 'development') {
      message.info('可在预览->服务状态内点击启动服务按钮进行前后端服务启动并预览。')
    }
    previousPhaseRef.current = phase
  }, [lifecycle, manualOverride, phase])

  // 回退切阶段（切到旅程上游 = 增量迭代）需二次确认；向前推进 / 同级直接切。
  const [confirmPhase, setConfirmPhase] = useState<WorkbenchPhase | null>(null)
  const handlePhaseClick = (phaseKey: WorkbenchPhase): void => {
    if (locked) return
    if (PHASE_ORDER.indexOf(phaseKey) < PHASE_ORDER.indexOf(derivedPhase)) {
      setConfirmPhase(phaseKey)
      return
    }
    // 用户主动切到开发阶段时，标记已确认进入开发（与对话区"进入开发"按钮一致），
    // 避免重挂载后自动阶段推导再次回到 product。作用域为当前迭代版本。
    if (phaseKey === 'development') {
      markApplicationEnteredDevelopment(
        application.id,
        application.currentVersionId || application.id
      )
    }
    switchPhase(phaseKey)
  }

  return (
    <div className={cx('workbench-topbar')}>
      <div className={cx('workbench-topbar-logo')}>
        <BrandLogo size={22} />
      </div>

      <span className={cx('workbench-topbar-divider')} aria-hidden="true" />

      <button
        className={cx('workbench-topbar-app')}
        onClick={onReturnWelcome}
        title={`返回欢迎页 · ${workspaceRoot}`}
        aria-label={`${application.name}，返回欢迎页`}
        type="button"
      >
        <LeftOutlined aria-hidden="true" />
        <FolderOutlined />
        <span className={cx('workbench-topbar-app-name')}>{application.name}</span>
      </button>

      {application.versions && application.versions.length > 0 ? (
        <VersionActions
          application={application as ApplicationConfig}
          lifecycle={lifecycle}
          viewingVersionId={viewingVersionId}
          onPublish={onPublishVersion || (() => {})}
          onRollback={onRollbackVersion || (() => {})}
          onStartIteration={onStartIteration || (() => {})}
          onVersionSelect={onVersionSelect || (() => {})}
          part="selector"
        />
      ) : null}

      <div className={cx('workbench-topbar-phase', locked && 'locked')}>
        <div className={cx('workbench-topbar-stepper')} role="tablist" aria-label="阶段">
          {PHASE_ORDER.map((phaseKey, idx) => {
            // 已生成版本（locked）也要高亮当前阶段：它只读、不可点，但仍要指明这个版本
            // 停在哪个阶段（历史版本冻结在验收），否则阶段条上没有任何位置提示。
            const isActive = phase === phaseKey
            // 回访资格使用独立的到达记录，不能随当前视图回退或 execution 收口而降低。
            const reached = PHASE_ORDER.indexOf(reachedPhase) >= idx
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
                  disabled={
                    locked || (phaseKey === 'test' ? testEntryGate?.allowed !== true : !reached)
                  }
                  title={
                    locked
                      ? '该版本已生成，阶段和 Agent 调度均已锁定'
                      : phaseKey === 'test'
                        ? testEntryGateReason(testEntryGate)
                        : undefined
                  }
                  onClick={() => handlePhaseClick(phaseKey)}
                >
                  <span className={cx('workbench-topbar-phase-dot')} aria-hidden="true" />
                  {WORKBENCH_PHASE_AGENTS[phaseKey].label}阶段
                  {phaseKey === 'development' && testEntryGate ? (
                    <span>
                      {testEntryGate.completed}/{testEntryGate.total}
                    </span>
                  ) : null}
                </button>
              </Fragment>
            )
          })}
        </div>
      </div>

      {application.versions && application.versions.length > 0 ? (
        <>
          <span
            aria-hidden="true"
            className={cx('workbench-topbar-arrow', 'workbench-topbar-terminal-arrow')}
          >
            →
          </span>
          <VersionActions
            application={application as ApplicationConfig}
            lifecycle={lifecycle}
            viewingVersionId={viewingVersionId}
            onPublish={onPublishVersion || (() => {})}
            onRollback={onRollbackVersion || (() => {})}
            onStartIteration={onStartIteration || (() => {})}
            onVersionSelect={onVersionSelect || (() => {})}
            part="terminal"
          />
        </>
      ) : null}

      {/* 历史版本只读回看：Agent 身份、跟随开关与预览开关都指向"当前迭代的推进"，
          在这里既无意义也无处可去，整组隐藏。 */}
      {!versionReadOnly ? (
        <>
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
        </>
      ) : null}

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
