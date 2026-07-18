import {
  ArrowRightOutlined,
  CheckCircleFilled,
  ExclamationCircleFilled,
  LoadingOutlined
} from '@ant-design/icons'
import { Button } from 'antd'
import type { ApplicationConfig } from '../../typings'
import type { ActivePlanningStatus } from '../../service/activeApplicationPlanning'
import { cx } from '../../utils'

type Props = {
  application: ApplicationConfig
  onOpen: () => void
  status: ActivePlanningStatus
}

// 根据规划状态返回首页入口所需的图标与文案。
function planningStatusPresentation(
  application: ApplicationConfig,
  status: ActivePlanningStatus
): { description: string; icon: JSX.Element; title: string } {
  if (status === 'ready') {
    return {
      description: '规划内容已经生成完成，点击进入查看并确认',
      icon: <CheckCircleFilled />,
      title: `「${application.appName}」的应用规划已生成`
    }
  }
  if (status === 'error') {
    return {
      description: '规划流程需要处理，点击进入查看详情或重试',
      icon: <ExclamationCircleFilled />,
      title: `「${application.appName}」的应用规划需要处理`
    }
  }
  return {
    description: '任务会在后台继续运行，点击查看生成进度',
    icon: <LoadingOutlined spin />,
    title: `正在生成「${application.appName}」的应用规划`
  }
}

// 在首页展示尚未结束的应用规划，并提供返回全屏规划页的入口。
export default function ActivePlanningAction({ application, onOpen, status }: Props): JSX.Element {
  const presentation = planningStatusPresentation(application, status)
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
