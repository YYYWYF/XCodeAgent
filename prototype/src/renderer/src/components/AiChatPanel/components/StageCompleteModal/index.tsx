import { CheckCircleOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import PhaseGateModal from '../../../PhaseGateModal'

type StageCompletionProps = {
  onCancel: () => void
  onConfirm: () => void
  open: boolean
}

/**
 * 在全部开发产物完成后，询问用户是否进入测试阶段；暂不进入时可从顶部阶段条再次唤起。
 * 文案保持静态通用：不绑定页面、接口、用例等会随演示变化的具体数量。
 */
export function DevelopmentStageCompleteModal({
  onCancel,
  onConfirm,
  open
}: StageCompletionProps): ReactElement {
  return (
    <PhaseGateModal
      cancelText="暂不进入"
      confirmText="进入测试阶段"
      icon={<CheckCircleOutlined />}
      lead="测试用例由后台任务自动生成，未完成的用例会先在用例目录中置灰展示，不影响现在进入测试阶段。是否进入测试阶段？"
      onCancel={onCancel}
      onConfirm={onConfirm}
      open={open}
      title="开发阶段的产物已全部完成"
    />
  )
}

/** 全部业务用例通过后，询问用户是否进入审查阶段；暂不进入时保留测试阶段。 */
export function TestingStageCompleteModal({
  onCancel,
  onConfirm,
  open
}: StageCompletionProps): ReactElement {
  return (
    <PhaseGateModal
      cancelText="暂不进入"
      confirmText="进入审查阶段"
      icon={<CheckCircleOutlined />}
      lead="全部业务测试用例已执行通过。是否进入审查阶段？"
      onCancel={onCancel}
      onConfirm={onConfirm}
      open={open}
      title="测试阶段已完成"
    />
  )
}
