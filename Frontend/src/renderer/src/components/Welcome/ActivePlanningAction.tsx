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
      description: lifecycle.error?.message || '规划流程需要处理，点击进入查看详情或重试',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用规划需要处理`
    }
  }
  // 技术执行态优先于上一次持久化的待交互阶段，避免生成期间提前提示产物已完成。
  if (status === 'running' && stage === 'awaiting_requirement_clarification') {
    return {
      description: '正在根据刚刚补充的信息生成需求文档',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/2：正在生成「${application.appName}」的需求文档`
    }
  }
  if (status === 'running' && stage === 'awaiting_requirement_confirmation') {
    return {
      description: '正在处理需求文档并准备后续项目计划',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/2：正在处理「${application.appName}」的需求文档`
    }
  }
  if (status === 'running' && stage === 'awaiting_project_plan_confirmation') {
    return {
      description: '正在处理项目计划并准备应用模板文件',
      icon: <LoadingOutlined spin />,
      title: `阶段 2/2：正在处理「${application.appName}」的项目规划`
    }
  }
  if (stage === 'awaiting_requirement_clarification') {
    return {
      description: '需要补充关键信息，完成后继续生成需求文档',
      icon: <CheckCircleFilled />,
      title: `阶段 1/2：补充「${application.appName}」的需求细节`
    }
  }
  if (stage === 'awaiting_requirement_confirmation') {
    return {
      description: '需求文档已生成，确认后继续生成项目计划',
      icon: <CheckCircleFilled />,
      title: `阶段 1/2：确认「${application.appName}」的需求文档`
    }
  }
  if (stage === 'awaiting_project_plan_confirmation') {
    return {
      description: '项目计划已生成，确认后进入工作台',
      icon: <CheckCircleFilled />,
      title: `阶段 2/2：确认「${application.appName}」的项目规划`
    }
  }
  if (stage === 'application_template_generation_failed') {
    return {
      description: lifecycle.error?.message || '应用模板文件生成失败，点击查看详情或重试',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用模板文件生成失败`
    }
  }
  if (stage === 'generating_application_template_files') {
    return {
      description: '项目计划已确认，正在生成应用模板文件',
      icon: <LoadingOutlined spin />,
      title: `正在为「${application.appName}」生成应用模板文件`
    }
  }
  if (stage === 'generating_project_plan') {
    return {
      description: '需求文档已确认，正在生成项目计划',
      icon: <LoadingOutlined spin />,
      title: `阶段 2/2：正在生成「${application.appName}」的项目规划`
    }
  }
  if (stage === 'generating_requirement_spec') {
    return {
      description: '需求分析已完成，正在生成需求文档',
      icon: <LoadingOutlined spin />,
      title: `阶段 1/2：正在生成「${application.appName}」的需求文档`
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
