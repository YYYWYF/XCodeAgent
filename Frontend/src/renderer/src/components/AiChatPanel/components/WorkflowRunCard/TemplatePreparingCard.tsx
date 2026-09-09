import {
  CheckCircleOutlined,
  ExclamationCircleOutlined
} from '@ant-design/icons'
import { Button, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ApplicationLifecycle } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  /** 应用生命周期：用 initialization.stage 驱动加载/就绪/失败/中断态。 */
  lifecycle?: ApplicationLifecycle
  /** 模板就绪后点击进入开发阶段。 */
  onEnterDevelopment?: () => void
  /** 模板失败或任务中断后只重试模板下载与初始化阶段。 */
  onRetry?: () => void
  /** 当前是否正在执行模板重试。 */
  retrying?: boolean
  /** lifecycle 仍在模板阶段但当前 renderer 没有对应生成任务。 */
  orphaned?: boolean
}

const TEMPLATE_STAGES = new Set([
  'generating_application_template_files',
  'ready_for_workbench',
  'application_template_generation_failed'
])

/** 判断 lifecycle 是否已进入模板准备阶段（TechnicalPlan 确认后）。 */
export function isTemplatePreparing(lifecycle?: ApplicationLifecycle): boolean {
  const stage = lifecycle?.initialization?.stage
  return Boolean(stage && TEMPLATE_STAGES.has(stage))
}

/** TechnicalPlan 确认后"产品 Agent 正在准备模板"卡片。
 *  由 applicationLifecycle.initialization.stage 与本地任务状态驱动四态：
 *  - generating_application_template_files：加载态（拉取模板工程 + 生成应用骨架）
 *  - generating_application_template_files 且无本地任务：中断态，可继续生成
 *  - ready_for_workbench：就绪态，出现"进入开发阶段"按钮
 *  - application_template_generation_failed：失败态，展示错误信息 */
export default function TemplatePreparingCard({
  lifecycle,
  onEnterDevelopment,
  onRetry,
  retrying = false,
  orphaned = false
}: Props): ReactElement {
  const stage = lifecycle?.initialization?.stage
  const failed = stage === 'application_template_generation_failed' && !retrying
  const interrupted = orphaned && !retrying
  const ready = stage === 'ready_for_workbench'

  if (failed) {
    return (
      <div className={cx('template-preparing-card', 'template-preparing-error')}>
        <div className={cx('template-preparing-head')}>
          <ExclamationCircleOutlined className={cx('template-preparing-icon', 'is-error')} />
          <Text strong>应用模板生成失败</Text>
        </div>
        <Text type="secondary" className={cx('template-preparing-desc')}>
          {lifecycle?.error?.message || '应用模板文件生成失败，请查看错误信息。'}
        </Text>
        <Button
          className={cx('template-preparing-retry-btn')}
          disabled={!onRetry}
          loading={retrying}
          onClick={onRetry}
          type="primary"
        >
          {retrying ? '正在重新生成模板' : '重新生成模板'}
        </Button>
      </div>
    )
  }

  if (interrupted) {
    return (
      <div className={cx('template-preparing-card', 'template-preparing-error')}>
        <div className={cx('template-preparing-head')}>
          <ExclamationCircleOutlined className={cx('template-preparing-icon', 'is-error')} />
          <Text strong>应用模板生成已中断</Text>
        </div>
        <Text type="secondary" className={cx('template-preparing-desc')}>
          上一次模板生成任务已经停止，已确认的需求和 TechnicalPlan 不受影响，可以继续从模板阶段重新执行。
        </Text>
        <Button
          className={cx('template-preparing-retry-btn')}
          disabled={!onRetry}
          loading={retrying}
          onClick={onRetry}
          type="primary"
        >
          继续生成模板
        </Button>
      </div>
    )
  }

  if (ready) {
    return (
      <div className={cx('template-preparing-card', 'template-preparing-ready')}>
        <div className={cx('template-preparing-head')}>
          <CheckCircleOutlined className={cx('template-preparing-icon', 'is-ready')} />
          <Text strong>应用模板已就绪</Text>
        </div>
        <Text type="secondary" className={cx('template-preparing-desc')}>
          TechnicalPlan 已确认，应用模板已生成。点击下方按钮进入开发阶段，开始详细设计与构建。
        </Text>
        <Button
          className={cx('template-preparing-enter-btn')}
          onClick={() => onEnterDevelopment?.()}
          size="large"
          type="primary"
        >
          进入开发阶段
        </Button>
      </div>
    )
  }

  // generating_application_template_files 或 lifecycle 未加载但已进入准备态
  return (
    <div className={cx('template-preparing-card', 'template-preparing-loading')}>
      <div className={cx('template-preparing-head')}>
        <Spin size="small" />
        <Text strong>产品 Agent 正在准备应用模板</Text>
      </div>
      <Text type="secondary" className={cx('template-preparing-desc')}>
        {retrying
          ? '正在重新拉取模板工程并初始化应用骨架…'
          : '正在拉取模板工程并生成应用骨架，请稍候…'}
      </Text>
    </div>
  )
}
