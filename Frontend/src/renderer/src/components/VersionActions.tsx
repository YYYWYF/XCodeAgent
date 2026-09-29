import { useState } from 'react'
import { Button, Dropdown } from 'antd'
import { DownOutlined, HistoryOutlined, LockOutlined } from '@ant-design/icons'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import {
  currentBranch,
  findBranch,
  isBranchPublishable,
  isViewingHistoricalBranch
} from '../service/applicationBranches'
import { useUncommittedChanges } from '../context'
import { actionableCandidates, useModuleCandidates } from '../hooks/useModuleCandidates'
import { cx } from '../utils'
import './VersionActions.less'

type Props = {
  application: ApplicationConfig
  /** 合并后的实时 lifecycle：提交推送判定用实时验收态，而非分支快照。 */
  lifecycle?: ApplicationLifecycle
  /** 当前查看的分支名；未传时取 currentBranch（当前分支）。 */
  viewingBranchName?: string
  onPublish: () => void
  onStartIteration: () => void
  onBranchSelect: (branchName: string) => void
  /** 顶栏布局需要把分支选择和审查后的终态动作拆到阶段条两侧。 */
  part?: 'all' | 'selector' | 'terminal'
}

/** 版本状态标签：当前版本可编辑，其余只读。 */
function statusLabelFor(isActive: boolean): string {
  return isActive ? '当前版本' : '只读版本'
}

/** 根据当前生命周期返回「提交并推送」按钮的阻塞原因，避免在验收阶段仍显示审查文案。 */
function releaseBlockedTitle(lifecycle?: ApplicationLifecycle): string {
  const extensions = (lifecycle?.extensions || {}) as Record<string, unknown>
  if (String(extensions.testExecutionStatus || '') !== 'passed')
    return '全部测试用例通过后可提交'
  if (String(extensions.reviewStatus || '') !== 'passed') return '审查通过后可提交'
  if (String(extensions.acceptanceStatus || '') !== 'passed') return '验收通过后可提交'
  return '完成当前版本前置流程后可提交'
}

/**
 * 工作台分支选择与提交推送操作区；查看历史分支不会改变当前分支。
 * 下拉面板单列：每条分支直显完整日志，选中即切换，无额外 hover 面板。
 */
export default function VersionActions({
  application,
  lifecycle,
  viewingBranchName,
  onPublish,
  onStartIteration,
  onBranchSelect,
  part = 'all'
}: Props): JSX.Element | null {
  const [menuOpen, setMenuOpen] = useState(false)
  const { count: uncommittedCount, snapshot: uncommittedSnapshot } = useUncommittedChanges()
  // 候选提交点：已完成、且文件还没提交的模块。展开面板可按模块查看它们的文件。
  // 必须过滤到"还有未提交文件"—— 计划文件只说模块做完过，不说文件是否已提交，
  // 不过滤的话提交后角标会一直挂着，暗示"可以先提交"而实际无事可做。
  // 刷新信号：构建任务逐个完成时 lifecycle revision 会变、文件落盘时 Git 指纹会变，
  // 两者任一变化都重读计划 —— 否则 hook 停在挂载那一刻，构建跑完也不出现候选模块。
  const candidatesRefreshKey = `${lifecycle?.revision ?? ''}:${uncommittedSnapshot?.fingerprint ?? ''}`
  const completedModules = useModuleCandidates(
    application.workspaceRoot || '',
    candidatesRefreshKey
  )
  const candidates = actionableCandidates({
    candidates: completedModules,
    uncommittedPaths: uncommittedSnapshot?.eligiblePaths ?? []
  })
  const allBranches = application.branches || []
  const viewedBranch =
    findBranch(application, viewingBranchName) ||
    currentBranch(application) ||
    allBranches.at(-1)
  if (!viewedBranch) return null

  const currentBranchName = application.branchName || allBranches.at(-1)?.name
  const isViewingCurrentBranch = !isViewingHistoricalBranch(
    currentBranchName || '',
    viewedBranch.name
  )
  const editable = isViewingCurrentBranch
  // 本轮是否已经提交推送过。分支不锁定，所以"提交过没有"不能靠状态判断，用提交事实
  // （gitRef）当标记：发起新迭代时会重置它，于是同一分支上的每一轮都是独立的一轮。
  const roundCommitted = Boolean(viewedBranch.gitRef)
  // 提交推送判定用实时 lifecycle（合并后），避免分支快照冻结验收态。
  const releasable =
    isViewingCurrentBranch && isBranchPublishable(lifecycle || viewedBranch.lifecycle)

  const branchPanel = (
    <div className={cx('version-dropdown')}>
      {/* 候选提交点：模块做完但整体执行未结束时，可按模块查看它们写了哪些文件。
          放在分支列表上方 —— 它是"当前这次构建里可以先提交的东西"，比历史分支更即时。 */}
      {candidates.length > 0 ? (
        <div className={cx('version-dropdown-candidates')}>
          <span className={cx('version-dropdown-candidates-head')}>
            已完成模块（{candidates.length}）
          </span>
          {candidates.map((candidate) => (
            <div key={candidate.unitId} className={cx('version-dropdown-candidate')}>
              <span className={cx('version-dropdown-candidate-title')}>
                {candidate.label}
                <span className={cx('version-dropdown-candidate-count')}>
                  {candidate.files.length} 个文件
                </span>
              </span>
              <span className={cx('version-dropdown-candidate-files')}>
                {candidate.files.map((file) => (
                  <span key={file} title={file}>
                    {file}
                  </span>
                ))}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {[...allBranches].reverse().map((branch) => {
        // 高亮跟随当前查看分支；状态标签跟随当前正在开发的分支。
        const isViewing = branch.name === viewedBranch.name
        const isWorking = branch.name === currentBranchName
        return (
          <button
            key={branch.name}
            type="button"
            className={cx(
              'version-dropdown-item',
              isViewing && 'is-active',
              !isWorking && 'is-released'
            )}
            onClick={() => {
              setMenuOpen(false)
              onBranchSelect(branch.name)
            }}
          >
            <span className={cx('version-dropdown-item-head')}>
              {!isWorking ? <LockOutlined /> : null}
              <span className={cx('version-menu-label')}>{branch.name}</span>
              <span className={cx('version-menu-status')}>{statusLabelFor(isWorking)}</span>
            </span>
            {branch.description ? (
              <span className={cx('version-dropdown-item-log')}>{branch.description}</span>
            ) : null}
          </button>
        )
      })}
    </div>
  )

  const branchSelector = (
    <Dropdown
      visible={menuOpen}
      onVisibleChange={setMenuOpen}
      overlay={branchPanel}
      placement="bottomLeft"
      trigger={['click']}
    >
      <button
        aria-label={`切换版本，当前 ${viewedBranch.name}${
          uncommittedCount > 0 ? `，${uncommittedCount} 个文件未提交` : ''
        }`}
        className={cx('workbench-version-badge', !isViewingCurrentBranch && 'is-released')}
        type="button"
      >
        {!isViewingCurrentBranch ? <LockOutlined /> : null}
        <span className={cx('workbench-version-label')}>{viewedBranch.name}</span>
        {/* 弱提醒：未提交变更数挂在分支入口，切页面/切应用/从托盘回来都能一眼看到。 */}
        {uncommittedCount > 0 ? (
          <span
            className={cx('workbench-version-uncommitted')}
            title={`${uncommittedCount} 个文件未提交`}
          >
            {uncommittedCount}
          </span>
        ) : null}
        {/* 中等提示：有模块做完但整体执行未结束时记一个候选提交点。
            只显示角标、不弹窗，避免打断后续批次；展开面板可看是哪些模块。 */}
        {candidates.length > 0 ? (
          <span
            className={cx('workbench-version-candidate')}
            title={`${candidates.length} 个模块已完成，可在版本面板查看`}
          >
            {candidates.length}
          </span>
        ) : null}
        <DownOutlined className={cx('workbench-version-caret')} />
      </button>
    </Dropdown>
  )

  const terminalAction = (
    <>
      {!isViewingCurrentBranch ? (
        <Button
          className={cx('workbench-rollback-button')}
          icon={<HistoryOutlined />}
          onClick={() => onBranchSelect(currentBranchName || '')}
          size="small"
        >
          回到当前版本
        </Button>
      ) : null}

      {/* 两个动作互斥，一轮迭代只出现一个：
          本轮还没提交 → 只给「提交并推送」；提交过 → 换成「发起新迭代」。
          发起新迭代会重置本轮提交标记（见 WorkbenchPage 的 handleConfirmIteration），
          所以在当前分支上继续迭代后，「提交并推送」会重新出现。 */}
      {editable && !roundCommitted ? (
        <Button
          className={cx(
            'workbench-terminal-action-button',
            'workbench-publish-button',
            releasable && 'ready'
          )}
          size="small"
          type="primary"
          disabled={!releasable}
          onClick={onPublish}
          title={
            releasable
              ? '提交并推送'
              : releaseBlockedTitle(lifecycle || viewedBranch.lifecycle)
          }
        >
          提交并推送
        </Button>
      ) : null}

      {editable && roundCommitted ? (
        <Button
          className={cx('workbench-terminal-action-button', 'workbench-iteration-button')}
          size="small"
          type="primary"
          onClick={onStartIteration}
        >
          发起新迭代
        </Button>
      ) : null}
    </>
  )

  return (
    <div className={cx('workbench-version-actions')}>
      {part === 'all' || part === 'selector' ? branchSelector : null}
      {part === 'all' || part === 'terminal' ? terminalAction : null}
    </div>
  )
}
