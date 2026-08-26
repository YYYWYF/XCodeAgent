import { CheckCircleOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  disabled?: boolean
  skippedUiDesign?: boolean
  onEnterPlanning: () => void
}

/** 在设计阶段结束后展示显式入口，并复用模板就绪卡片的绿色视觉样式。 */
export default function PlanningStageEntryCard({
  disabled,
  skippedUiDesign,
  onEnterPlanning
}: Props): ReactElement {
  return (
    <section className={cx('template-preparing-card', 'template-preparing-ready')}>
      <div className={cx('template-preparing-head')}>
        <CheckCircleOutlined className={cx('template-preparing-icon', 'is-ready')} />
        <Text strong>{skippedUiDesign ? 'UI 设计已跳过' : '设计阶段已完成'}</Text>
      </div>
      <Text className={cx('template-preparing-desc')} type="secondary">
        进入规划阶段后，规划 Agent 将根据已确认的需求、产品规划和
        {skippedUiDesign ? '跳过状态' : ' UI 设计稿'}
        自动生成技术规划。生成后仍需你确认，才会进入开发准备。
      </Text>
      <Button
        className={cx('template-preparing-enter-btn')}
        disabled={disabled}
        onClick={onEnterPlanning}
        size="large"
        type="primary"
      >
        进入规划阶段
      </Button>
    </section>
  )
}
