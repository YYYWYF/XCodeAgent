import { useState } from 'react'
import { Button, Dropdown } from 'antd'
import {
  DownOutlined,
  HistoryOutlined,
  LockOutlined
} from '@ant-design/icons'
import type { ApplicationConfig, ApplicationLifecycle, ApplicationVersion } from '../typings'
import {
  currentVersion,
  findVersion,
  isVersionEditable,
  isVersionReleasable
} from '../service/applicationVersions'
import { cx } from '../utils'
import './VersionActions.less'

type Props = {
  application: ApplicationConfig
  /** 合并后的实时 lifecycle：生成版本判定用实时验收态，而非版本快照。 */
  lifecycle?: ApplicationLifecycle
  activeVersionId?: string
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

/**
 * 工作台版本选择与生成版本操作区；查看历史版本不会改变当前单向版本头。
 * 下拉面板单列：每个版本直显完整日志，选中即切换，无额外 hover 面板。
 */
export default function VersionActions({
  application,
  lifecycle,
  activeVersionId,
  onPublish,
  onRollback,
  onStartIteration,
  onVersionSelect,
  part = 'all'
}: Props): JSX.Element | null {
  const [menuOpen, setMenuOpen] = useState(false)
  const viewedVersion = currentVersion(application)
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
        aria-label={`切换版本，当前 ${viewedVersion.versionLabel}`}
        className={cx(
          'workbench-version-badge',
          viewedVersion.status === 'released' && 'is-released'
        )}
        type="button"
      >
        {viewedVersion.status === 'released' ? <LockOutlined /> : null}
        <span className={cx('workbench-version-label')}>{viewedVersion.versionLabel}</span>
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
          title={releasable ? '生成新版本' : '完成代码审查后可生成新版本'}
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
