import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Button, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ApplicationLifecycle } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  /** 应用生命周期：用 initialization.stage 驱动加载/就绪/失败三态。 */
  lifecycle?: ApplicationLifecycle
  /** 模板就绪后点击进入开发阶段。 */
  onEnterDevelopment?: () => void
  /** 模板生成失败后重试。 */
  onRetry?: () => void
}

const TEMPLATE_STAGES = new Set([
  'generating_application_template_files',
  'ready_for_workbench',
  'application_template_generation_failed'
])

/** 判断 lifecycle 是否已进入模板准备阶段（用户点过"进入开发阶段"后）。 */
export function isTemplatePreparing(lifecycle?: ApplicationLifecycle): boolean {
  const stage = lifecycle?.initialization?.stage
  return Boolean(stage && TEMPLATE_STAGES.has(stage))
}

/** 项目计划确认后"产品 Agent 正在准备模板"卡片。
 *  由 applicationLifecycle.initialization.stage 驱动三态：
 *  - generating_application_template_files：加载态（拉取模板工程 + 生成应用骨架）
 *  - ready_for_workbench：就绪态，出现"进入开发阶段"按钮
 *  - application_template_generation_failed：失败态，错误信息 + 重试 */
export default function TemplatePreparingCard({
  lifecycle,
  onEnterDevelopment,
  onRetry
}: Props): ReactElement {
  const stage = lifecycle?.initialization?.stage
  const failed = stage === 'application_template_generation_failed'
  const ready = stage === 'ready_for_workbench'

  if (failed) {
    return (
      <div className={cx('template-preparing-card', 'template-preparing-error')}>
        <div className={cx('template-preparing-head')}>
          <ExclamationCircleOutlined className={cx('template-preparing-icon', 'is-error')} />
          <Text strong>应用模板生成失败</Text>
        </div>
        <Text type="secondary" className={cx('template-preparing-desc')}>
          {lifecycle?.error?.message || '应用模板文件生成失败，请重试。'}
        </Text>
        <Button icon={<ReloadOutlined />} onClick={() => onRetry?.()}>
          重试
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
          项目计划已确认，应用模板已生成。点击下方按钮进入开发阶段，开始详细设计与构建。
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
        正在拉取模板工程并生成应用骨架，请稍候…
      </Text>
    </div>
  )
}
