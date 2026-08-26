import {
  ArrowRightOutlined,
  CheckCircleFilled,
  DeleteOutlined,
  ExclamationCircleFilled,
  LoadingOutlined
} from '@ant-design/icons'
import { Button } from 'antd'
import type { ApplicationConfig, ApplicationLifecycle } from '../../typings'
import type { ActivePlanningStatus } from '../../service/activeApplicationPlanning'
import { cx } from '../../utils'

type Props = {
  application: ApplicationConfig
  lifecycle: ApplicationLifecycle
  deleting: boolean
  onDelete: () => void
  onOpen: () => void
  status: ActivePlanningStatus
}

// 根据权威 lifecycle 阶段返回首页入口所需的图标与文案。
function planningStatusPresentation(
  application: ApplicationConfig,
  lifecycle: ApplicationLifecycle,
  status: ActivePlanningStatus
): { description: string; icon: JSX.Element; title: string } {
  const stage = lifecycle.initialization.stage
  if (status === 'error') {
    return {
      description: lifecycle.error?.message || '规划流程需要处理，点击进入查看详情',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用规划需要处理`
    }
  }
  // 技术执行态优先于上一次持久化的待交互阶段，避免生成期间提前提示产物已完成。
  if (status === 'running' && stage === 'awaiting_requirement_clarification') {
    return {
      description: '需求分析等待补充信息，收到后重新分析',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/4：等待补充「${application.appName}」的需求细节`
    }
  }
  if (status === 'running' && stage === 'awaiting_requirement_document_confirmation') {
    return {
      description: '需求事实与页面、操作规划已完成，正在等待联合确认',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/4：等待确认「${application.appName}」的需求文档`
    }
  }
  if (status === 'running' && stage === 'awaiting_ui_design_confirmation') {
    return {
      description: '正在处理 UI 设计稿并准备技术规划',
      icon: <LoadingOutlined spin />,
      title: `阶段 3/4：正在处理「${application.appName}」的 UI 设计稿`
    }
  }
  if (status === 'running' && stage === 'awaiting_technical_plan_confirmation') {
    return {
      description: '正在处理技术规划并准备应用模板文件',
      icon: <LoadingOutlined spin />,
      title: `阶段 4/4：正在处理「${application.appName}」的技术规划`
    }
  }
  if (stage === 'awaiting_requirement_clarification') {
    return {
      description: '需要补充关键信息，完成后继续生成需求文档',
      icon: <CheckCircleFilled />,
      title: `阶段 1/4：补充「${application.appName}」的需求细节`
    }
  }
  if (stage === 'awaiting_requirement_document_confirmation') {
    return {
      description: '需求事实与页面、操作规划已生成，确认后进入 UI 设计',
      icon: <CheckCircleFilled />,
      title: `阶段 1/4：确认「${application.appName}」的需求文档`
    }
  }
  if (stage === 'awaiting_ui_design_confirmation') {
    return {
      description: 'React UI 设计稿已生成，请由产品确认后继续',
      icon: <CheckCircleFilled />,
      title: `阶段 3/4：确认「${application.appName}」的 UI 设计稿`
    }
  }
  if (stage === 'awaiting_planning_stage_entry') {
    return {
      description: '设计阶段已完成，请确认是否进入规划阶段并生成技术规划',
      icon: <CheckCircleFilled />,
      title: `「${application.appName}」正在等待进入规划阶段`
    }
  }
  if (stage === 'awaiting_technical_plan_confirmation') {
    return {
      description: '技术规划已生成，请由开发确认后进入工作台',
      icon: <CheckCircleFilled />,
      title: `阶段 4/4：确认「${application.appName}」的技术规划`
    }
  }
  if (stage === 'application_template_generation_failed') {
    return {
      description: lifecycle.error?.message || '应用模板文件生成失败，点击查看详情',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用模板文件生成失败`
    }
  }
  if (stage === 'generating_application_template_files') {
    return {
      description: '技术规划已确认，正在生成应用模板文件',
      icon: <LoadingOutlined spin />,
      title: `正在为「${application.appName}」生成应用模板文件`
    }
  }
  if (stage === 'generating_technical_plan') {
    return {
      description: '已进入独立规划阶段，正在生成技术规划',
      icon: <LoadingOutlined spin />,
      title: `阶段 4/4：正在生成「${application.appName}」的技术规划`
    }
  }
  if (stage === 'generating_ui_designs') {
    return {
      description: '产品规划已确认，正在生成 React UI 设计稿',
      icon: <LoadingOutlined spin />,
      title: `阶段 3/4：正在生成「${application.appName}」的 UI 设计稿`
    }
  }
  if (stage === 'generating_requirement_document') {
    return {
      description: '正在整合需求事实、页面与操作规划',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/4：正在生成「${application.appName}」的需求文档`
    }
  }
  return {
    description: '正在分析需求并识别待补充信息',
    icon: <LoadingOutlined spin />,
    title: `阶段 1/4：正在分析「${application.appName}」的需求`
  }
}

// 在首页展示尚未结束的应用规划，并提供返回全屏规划页的入口。
export default function ActivePlanningAction({
  application,
  deleting,
  lifecycle,
  onDelete,
  onOpen,
  status
}: Props): JSX.Element {
  const presentation = planningStatusPresentation(application, lifecycle, status)
  return (
    <div className={cx('active-planning-action-shell', `status-${status}`)}>
      <Button className={cx('active-planning-action')} onClick={onOpen} type="text">
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
      <Button
        className={cx('active-planning-action-delete')}
        danger
        disabled={deleting}
        icon={<DeleteOutlined />}
        loading={deleting}
        onClick={onDelete}
        type="text"
      >
        删除
      </Button>
    </div>
  )
}
