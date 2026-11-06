import { CheckCircleOutlined, CloseCircleOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { Button, Collapse, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './WorkflowInteractionFeedback.less'

const { Text } = Typography

/** 用具体业务阶段命名卡片，避免所有操作都被称为工作流执行。 */
export function workflowInteractionTitle(mode?: string, phase?: string): string {
  const titles: Record<string, string> = {
    requirement_document_confirmation: '需求文档',
    technical_plan_confirmation: '技术规划',
    technical_plan_generation_error: '技术规划生成未完成',
    project_plan_confirmation: '项目计划',
    ui_design_confirmation: 'UI 设计稿',
    planning_stage_entry_confirmation: '进入计划阶段',
    api_design_required: '接口配置',
    entity_source_binding_required: '实体配置',
    entity_source_binding: '实体设计',
    build_task_plan_confirmation: '开发任务计划',
    unit_test_confirmation: '单元测试',
    frontend_performance_confirmation: '性能测试',
    test_phase_confirmation: '进入测试阶段',
    review_phase_confirmation: '进入审查阶段',
    acceptance_phase_confirmation: '进入验收阶段',
    revision_impact_confirmation: '修改范围',
    revision_draft_interaction: '修改方案',
    implementation_fix_confirmation: '修改需求',
    technical_planning: '技术规划',
    product_planning: '需求规划',
    project_planning: '项目计划'
  }
  return titles[mode || ''] || titles[phase || ''] || '任务进展'
}

/** 已消费且没有预览内容的交互只保留简短回执，不再显示等待提示和失效按钮。 */
export function WorkflowInteractionReceipt({ mode, phase, result }: {
  mode?: string
  phase?: string
  result: 'confirmed' | 'cancelled' | 'submitted' | 'stale'
}): ReactElement {
  const cancelled = result === 'cancelled'
  const title = mode === 'technical_plan_generation_error' && result === 'submitted'
    ? '重新生成请求已提交'
    : result === 'stale' ? '此前的技术规划记录'
    : `${workflowInteractionTitle(mode, phase)}${cancelled ? '已取消' : result === 'confirmed' ? '已确认' : '已提交'}`
  return (
    <div className={cx('workflow-interaction-receipt')} role="note">
      {cancelled ? <CloseCircleOutlined /> : result === 'stale' ? <InfoCircleOutlined /> : <CheckCircleOutlined />}
      <Text>{title}</Text>
      {result === 'submitted' ? <Text type="secondary">后续进展请看最新任务消息。</Text> : null}
      {result === 'stale' ? <Text type="secondary">请在最新的技术规划消息中查看当前内容和操作。</Text> : null}
    </div>
  )
}

/** 生成失败需要用户选择重试，不属于正式产物确认，诊断按需展开。 */
export function TechnicalPlanGenerationIssue({ disabled, errors, onRetry }: {
  disabled: boolean
  errors?: string[]
  onRetry: () => void
}): ReactElement {
  const details = errors?.map(String).filter(Boolean).slice(0, 5) || []
  return (
    <div className={cx('workflow-generation-issue')}>
      <Text>这次技术规划未通过检查，还没有可供确认的内容。</Text>
      <Text type="secondary">可以重新生成，通过检查后再审阅技术规划。</Text>
      {details.length ? (
        <Collapse ghost>
          <Collapse.Panel header="查看问题详情" key="details">
            <ul>{details.map((detail, index) => <li key={index}>{detail}</li>)}</ul>
          </Collapse.Panel>
        </Collapse>
      ) : null}
      <Button disabled={disabled} onClick={onRetry} type="primary">重新生成技术规划</Button>
    </div>
  )
}
