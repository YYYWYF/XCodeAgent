import { useState } from 'react'
import type { ApplicationOutlineProps } from '../components/ApplicationOutline'
import type { RightPanelState } from '../types'

type ArtifactSelection = {
  applicationId: string
  label: string
  pageId?: string
  endpointKey?: string
  entityId?: string
  agentId?: string
}

type Options = {
  applicationId: string
  setRightPanel: (panel?: RightPanelState) => void
  onRightPanelOpenChange: (open: boolean) => void
}

type DevelopmentArtifactDetail = {
  artifactDetailLabel?: string
  artifactOutlineProps: Pick<
    ApplicationOutlineProps,
    | 'onPageSelect'
    | 'onApiEndpointSelect'
    | 'onEntitySelect'
    | 'onAgentSelect'
    | 'selectedPageId'
    | 'selectedApiEndpointKey'
    | 'selectedEntityId'
    | 'selectedAgentId'
  >
}

/** 开发产物仅维护本地浏览选择并打开空白详情，不改变会话、开发目标或工作流。 */
export function useDevelopmentArtifactDetail({
  applicationId,
  setRightPanel,
  onRightPanelOpenChange
}: Options): DevelopmentArtifactDetail {
  const [selection, setSelection] = useState<ArtifactSelection>()
  const currentSelection = selection?.applicationId === applicationId ? selection : undefined

  /** 更新产物浏览选择，并保持开发产物内部的菜单与空白详情同时展示。 */
  const openDetail = (target: Omit<ArtifactSelection, 'applicationId'>): void => {
    setSelection({ ...target, applicationId })
    setRightPanel({ type: 'outline' })
    onRightPanelOpenChange(true)
  }

  /** 点击页面只打开该页面的空白详情。 */
  const onPageSelect: ApplicationOutlineProps['onPageSelect'] = (page) => {
    openDetail({ pageId: page.pageId, label: page.label })
  }

  /** 点击 API 只打开该接口的空白详情。 */
  const onApiEndpointSelect: ApplicationOutlineProps['onApiEndpointSelect'] = (endpoint) => {
    openDetail({ endpointKey: endpoint.endpointKey, label: endpoint.label })
  }

  /** 点击实体只打开该实体的空白详情。 */
  const onEntitySelect: ApplicationOutlineProps['onEntitySelect'] = (entity) => {
    openDetail({ entityId: entity.id, label: entity.label })
  }

  /** 点击智能体打开其只读 Contract 详情。 */
  const onAgentSelect: ApplicationOutlineProps['onAgentSelect'] = (agent) => {
    openDetail({ agentId: agent.agentId, label: agent.label })
  }

  return {
    artifactDetailLabel: currentSelection?.label,
    artifactOutlineProps: {
      onPageSelect,
      onApiEndpointSelect,
      onEntitySelect,
      onAgentSelect,
      selectedPageId: currentSelection?.pageId || '',
      selectedApiEndpointKey: currentSelection?.endpointKey || '',
      selectedEntityId: currentSelection?.entityId || '',
      selectedAgentId: currentSelection?.agentId || ''
    }
  }
}
