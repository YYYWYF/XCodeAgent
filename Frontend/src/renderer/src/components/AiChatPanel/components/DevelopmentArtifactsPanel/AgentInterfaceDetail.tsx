import { RobotOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import type { ReactElement } from 'react'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import type { DevelopmentArtifactProgress } from '../../../../typings'
import { developmentStatusText } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import TechnicalPlanAgentInvocation from '../DocPanel/TechnicalPlanAgentInvocation'
import DevelopmentStatusDot from '../ApplicationOutline/DevelopmentStatusDot'
import './AgentInterfaceDetail.less'

type Props = {
  agent: BusinessAgentOption
  progress?: DevelopmentArtifactProgress
  onOpenAgent: () => void
}

/** 展示既有 Agent 调用契约，导航到同一个 Agent，不创建独立接口开发任务。 */
export default function AgentInterfaceDetail({
  agent,
  progress,
  onOpenAgent
}: Props): ReactElement {
  return (
    <div className={cx('agent-interface-detail')}>
      <header>
        <RobotOutlined />
        <h2>{agent.label} · 智能体接口</h2>
      </header>
      <p>{agent.purpose}</p>
      <div className={cx('agent-interface-development')}>
        <DevelopmentStatusDot progress={progress} />
        <span>智能体开发状态：{developmentStatusText(progress)}</span>
        <Button onClick={onOpenAgent}>查看智能体</Button>
      </div>
      <TechnicalPlanAgentInvocation agentId={agent.agentId} pageIds={agent.pageIds} />
    </div>
  )
}
