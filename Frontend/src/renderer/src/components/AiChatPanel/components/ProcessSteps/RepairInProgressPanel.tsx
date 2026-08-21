import { CheckCircleFilled, LoadingOutlined, ToolOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './RepairInProgressPanel.less'

const { Text } = Typography

type Props = {
  detail: string
  completed?: boolean
}

/** 展示局部修复的执行或完成反馈，并用紫色主题区分当前状态。 */
export default function RepairInProgressPanel({ detail, completed = false }: Props): ReactElement {
  return (
    <section
      aria-busy={!completed}
      aria-label={completed ? '局部修复已完成' : '正在准备局部修复'}
      aria-live="polite"
      className={cx('repair-in-progress', completed && 'repair-completed')}
    >
      <span className={cx('repair-in-progress-mark')}>
        {completed ? <CheckCircleFilled /> : <ToolOutlined />}
        {!completed && <LoadingOutlined spin />}
      </span>
      <span className={cx('repair-in-progress-content')}>
        <Text className={cx('repair-in-progress-eyebrow')}>
          {completed ? 'AUTO REPAIR · COMPLETE' : 'AUTO REPAIR'}
        </Text>
        <Text className={cx('repair-in-progress-title')} strong>
          {completed ? '局部修复任务已完成' : '正在定位失败原因并生成局部修复方案'}
        </Text>
        <Text className={cx('repair-in-progress-detail')} type="secondary">
          {detail ||
            (completed
              ? '修复结果已应用，正在重新执行集成测试。'
              : '修复任务准备完成后将自动执行，无需手动刷新。')}
        </Text>
      </span>
      {completed ? (
        <span className={cx('repair-in-progress-status')}>
          <CheckCircleFilled />
          已完成
        </span>
      ) : (
        <span aria-hidden="true" className={cx('repair-in-progress-dots')}>
          <i />
          <i />
          <i />
        </span>
      )}
    </section>
  )
}
