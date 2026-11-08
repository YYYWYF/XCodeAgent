import type { Dispatch, SetStateAction } from 'react'
import { useState } from 'react'
import type { ApplicationOutlineProps } from '../components/ApplicationOutline'
import { ARTIFACT_DOCUMENTS } from '../components/ApplicationOutline/artifactDocuments'
import type { ArtifactDocumentKey } from '../components/ApplicationOutline/artifactDocuments'
import type { RightPanelLayout, RightPanelState } from '../types'

type ArtifactSelection = {
  applicationId: string
  label: string
  pageId?: string
  endpointKey?: string
  apiContractId?: string
  endpointId?: string
  entityId?: string
  documentKey?: ArtifactDocumentKey
}

type Options = {
  applicationId: string
  setRightPanel: (panel?: RightPanelState) => void
  /** 打开产物详情时需要确保右侧可见；隐藏态下切回分栏，已是分栏/全宽则不动。 */
  setRightPanelLayout: Dispatch<SetStateAction<RightPanelLayout>>
}

type DevelopmentArtifactDetail = {
  openArtifactOverview: () => void
  artifactDetailLabel?: string
  apiTarget?: { apiContractId: string; endpointId: string }
  artifactOutlineProps: Pick<
    ApplicationOutlineProps,
    | 'onPageSelect'
    | 'onApiEndpointSelect'
    | 'onEntitySelect'
    | 'selectedPageId'
    | 'selectedApiEndpointKey'
    | 'selectedEntityId'
    | 'onDocumentSelect'
    | 'selectedDocumentKey'
  >
}

/** 开发产物仅维护本地浏览选择，不改变会话、开发目标或工作流。 */
export function useDevelopmentArtifactDetail({
  applicationId,
  setRightPanel,
  setRightPanelLayout
}: Options): DevelopmentArtifactDetail {
  const [selection, setSelection] = useState<ArtifactSelection>()
  const currentSelection = selection?.applicationId === applicationId ? selection : undefined

  /** 更新产物浏览选择，保持菜单与对应详情同时展示。 */
  const openDetail = (target: Omit<ArtifactSelection, 'applicationId'>): void => {
    setSelection({ ...target, applicationId })
    setRightPanel({ type: 'outline' })
    // 隐藏态下打开详情要恢复可见；已是分栏/全宽则保持用户当前布局。
    setRightPanelLayout((current) => (current === 'hidden' ? 'split' : current))
  }

  /** 点击页面只打开其事实概览，不启动正式开发任务。 */
  const onPageSelect: ApplicationOutlineProps['onPageSelect'] = (page) => {
    openDetail({ pageId: page.pageId, label: page.label })
  }

  /** 点击 API 只打开该接口的空白详情。 */
  const onApiEndpointSelect: ApplicationOutlineProps['onApiEndpointSelect'] = (endpoint) => {
    openDetail({
      apiContractId: endpoint.apiContractId,
      endpointId: endpoint.endpointId,
      endpointKey: endpoint.endpointKey,
      label: endpoint.label
    })
  }

  /** 点击实体打开其已保存的信息，仅更新本地浏览选择。 */
  const onEntitySelect: ApplicationOutlineProps['onEntitySelect'] = (entity) => {
    openDetail({ entityId: entity.id, label: entity.label })
  }

  /** 文档浏览只打开应用产物详情，不切换阶段或触发下游开发。 */
  const onDocumentSelect = (documentKey: ArtifactDocumentKey): void => {
    openDetail({ documentKey, label: ARTIFACT_DOCUMENTS.find((doc) => doc.key === documentKey)!.label })
  }

  return {
    /** 无关联实体时清除上次浏览对象，仅展示现有产物目录。 */
    openArtifactOverview: (): void => {
      setSelection(undefined)
      setRightPanel({ type: 'outline' })
      setRightPanelLayout((current) => current === 'hidden' ? 'split' : current)
    },
    artifactDetailLabel: currentSelection?.label,
    apiTarget: currentSelection?.apiContractId && currentSelection.endpointId
      ? { apiContractId: currentSelection.apiContractId, endpointId: currentSelection.endpointId }
      : undefined,
    artifactOutlineProps: {
      onPageSelect,
      onApiEndpointSelect,
      onEntitySelect,
      onDocumentSelect,
      selectedDocumentKey: currentSelection?.documentKey,
      selectedPageId: currentSelection?.pageId || '',
      selectedApiEndpointKey: currentSelection?.endpointKey || '',
      selectedEntityId: currentSelection?.entityId || ''
    }
  }
}
