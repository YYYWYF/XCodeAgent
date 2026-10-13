import { CodeOutlined, FileTextOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { ProcessStepRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './RepairToolActivityRow.less'

const { Text } = Typography

type Props = {
  step: ProcessStepRecord
  icon: ReactNode
  title: string
  isLast: boolean
}

const STATUS_LABELS: Record<ProcessStepRecord['status'], string> = {
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  requires_user_input: '等待确认'
}

/** 直接展示修复工具的状态与安全文件活动，不提供子步骤折叠交互。 */
export default function RepairToolActivityRow({ step, icon, title, isLast }: Props): ReactElement {
  const detail = step.detail.trim()
  return (
    <div
      className={cx(
        'process-step',
        step.kind,
        step.status,
        'static',
        'repair-tool-row',
        isLast && 'last'
      )}
    >
      <div className={cx('process-step-summary', 'repair-tool-row-heading')}>
        <span className={cx('process-step-icon')}>{icon}</span>
        <Text>{title}</Text>
        <span className={cx('repair-tool-row-status')}>{STATUS_LABELS[step.status]}</span>
      </div>
      {detail && (
        <div className={cx('repair-tool-row-file')} title={detail}>
          <span aria-hidden="true">
            {step.kind === 'command' ? <CodeOutlined /> : <FileTextOutlined />}
          </span>
          <Text>{detail}</Text>
        </div>
      )}
    </div>
  )
}
