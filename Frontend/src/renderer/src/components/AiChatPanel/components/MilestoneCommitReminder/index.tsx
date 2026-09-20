import { GitlabOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Spin, Typography } from 'antd'
import { type ReactElement } from 'react'
import { cx } from '../../../../utils'
import MilestoneCommitModal from './MilestoneCommitModal'
import { useMilestoneCommit } from './useMilestoneCommit'
import { shouldRenderCommitReminder } from './visibility'
import '../VersionCommitReminder/VersionCommitReminder.less'

const { Text } = Typography

type Props = {
  /** 工作区根目录。 */
  workspaceRoot: string
  /** 提醒标题，如"应用模板已就绪，建议创建初始化提交"。 */
  title: string
  /** 默认提交信息。 */
  defaultCommitMessage: string
  /** 里程碑标识，用于指纹去重的 key。 */
  milestoneId: string
  /** 是否禁用操作（Agent 运行中等）。 */
  disabled: boolean
  /**
   * 覆盖副标题。默认是"当前 N 个文件可提交。"。
   *
   * 「检查遗漏变更」用它列出具体是哪些文件没归属到模块 —— 光说数量，用户仍要自己
   * 去弹窗里逐个找，等于没答"该确认哪些"。
   */
  description?: string
  /**
   * 是否把 `.xcodeagent` 平台产物也算作可提交（默认否，见 `useMilestoneCommit`）。
   *
   * 只有设计阶段的「设计文档已确认，可保存为设计版本」传 true。
   */
  includePlatformArtifacts?: boolean
  /**
   * 读不到 Git 状态时是否直接不渲染（弱提醒用）。
   *
   * 强提醒（模板初始化、验收通过）在"本该有仓库"的时点出现，读取失败值得报出来；
   * 弱提醒（设计文档已确认）可能发生在**仓库还没建立**的设计阶段 —— 那时"提交"物理上
   * 不成立，报错只会制造噪声。弱提醒一律静默，符合"不打断"的定位。
   */
  hideWhenUnavailable?: boolean
}

/** 里程碑代码提交提醒：在验收通过等节点展示，引导用户提交代码。
 *  模板初始化场景的提交已合并进 TemplatePreparingCard，本组件仅用于验收通过场景。 */
export default function MilestoneCommitReminder({
  workspaceRoot,
  title,
  defaultCommitMessage,
  milestoneId,
  disabled,
  description,
  includePlatformArtifacts = false,
  hideWhenUnavailable = false
}: Props): ReactElement | null {
  const commit = useMilestoneCommit(
    workspaceRoot,
    milestoneId,
    defaultCommitMessage,
    includePlatformArtifacts
  )
  const {
    snapshot,
    commitResult,
    eligiblePaths,
    inspectError,
    inspecting,
    dismissed,
    handleDefer,
    handleOpenCommit,
    loadSnapshot
  } = commit

  if (
    !shouldRenderCommitReminder({
      hasResult: Boolean(commitResult),
      dismissed,
      hideWhenUnavailable,
      inspectError,
      inspecting,
      hasSnapshot: Boolean(snapshot),
      eligibleCount: eligiblePaths.length
    })
  ) {
    return null
  }

  return (
    <>
      <section className={cx('version-commit-reminder', inspectError && 'warning')}>
        <span className={cx('version-commit-icon')}>
          {inspecting ? (
            <Spin size="small" />
          ) : inspectError ? (
            <ReloadOutlined />
          ) : (
            <GitlabOutlined />
          )}
        </span>
        <div className={cx('version-commit-copy')}>
          <Text strong>
            {inspecting ? '正在核对当前 Git 状态' : inspectError ? '暂时无法准备提交' : title}
          </Text>
          <Text type="secondary">
            {inspectError || description || `当前 ${eligiblePaths.length} 个文件可提交。`}
          </Text>
        </div>
        <div className={cx('version-commit-actions')}>
          {inspectError ? (
            <Button
              disabled={disabled || inspecting}
              icon={<ReloadOutlined />}
              onClick={() => void loadSnapshot()}
              size="small"
            >
              重试
            </Button>
          ) : (
            <>
              <Button disabled={disabled || inspecting} onClick={handleDefer} size="small">
                稍后
              </Button>
              <Button
                disabled={disabled || inspecting || !eligiblePaths.length}
                onClick={() => void handleOpenCommit()}
                size="small"
                type="primary"
              >
                审阅并提交
              </Button>
            </>
          )}
        </div>
      </section>

      <MilestoneCommitModal title={title} disabled={disabled} commit={commit} />
    </>
  )
}
