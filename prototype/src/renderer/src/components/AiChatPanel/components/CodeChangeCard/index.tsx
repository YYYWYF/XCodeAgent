import { CheckCircleOutlined, CodeOutlined, UndoOutlined } from '@ant-design/icons'
import { Button, Space, Tag, Typography } from 'antd'
import { useMemo, type ReactElement } from 'react'
import type { WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import {
  groupWorkspaceCodeChanges,
  splitWorkspacePath,
  summarizeWorkspaceCodeChanges,
  workspaceCodeChangeDisplayPath
} from '../../utils'
import './CodeChangeCard.less'

const { Text } = Typography

type Props = {
  codeChanges: WorkspaceCodeChangeSet
  compact?: boolean
  loading: boolean
  onApproveAll: () => void
  onOpenFile: (path: string) => void
  onRevert: () => void
  revertDisabled: boolean
  reverting: boolean
}

/** 展示一次工作流产生的可审阅文件列表与汇总数据。 */
export default function CodeChangeCard({
  codeChanges,
  compact = false,
  loading,
  onApproveAll,
  onOpenFile,
  onRevert,
  revertDisabled,
  reverting
}: Props): ReactElement {
  const groupedChanges = useMemo(
    () => groupWorkspaceCodeChanges(codeChanges.files),
    [codeChanges.files]
  )
  const summary = useMemo(() => summarizeWorkspaceCodeChanges(groupedChanges), [groupedChanges])
  const pending =
    codeChanges.status === 'pending_approval' && Boolean(codeChanges.approvals?.length)
  const reverted = codeChanges.status === 'reverted'
  const resolved = codeChanges.status === 'applied' || codeChanges.status === 'rejected' || reverted

  if (compact) {
    const firstFile = groupedChanges[0]
    const displayPath = firstFile
      ? workspaceCodeChangeDisplayPath(
          firstFile.path,
          codeChanges.workspaceRoot,
          codeChanges.workspaceName
        )
      : '当前文件'

    return (
      <div className={cx('code-change-card', 'compact', pending && 'pending', resolved && 'resolved')}>
        <Text className={cx('code-change-compact-label')} strong>
          文件改动
        </Text>
        <Text className={cx('code-change-compact-path')} title={displayPath}>
          {displayPath}
        </Text>
        <span className={cx('code-change-file-stats')}>
          <span className={cx('addition')}>+{summary.additions}</span>
          <span className={cx('deletion')}>-{summary.deletions}</span>
        </span>
        <span className={cx('code-change-compact-actions')}>
          <Button
            className={cx('code-change-compact-revert')}
            disabled={revertDisabled || reverted}
            loading={reverting}
            onClick={onRevert}
            size="small"
            type="text"
          >
            {reverted ? '已撤销' : '撤销'}
          </Button>
          <Button
            className={cx('code-change-compact-action')}
            disabled={loading || reverted}
            loading={loading}
            onClick={onApproveAll}
            size="small"
            type="primary"
          >
            接受
          </Button>
        </span>
      </div>
    )
  }

  return (
    <div className={cx('code-change-card', pending && 'pending', resolved && 'resolved')}>
      <div className={cx('code-change-header')}>
        <Space size={10}>
          <span className={cx('code-change-icon')}>
            <CodeOutlined />
          </span>
          <div className={cx('code-change-title')}>
            <Text strong>{pending ? '待审核变更' : '文件改动'}</Text>
            <Text className={cx('code-change-count')}>{summary.files} 个文件已变更</Text>
            <span className={cx('code-change-total')}>
              <span className={cx('addition')}>+{summary.additions}</span>
              <span className={cx('deletion')}>-{summary.deletions}</span>
            </span>
          </div>
        </Space>
        {!pending && groupedChanges.length > 0 && (
          <div className={cx('code-change-header-actions')}>
            <Button
              className={cx(reverted && 'code-change-reverted-button')}
              danger
              disabled={reverted || revertDisabled}
              icon={<UndoOutlined />}
              loading={reverting}
              onClick={onRevert}
              size="small"
              type="text"
            >
              {reverted ? '已撤销' : '撤销'}
            </Button>
            {/* 右侧面板已是审阅视角，这里直接以“接受”完成确认（见 MessageList 接线）。 */}
            <Button onClick={onApproveAll} size="small" type="primary">
              接受
            </Button>
          </div>
        )}
        {pending && <Tag color="gold">{formatStatus(codeChanges.status)}</Tag>}
      </div>

      <div className={cx('code-change-file-list')}>
        {groupedChanges.map((file) => {
          const displayPath = workspaceCodeChangeDisplayPath(
            file.path,
            codeChanges.workspaceRoot,
            codeChanges.workspaceName
          )
          const pathParts = splitWorkspacePath(displayPath)
          return (
            <button
              className={cx('code-change-file-row')}
              key={file.path}
              onClick={() => onOpenFile(file.path)}
              type="button"
            >
              <span className={cx('code-change-file-path')} title={displayPath}>
                {pathParts.directory && (
                  <span className={cx('code-change-file-directory')}>{pathParts.directory}/</span>
                )}
                <span className={cx('code-change-file-name')}>{pathParts.fileName}</span>
              </span>
              <span className={cx('code-change-file-stats')}>
                <span className={cx('addition')}>+{file.additions}</span>
                <span className={cx('deletion')}>-{file.deletions}</span>
              </span>
            </button>
          )
        })}
      </div>

      {pending && (
        <div className={cx('code-change-actions')}>
          <Button disabled={loading} loading={loading} onClick={onApproveAll} type="primary">
            审核通过
          </Button>
        </div>
      )}

      {resolved && (
        <span className={cx('code-change-resolved-mark')}>
          {reverted ? <UndoOutlined /> : <CheckCircleOutlined />}
        </span>
      )}
    </div>
  )
}

/** 将变更集状态转换为历史卡片展示文案。 */
function formatStatus(status: WorkspaceCodeChangeSet['status']): string {
  if (status === 'pending_approval') return '待审核'
  if (status === 'rejected') return '已退回'
  if (status === 'reverted') return '已撤销'
  return '已应用'
}
