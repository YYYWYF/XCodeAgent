import { Button, Dropdown, Menu } from 'antd'
import {
  DownOutlined,
  HistoryOutlined,
  LockOutlined,
  PlusOutlined,
  RocketOutlined
} from '@ant-design/icons'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
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
  /** 合并后的实时 lifecycle：发布判定用实时验收态，而非版本快照。 */
  lifecycle?: ApplicationLifecycle
  activeVersionId?: string
  onPublish: () => void
  onRollback: (versionId: string) => void
  onStartIteration: () => void
  onVersionSelect: (versionId: string) => void
}

/**
 * 工作台版本选择与发布操作区；查看历史版本不会改变当前单向版本头。
 */
export default function VersionActions({
  application,
  lifecycle,
  activeVersionId,
  onPublish,
  onRollback,
  onStartIteration,
  onVersionSelect
}: Props): JSX.Element | null {
  const viewedVersion = currentVersion(application)
  if (!viewedVersion) return null

  const allVersions = application.versions || []
  const activeVersion = findVersion(application, activeVersionId || '') || allVersions.at(-1)
  const isViewingActiveVersion = viewedVersion.id === activeVersion?.id
  const editable = isViewingActiveVersion && isVersionEditable(viewedVersion)
  // 发布判定用实时 lifecycle（合并后），避免版本快照冻结验收态。
  const releasable = isVersionReleasable({
    ...viewedVersion,
    lifecycle: lifecycle || viewedVersion.lifecycle
  })
  const versionMenu = (
    <Menu
      onClick={({ key }) => onVersionSelect(String(key))}
      selectedKeys={[viewedVersion.id]}
    >
      {[...allVersions].reverse().map((version) => {
        const isActive = version.id === activeVersion?.id
        const statusLabel = isActive
          ? version.status === 'released'
            ? '最新已发布'
            : '当前迭代'
          : version.status === 'released'
            ? '已发布'
            : '已保存'
        return (
          <Menu.Item key={version.id}>
            <span className={cx('version-menu-item', version.status === 'released' && 'is-released')}>
              {version.status === 'released' ? <LockOutlined /> : null}
              <span className={cx('version-menu-label')}>{version.versionLabel}</span>
              <span className={cx('version-menu-status')}>{statusLabel}</span>
            </span>
          </Menu.Item>
        )
      })}
    </Menu>
  )

  return (
    <div className={cx('workbench-version-actions')}>
      <Dropdown overlay={versionMenu} placement="bottomRight" trigger={['click']}>
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
          className={cx('workbench-publish-button', releasable && 'ready')}
          size="small"
          type="primary"
          disabled={!releasable}
          icon={<RocketOutlined />}
          onClick={onPublish}
          title={releasable ? '发布当前版本' : '完成代码审查后可发布'}
        >
          发布
        </Button>
      ) : null}

      {isViewingActiveVersion && !editable && viewedVersion.status === 'released' ? (
        <Button size="small" type="primary" icon={<PlusOutlined />} onClick={onStartIteration}>
          发起新迭代
        </Button>
      ) : null}
    </div>
  )
}
