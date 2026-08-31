import { HourglassOutlined, InboxOutlined, MoonOutlined } from '@ant-design/icons'
import { Fragment, useEffect, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import PhaseGateModal from '../../../PhaseGateModal'
import {
  TEST_CASE_GENERATION_TASK_TYPE_META,
  type TestCaseGenerationTaskType
} from '../../../../testCasePreparation'
import './index.less'

export type { TestCaseGenerationTaskType } from '../../../../testCasePreparation'

type TestCaseTaskTypeModalProps = {
  open: boolean
  /** 预计生成的业务测试用例数量；计划阶段已确认，数量在此处是确定值。 */
  testCaseTotal: number
  onCancel: () => void
  onConfirm: (taskType: TestCaseGenerationTaskType) => void
}

type TaskTypeOption = {
  description: string
  highlightCredit?: boolean
  icon: ReactElement
  id: TestCaseGenerationTaskType
  name: string
}

/** 两种生成任务的固定说明与图标，名称同时用于弹框选择与后续任务标签；图标取意：异步=沙漏放后台执行，潮汐=闲时月亮。 */
const TASK_TYPE_OPTIONS: TaskTypeOption[] = [
  {
    id: 'async',
    name: TEST_CASE_GENERATION_TASK_TYPE_META.async.label,
    icon: <HourglassOutlined />,
    description: TEST_CASE_GENERATION_TASK_TYPE_META.async.description,
    highlightCredit: true
  },
  {
    id: 'tide',
    name: TEST_CASE_GENERATION_TASK_TYPE_META.tide.label,
    icon: <MoonOutlined />,
    description: TEST_CASE_GENERATION_TASK_TYPE_META.tide.description
  }
]

/** 按需将任务说明中的「码豆」渲染为红色强调；仅消耗码豆的选项需要提醒，不消耗的不标。 */
function renderDescription(description: string, highlightCredit: boolean): ReactNode {
  return description.split('码豆').map((segment, index) => (
    <Fragment key={index}>
      {index > 0 && highlightCredit ? (
        <span className={cx('test-case-task-type-credit')}>码豆</span>
      ) : null}
      {segment}
    </Fragment>
  ))
}

/** 项目计划确认后的开发准入门弹框：选择测试用例生成任务类型后才进入开发阶段；用例数量由计划阶段确认，属确定信息。 */
export default function TestCaseTaskTypeModal({
  open,
  testCaseTotal,
  onCancel,
  onConfirm
}: TestCaseTaskTypeModalProps): ReactElement {
  const [selectedTaskType, setSelectedTaskType] = useState<TestCaseGenerationTaskType>()

  // 每次重新打开都要求用户对当前项目计划显式选择，避免沿用上一次未提交的选择。
  useEffect(() => {
    if (open) setSelectedTaskType(undefined)
  }, [open])

  return (
    <PhaseGateModal
      cancelText="暂不进入"
      confirmDisabled={!selectedTaskType}
      confirmText="确认并进入开发阶段"
      icon={<InboxOutlined />}
      lead="项目计划已确认，当前版本已具备进入开发阶段的条件。"
      onCancel={onCancel}
      onConfirm={() => {
        if (selectedTaskType) onConfirm(selectedTaskType)
      }}
      open={open}
      subtitle="同时创建测试用例后台任务"
      title="进入开发阶段？"
    >
      <p className={cx('test-case-task-type-hint')}>
        预计生成 <strong>{testCaseTotal}</strong>{' '}
        条业务测试用例，开发期间在后台生成，不影响当前开发进度。
      </p>
      <div
        className={cx('test-case-task-type-options')}
        role="radiogroup"
        aria-label="测试用例生成任务类型"
      >
        {TASK_TYPE_OPTIONS.map((option) => {
          const selected = selectedTaskType === option.id
          return (
            <button
              aria-checked={selected}
              className={cx('test-case-task-type-option', selected && 'selected')}
              key={option.id}
              onClick={() => setSelectedTaskType(option.id)}
              role="radio"
              type="button"
            >
              <span className={cx('test-case-task-type-option-icon')} aria-hidden="true">
                {option.icon}
              </span>
              <span className={cx('test-case-task-type-option-content')}>
                <strong>{option.name}</strong>
                <span>
                  {renderDescription(option.description, Boolean(option.highlightCredit))}
                </span>
              </span>
              <span className={cx('test-case-task-type-option-radio')} aria-hidden="true" />
            </button>
          )
        })}
      </div>
    </PhaseGateModal>
  )
}
