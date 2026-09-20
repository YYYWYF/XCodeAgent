import { useState } from 'react'
import { Button, Dropdown } from 'antd'
import { DownOutlined, HistoryOutlined, LockOutlined } from '@ant-design/icons'
import type { ApplicationConfig, ApplicationLifecycle, ApplicationVersion } from '../typings'
import {
  currentVersion,
  findVersion,
  isVersionEditable,
  isVersionReleasable
} from '../service/applicationVersions'
import { useUncommittedChanges } from '../context'
import { actionableCandidates, useModuleCandidates } from '../hooks/useModuleCandidates'
import { cx } from '../utils'
import './VersionActions.less'

type Props = {
  application: ApplicationConfig
  /** 合并后的实时 lifecycle：生成版本判定用实时验收态，而非版本快照。 */
  lifecycle?: ApplicationLifecycle
  activeVersionId?: string
  /** 当前查看的版本 id；未传时取 currentVersion（活跃版本）。 */
  viewingVersionId?: string
  onPublish: () => void
  onRollback: (versionId: string) => void
  onStartIteration: () => void
  onVersionSelect: (versionId: string) => void
  /** 顶栏布局需要把版本选择和审查后的终态动作拆到阶段条两侧。 */
  part?: 'all' | 'selector' | 'terminal'
}

function statusLabelFor(version: ApplicationVersion, isActive: boolean): string {
  if (isActive) return version.status === 'released' ? '最新版本' : '当前迭代'
  return version.status === 'released' ? '已生成版本' : '已保存'
}

/** 根据当前生命周期返回生成版本按钮的阻塞原因，避免在验收阶段仍显示审查文案。 */
function releaseBlockedTitle(lifecycle?: ApplicationLifecycle): string {
  const extensions = (lifecycle?.extensions || {}) as Record<string, unknown>
  if (String(extensions.testExecutionStatus || '') !== 'passed')
    return '全部测试用例通过后可生成新版本'
  if (String(extensions.reviewStatus || '') !== 'passed') return '审查通过后可生成新版本'
  if (String(extensions.acceptanceStatus || '') !== 'passed') return '验收通过后可生成新版本'
  return '完成当前版本前置流程后可生成新版本'
}

/**
 * 工作台版本选择与生成版本操作区；查看历史版本不会改变当前单向版本头。
 * 下拉面板单列：每个版本直显完整日志，选中即切换，无额外 hover 面板。
 */
export default function VersionActions({
  application,
  lifecycle,
  activeVersionId,
  viewingVersionId,
  onPublish,
  onRollback,
  onStartIteration,
  onVersionSelect,
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
  const viewedVersion =
    findVersion(application, viewingVersionId || '') || currentVersion(application)
  if (!viewedVersion) return null

  const allVersions = application.versions || []
  const activeVersion = findVersion(application, activeVersionId || '') || allVersions.at(-1)
  const isViewingActiveVersion = viewedVersion.id === activeVersion?.id
  const editable = isViewingActiveVersion && isVersionEditable(viewedVersion)
  // 生成版本判定用实时 lifecycle（合并后），避免版本快照冻结验收态。
  const releasable = isVersionReleasable({
    ...viewedVersion,
    lifecycle: lifecycle || viewedVersion.lifecycle
  })

  const versionPanel = (
    <div className={cx('version-dropdown')}>
      {/* 候选提交点：模块做完但整体执行未结束时，可按模块查看它们写了哪些文件。
          放在版本列表上方 —— 它是"当前这次构建里可以先提交的东西"，比历史版本更即时。 */}
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
      {[...allVersions].reverse().map((version) => {
        // 高亮跟随当前查看版本(viewedVersion);状态标签跟随当前工作版本(activeVersion)。
        const isViewing = version.id === viewedVersion.id
        const isWorking = version.id === activeVersion?.id
        return (
          <button
            key={version.id}
            type="button"
            className={cx(
              'version-dropdown-item',
              isViewing && 'is-active',
              version.status === 'released' && 'is-released'
            )}
            onClick={() => {
              setMenuOpen(false)
              onVersionSelect(version.id)
            }}
          >
            <span className={cx('version-dropdown-item-head')}>
              {version.status === 'released' ? <LockOutlined /> : null}
              <span className={cx('version-menu-label')}>{version.versionLabel}</span>
              <span className={cx('version-menu-status')}>
                {statusLabelFor(version, isWorking)}
              </span>
            </span>
            {version.description ? (
              <span className={cx('version-dropdown-item-log')}>{version.description}</span>
            ) : null}
          </button>
        )
      })}
    </div>
  )

  const versionSelector = (
    <Dropdown
      visible={menuOpen}
      onVisibleChange={setMenuOpen}
      overlay={versionPanel}
      placement="bottomLeft"
      trigger={['click']}
    >
      <button
        aria-label={`切换版本，当前 ${viewedVersion.versionLabel}${
          uncommittedCount > 0 ? `，${uncommittedCount} 个文件未提交` : ''
        }`}
        className={cx(
          'workbench-version-badge',
          viewedVersion.status === 'released' && 'is-released'
        )}
        type="button"
      >
        {viewedVersion.status === 'released' ? <LockOutlined /> : null}
        <span className={cx('workbench-version-label')}>{viewedVersion.versionLabel}</span>
        {/* 弱提醒：未提交变更数挂在版本入口，切页面/切应用/从托盘回来都能一眼看到。 */}
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
      {!isViewingActiveVersion ? (
        <Button
          className={cx('workbench-rollback-button')}
          icon={<HistoryOutlined />}
          onClick={() => onRollback(viewedVersion.id)}
          size="small"
        >
          基于此版本迭代
        </Button>
      ) : null}

      {editable ? (
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
            releasable ? '生成新版本' : releaseBlockedTitle(lifecycle || viewedVersion.lifecycle)
          }
        >
          生成新版本
        </Button>
      ) : null}

      {isViewingActiveVersion && !editable && viewedVersion.status === 'released' ? (
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
      {part === 'all' || part === 'selector' ? versionSelector : null}
      {part === 'all' || part === 'terminal' ? terminalAction : null}
    </div>
  )
}
