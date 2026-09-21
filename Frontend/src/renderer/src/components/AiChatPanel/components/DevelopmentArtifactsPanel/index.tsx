import { Button } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import ApplicationOutline from '../ApplicationOutline'
import AgentDevelopmentDetail from '../AgentDevelopmentDetail'
import type {
  ApplicationLifecycle,
  DevelopmentArtifactTarget,
  DevelopmentPlanningAgentOption,
  WorkbenchExecution
} from '../../../../typings'
import type { ApplicationOutlineProps } from '../ApplicationOutline'
import EndpointDesignResult from '../EndpointDesignResult'
import { useEndpointDesignDetail } from '../../hooks/useEndpointDesignDetail'
import DevelopmentTargetDetail from './DevelopmentTargetDetail'
import './DevelopmentArtifactsPanel.less'

type Props = ApplicationOutlineProps & {
  detailLabel?: string
  apiTarget?: { apiContractId: string; endpointId: string }
  apiDesignRefreshKey?: string
  developmentDisabled?: boolean
  applicationLifecycle?: ApplicationLifecycle
  onEndAgentExecution?: (execution: WorkbenchExecution) => Promise<boolean>
  onOpenAgentExecution?: (execution: WorkbenchExecution) => Promise<void>
  onAgentSettingsApplied?: () => void
  onStartAgentDevelopment?: (agent: DevelopmentPlanningAgentOption) => void
  onStartDevelopment?: (target: DevelopmentArtifactTarget) => void
  workspaceRoot?: string
}

/** 并排展示常驻菜单和当前选中产物的开发详情。 */
export default function DevelopmentArtifactsPanel({
  detailLabel,
  apiTarget,
  apiDesignRefreshKey,
  developmentDisabled,
  applicationLifecycle,
  onEndAgentExecution,
  onOpenAgentExecution,
  onAgentSettingsApplied,
  onStartAgentDevelopment,
  onStartDevelopment,
  workspaceRoot,
  ...outlineProps
}: Props): ReactElement {
  const selectedAgent = outlineProps.agents.find(
    (agent) => agent.agentId === outlineProps.selectedAgentId
  )
  const selectedPage = outlineProps.pages.find((page) => page.pageId === outlineProps.selectedPageId)
  const selectedEntity = outlineProps.entities.find(
    (entity) => entity.id === outlineProps.selectedEntityId
  )
  const selectedEndpoint = apiTarget
    ? outlineProps.apiContracts
        .flatMap((contract) =>
          contract.endpoints.map((endpoint) => ({
            ...endpoint,
            apiContractId: endpoint.apiContractId || contract.id
          }))
        )
        .find(
          (endpoint) =>
            endpoint.apiContractId === apiTarget.apiContractId && endpoint.id === apiTarget.endpointId
        )
    : undefined
  const { detail, error, loading, reload } = useEndpointDesignDetail(
    workspaceRoot,
    apiTarget,
    apiDesignRefreshKey
  )
  return (
    <div className={cx('development-artifacts-panel')}>
      <aside aria-label="开发产物菜单" className={cx('development-artifacts-menu')}>
        <ApplicationOutline {...outlineProps} />
      </aside>
      <section
        aria-label={detailLabel ? `${detailLabel}详情` : '开发产物详情'}
        className={cx('development-artifacts-detail')}
      >
        {selectedAgent && onStartAgentDevelopment ? (
          <AgentDevelopmentDetail
            agent={selectedAgent}
            applicationLifecycle={applicationLifecycle}
            disabled={developmentDisabled}
            onEndAgentExecution={onEndAgentExecution}
            onOpenAgentExecution={onOpenAgentExecution}
            onSettingsApplied={onAgentSettingsApplied || (() => undefined)}
            onStartDevelopment={onStartAgentDevelopment}
            workspaceRoot={workspaceRoot}
          />
        ) : apiTarget ? (
          loading ? (
            <div
              aria-atomic="true"
              className={cx('development-artifacts-placeholder')}
              role="status"
            >
              <h3>正在读取映射结果…</h3>
            </div>
          ) : error ? (
            <div
              aria-atomic="true"
              className={cx('development-artifacts-placeholder')}
              role="status"
            >
              <h3>读取失败</h3>
              <p>{error}</p>
              <Button onClick={reload} type="primary">
                重试
              </Button>
            </div>
          ) : (
            <DevelopmentTargetDetail
              description={selectedEndpoint?.summary}
              disabled={developmentDisabled}
              extra={<EndpointDesignResult detail={detail} />}
              kind="endpoint"
              progress={
                outlineProps.developmentArtifacts?.endpoints[apiTarget.apiContractId]?.[
                  apiTarget.endpointId
                ]
              }
              subtitle={`${String(selectedEndpoint?.method || 'API').toUpperCase()} ${selectedEndpoint?.path || ''}`}
              title={detailLabel || apiTarget.endpointId}
              onStart={() =>
                onStartDevelopment?.({
                  type: 'endpoint',
                  apiContractId: apiTarget.apiContractId,
                  endpointId: apiTarget.endpointId
                })
              }
            />
          )
        ) : selectedPage ? (
          <DevelopmentTargetDetail
            description={selectedPage.purpose}
            disabled={developmentDisabled}
            kind="page"
            progress={outlineProps.developmentArtifacts?.pages[selectedPage.pageId]}
            subtitle={selectedPage.path}
            title={selectedPage.label}
            onStart={() =>
              onStartDevelopment?.({ type: 'page', pageId: selectedPage.pageId })
            }
          />
        ) : selectedEntity ? (
          <DevelopmentTargetDetail
            description={selectedEntity.purpose}
            disabled={developmentDisabled}
            kind="entity"
            progress={outlineProps.developmentArtifacts?.entities[selectedEntity.id]}
            subtitle={selectedEntity.dataSourceType}
            title={selectedEntity.label}
            onStart={() =>
              onStartDevelopment?.({ type: 'entity', entityId: selectedEntity.id })
            }
          />
        ) : (
          <div aria-atomic="true" className={cx('development-artifacts-placeholder')} role="status">
            <h3>请选择开发产物</h3>
            <p>点击左侧菜单查看开发状态，并开始尚未完成的页面、接口或实体。</p>
          </div>
        )}
      </section>
    </div>
  )
}
