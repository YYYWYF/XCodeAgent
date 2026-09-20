import { CheckCircleOutlined, ExclamationCircleOutlined, SyncOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './AcceptanceCommitDock.less'
import { useAcceptanceAutoCommit } from './useAcceptanceAutoCommit'

const { Text } = Typography

type Props = {
  workspaceRoot: string
  /** Agent 运行中或工作区忙时不做自动保存。 */
  disabled?: boolean
  /**
   * 需要用户自己决定时渲染什么（有暂存内容、含敏感文件、读不到状态）。
   *
   * 用 render prop 而不是回调通知外层：判定结果要决定**渲染什么**，而回调通知外层
   * 意味着在子组件 render 期间 setState，会触发 React 警告与多余的渲染轮次。
   */
  renderManualReview: () => ReactElement
}

/**
 * 验收阶段的代码自动保存：质量门禁通过后把工作区快照成一个独立 commit。
 *
 * 只展示结果，不提供操作 —— 用户没有参与写提交信息，所以要把"存了什么"说清楚
 * （提交信息 + 短哈希 + 文件数），否则这个 commit 对用户是黑盒。
 *
 * 需要用户自己决定的情况不在这里替他决定，改为渲染 renderManualReview（手动提醒）。
 */
export default function AcceptanceCommitDock({
  workspaceRoot,
  disabled = false,
  renderManualReview
}: Props): ReactElement | null {
  const { commitResult, working, error, needsManualReview, remainingDirty } =
    useAcceptanceAutoCommit(workspaceRoot, !disabled)

  // 降级为手动审阅。失败原因要一并显示 —— 只给出手动入口而不说为什么，
  // 用户会以为自动保存是随机失效的。
  if (needsManualReview) {
    return (
      <>
        {error ? (
          <section className={cx('acceptance-commit-dock', 'warning')}>
            <span className={cx('acceptance-commit-icon')}>
              <ExclamationCircleOutlined />
            </span>
            <div className={cx('acceptance-commit-copy')}>
              <Text strong>代码尚未自动保存</Text>
              <Text type="secondary">{error}</Text>
            </div>
          </section>
        ) : null}
        {renderManualReview()}
      </>
    )
  }

  if (commitResult) {
    return (
      <section className={cx('acceptance-commit-dock', 'saved')}>
        <span className={cx('acceptance-commit-icon')}>
          <CheckCircleOutlined />
        </span>
        <div className={cx('acceptance-commit-copy')}>
          <Text strong>代码已自动保存</Text>
          <Text type="secondary">
            {commitResult.message} · {commitResult.commitSha.slice(0, 8)} ·{' '}
            {commitResult.committedPaths.length} 个文件
            {remainingDirty ? '，其余修改仍保留在工作区' : ''}
          </Text>
        </div>
      </section>
    )
  }

  // 正在读取或正在提交。读不到变更时整块不渲染（无事可说）。
  if (working) {
    return (
      <section className={cx('acceptance-commit-dock')}>
        <span className={cx('acceptance-commit-icon')}>
          <SyncOutlined spin />
        </span>
        <div className={cx('acceptance-commit-copy')}>
          <Text strong>正在保存当前代码</Text>
          <Text type="secondary">验收通过前会把当前工作区快照成一个独立版本。</Text>
        </div>
      </section>
    )
  }

  return null
}
