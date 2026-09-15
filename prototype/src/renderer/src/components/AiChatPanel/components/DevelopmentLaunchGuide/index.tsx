import { AppstoreOutlined, LayoutOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ComposerArtifactState, ComposerArtifactTarget } from '../../artifactMention'
import { cx } from '../../../../utils'
import './DevelopmentLaunchGuide.less'

const { Text } = Typography

/** 与 ChatComposer 产物面板共用同一套状态徽标文案：对齐真实工程的产物状态口径（未开始/进行中/已完成）。 */
const ARTIFACT_STATE_LABELS: Record<ComposerArtifactState, string> = {
  continue: '继续处理',
  delivered: '已完成',
  'in-progress': '进行中',
  queued: '后台任务',
  ready: '未开始'
}

/** 引导卡的固定分组：类别图标与输入区「产物」面板左列同款，让两组内容一眼可分。 */
const LAUNCH_SECTIONS: Array<{
  kind: ComposerArtifactTarget['kind']
  title: string
  icon: ReactElement
  emptyText: string
}> = [
  { kind: 'page', title: '页面', icon: <LayoutOutlined />, emptyText: '暂无页面产物' },
  {
    kind: 'business-object',
    title: '实体',
    icon: <AppstoreOutlined />,
    emptyText: '暂无实体产物'
  }
]

type LaunchSectionProps = {
  disabled?: boolean
  emptyText: string
  icon: ReactElement
  items: ComposerArtifactTarget[]
  onLaunch: (item: ComposerArtifactTarget) => void
  onResume?: (taskId: string) => void
  title: string
}

/** 渲染一个产物分组：浅色内嵌区块 + 类别图标标题 + “名称 + 状态”行，形态对齐右侧产物目录。 */
function LaunchSection({
  disabled,
  emptyText,
  icon,
  items,
  onLaunch,
  onResume,
  title
}: LaunchSectionProps): ReactElement {
  return (
    <section className={cx('development-launch-section')}>
      <div className={cx('development-launch-caption')}>
        <span className={cx('development-launch-caption-icon')} aria-hidden="true">
          {icon}
        </span>
        <Text strong>{title}</Text>
      </div>
      {items.length > 0 ? (
        <div className={cx('development-launch-list')}>
          {items.map((item) => (
            <button
              aria-disabled={item.disabled || disabled}
              aria-label={`${item.label}，${ARTIFACT_STATE_LABELS[item.state]}`}
              className={cx('development-launch-item', (item.disabled || disabled) && 'disabled')}
              key={item.artifactId}
              onClick={() => {
                if (disabled) return
                // 继续处理行点击恢复该产物已完成的工作流；其余可用行点击直接发起实施，
                // 禁用行仍交给发起校验给出原因反馈（与产物面板同一条链路）。
                if (item.continuationTaskId) {
                  onResume?.(item.continuationTaskId)
                  return
                }
                onLaunch(item)
              }}
              title={
                item.disabled
                  ? item.disabledReason
                  : item.continuationTaskId
                    ? '该产物的实现已完成，点击继续处理后续工作流'
                    : '点击直接发起实施'
              }
              type="button"
            >
              <Text className={cx('development-launch-item-name')} ellipsis>
                {item.label}
              </Text>
              <span className={cx('development-launch-state', `is-${item.state}`)}>
                {ARTIFACT_STATE_LABELS[item.state]}
              </span>
            </button>
          ))}
        </div>
      ) : (
        <Text className={cx('development-launch-empty')} type="secondary">
          {emptyText}
        </Text>
      )}
    </section>
  )
}

type DevelopmentLaunchGuideProps = {
  /** 版本只读、查看历史阶段或运行中时整卡只读，仅保留状态展示。 */
  disabled?: boolean
  /** 产物候选：与输入区「产物」按钮共用同一份 composerMentionItems。 */
  items: ComposerArtifactTarget[]
  onLaunch: (item: ComposerArtifactTarget) => void
  onResume?: (taskId: string) => void
}

/**
 * 开发阶段的产物发起引导卡：卡壳复用 workflow-run-card（与澄清卡/确认卡同一套背景与区块设计），
 * 卡内分“页面 / 实体”两个浅色区块平铺候选，每行只有名称与状态徽标；
 * 候选与发起链路复用产物面板，两个入口永远一致。
 */
export default function DevelopmentLaunchGuide({
  disabled,
  items,
  onLaunch,
  onResume
}: DevelopmentLaunchGuideProps): ReactElement {
  return (
    <div className={cx('workflow-run-card', 'development-launch-guide')}>
      <header className={cx('development-launch-heading')}>
        {/* 头部结构对齐确认卡：信号点 + 标题 + 一句说明，观感上是同一族交互卡。 */}
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <Text className={cx('development-launch-title')} strong>
            今天想从哪里开始？
          </Text>
        </div>
        <Text className={cx('workflow-ui-design-copy')}>点选产物即可直接发起实施。</Text>
      </header>
      <div className={cx('development-launch-grid')}>
        {LAUNCH_SECTIONS.map((section) => (
          <LaunchSection
            disabled={disabled}
            emptyText={section.emptyText}
            icon={section.icon}
            items={items.filter((item) => item.kind === section.kind)}
            key={section.kind}
            onLaunch={onLaunch}
            onResume={onResume}
            title={section.title}
          />
        ))}
      </div>
    </div>
  )
}
