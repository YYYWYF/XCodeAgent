import {
  ArrowRightOutlined,
  CheckCircleFilled,
  ExclamationCircleFilled,
  LoadingOutlined
} from '@ant-design/icons'
import { Button } from 'antd'
import type { ApplicationConfig, WorkflowRunPayload } from '../../typings'
import type { ActivePlanningStatus } from '../../service/activeApplicationPlanning'
import { cx } from '../../utils'
import { planningWorkflowPhase } from './planningWorkflowState'

type Props = {
  application: ApplicationConfig
  onOpen: () => void
  status: ActivePlanningStatus
  workflow?: WorkflowRunPayload
}

type PlanningPresentationStage =
  | 'detail-review'
  | 'error'
  | 'project-planning'
  | 'project-plan-revision'
  | 'project-plan-confirmation'
  | 'requirement-clarification'
  | 'requirement-confirmation'
  | 'requirements'
  | 'unknown-confirmation'

// 从公开 Workflow 快照读取当前确认类型，兼容 summary、state 和 result 三种投影位置。
function workflowClarificationMode(workflow?: WorkflowRunPayload): string {
  for (const source of [
    workflow?.summary.clarification,
    workflow?.state?.clarification,
    workflow?.result?.clarification
  ]) {
    if (source && typeof source === 'object' && 'mode' in source) {
      return String(source.mode || '')
    }
  }
  return ''
}

// 根据运行状态、工作流节点和确认门禁推导首页需要展示的精确规划阶段。
function planningPresentationStage(
  status: ActivePlanningStatus,
  workflow?: WorkflowRunPayload
): PlanningPresentationStage {
  if (status === 'error') return 'error'
  const phase = planningWorkflowPhase(workflow)
  const clarificationMode = workflowClarificationMode(workflow)
  if (status === 'running') {
    return phase === 'project_planning' ? 'project-planning' : 'requirements'
  }
  if (clarificationMode === 'detail_review') return 'detail-review'
  if (clarificationMode === 'direct') return 'requirement-clarification'
  if (clarificationMode === 'requirement_spec_confirmation') return 'requirement-confirmation'
  if (clarificationMode === 'project_plan_confirmation') return 'project-plan-confirmation'
  if (
    clarificationMode === 'project_plan_dependency_validation_error' ||
    clarificationMode === 'project_plan_revision_required'
  ) {
    return 'project-plan-revision'
  }
  if (workflow?.confirmationArtifact?.id === 'requirement_spec') {
    return 'requirement-confirmation'
  }
  if (workflow?.confirmationArtifact?.id === 'project_plan') {
    return 'project-plan-confirmation'
  }
  return 'unknown-confirmation'
}

// 根据规划状态返回首页入口所需的图标与文案。
function planningStatusPresentation(
  application: ApplicationConfig,
  status: ActivePlanningStatus,
  workflow?: WorkflowRunPayload
): { description: string; icon: JSX.Element; title: string } {
  const stage = planningPresentationStage(status, workflow)
  if (stage === 'requirement-clarification') {
    return {
      description: '需要补充关键信息，完成后继续生成需求文档',
      icon: <CheckCircleFilled />,
      title: `阶段 1/2：补充「${application.appName}」的需求细节`
    }
  }
  if (stage === 'requirement-confirmation') {
    return {
      description: '需求文档已生成，确认后继续生成项目计划',
      icon: <CheckCircleFilled />,
      title: `阶段 1/2：确认「${application.appName}」的需求文档`
    }
  }
  if (stage === 'project-plan-confirmation') {
    return {
      description: '项目计划已生成，确认后进入工作台',
      icon: <CheckCircleFilled />,
      title: `阶段 2/2：确认「${application.appName}」的项目规划`
    }
  }
  if (stage === 'project-plan-revision') {
    return {
      description: '项目规划仍有待补充或修订的内容，处理后才能继续',
      icon: <ExclamationCircleFilled />,
      title: `阶段 2/2：完善「${application.appName}」的项目规划`
    }
  }
  if (stage === 'detail-review') {
    return {
      description: '项目计划已确认，请审核并补充页面与数据源设计',
      icon: <CheckCircleFilled />,
      title: `阶段 2/2：补充「${application.appName}」的页面与数据源细节`
    }
  }
  if (stage === 'error') {
    return {
      description: '规划流程需要处理，点击进入查看详情或重试',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用规划需要处理`
    }
  }
  if (stage === 'project-planning') {
    return {
      description: '需求文档已确认，正在生成项目计划',
      icon: <LoadingOutlined spin />,
      title: `阶段 2/2：正在生成「${application.appName}」的项目规划`
    }
  }
  if (stage === 'unknown-confirmation') {
    return {
      description: '当前规划阶段等待查看和确认',
      icon: <CheckCircleFilled />,
      title: `「${application.appName}」的应用规划等待确认`
    }
  }
  return {
    description: '正在分析需求并生成需求文档',
    icon: <LoadingOutlined spin />,
    title: `阶段 1/2：正在生成「${application.appName}」的需求文档`
  }
}

// 在首页展示尚未结束的应用规划，并提供返回全屏规划页的入口。
export default function ActivePlanningAction({
  application,
  onOpen,
  status,
  workflow
}: Props): JSX.Element {
  const presentation = planningStatusPresentation(application, status, workflow)
  return (
    <Button
      className={cx('active-planning-action', `status-${status}`)}
      onClick={onOpen}
      type="text"
    >
      <span className={cx('active-planning-action-icon')} aria-hidden="true">
        {presentation.icon}
      </span>
      <span className={cx('active-planning-action-copy')}>
        <strong>{presentation.title}</strong>
        <small>{presentation.description}</small>
      </span>
      <span className={cx('active-planning-action-open')}>
        查看计划
        <ArrowRightOutlined />
      </span>
    </Button>
  )
}
