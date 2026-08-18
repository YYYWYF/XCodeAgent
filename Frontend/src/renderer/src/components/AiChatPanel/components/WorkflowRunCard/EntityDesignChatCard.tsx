import { LoadingOutlined } from '@ant-design/icons'
import { Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowRunPayload } from '../../../../typings'
import { cx } from '../../../../utils'
import { EntityDesignCard } from './EntityDesignPanels'
import { workflowClarification, type ClarificationAnswers } from './index'
import './EntityDesignChatCard.less'

const { Text } = Typography

type EntityDesignChatCardProps = {
  disabled?: boolean
  loading?: boolean
  onInteraction?: () => void
  onSubmitClarification?: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => void
  workflow?: WorkflowRunPayload
  workspaceRoot?: string
}

/** 以 AI 对话气泡展示实体设计：后端对话文本 + 内嵌交互卡片，隐藏工作流外壳。 */
export default function EntityDesignChatCard({
  disabled,
  loading = false,
  onInteraction,
  onSubmitClarification,
  workflow,
  workspaceRoot
}: EntityDesignChatCardProps): ReactElement {
  const clarification = workflow ? workflowClarification(workflow) : undefined
  const entityDesign = clarification?.review?.summary?.entityDesign
  const entityTarget = clarification?.review?.entities?.[0]

  if (loading && !workflow) {
    return (
      <div className={cx('entity-design-chat-bubble')}>
        <Spin indicator={<LoadingOutlined spin />} size="small" />
        <Text type="secondary">正在生成设计建议…</Text>
      </div>
    )
  }

  return (
    <div className={cx('entity-design-chat-bubble')}>
      {entityDesign ? (
        <EntityDesignCard
          disabled={disabled}
          entityDesign={entityDesign}
          entityTarget={entityTarget}
          onInteraction={onInteraction}
          onAction={(action) =>
            workflow &&
            onSubmitClarification?.(workflow, {
              entity_design: action
            })
          }
          workspaceRoot={workspaceRoot}
        />
      ) : null}
    </div>
  )
}
