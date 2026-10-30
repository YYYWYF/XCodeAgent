import { Alert, Button } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import ApplicationOutline from '../ApplicationOutline'
import type { ApplicationOutlineProps } from '../ApplicationOutline'
import EndpointDesignResult from '../EndpointDesignResult'
import EntityInfoPanel from '../EntityInfoPanel'
import AgentDevelopmentDetail from '../AgentDevelopmentDetail'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import { useEndpointDesignDetail } from '../../hooks/useEndpointDesignDetail'
import type { EndpointRecoveryReporter } from '../../hooks/useEndpointDesignRecovery'
import RuntimeAgentDetail from '../RuntimeAgentDetail'
import { requestRuntimeWorkbench } from '../../../../service/runtimeWorkbench'
import type { RuntimeAgent, RuntimeSettingsResult } from '../../../../service/runtimeWorkbench'
import type { WorkbenchExecution } from '../../../../typings'
import './DevelopmentArtifactsPanel.less'

type Props = ApplicationOutlineProps & {
  onStartAgent?: (agent: BusinessAgentOption) => void
  onAgentRefresh?: () => void
  agentActionsDisabled?: boolean
  theme: 'light' | 'dark'
  useBuiltinDatabase?: boolean
  onEndpointRecovery?: EndpointRecoveryReporter
  detailLabel?: string
  apiTarget?: { apiContractId: string; endpointId: string }
  workspaceRoot?: string
  apiDesignRefreshKey?: number
  onConfigureApi?: (target: { apiContractId: string; endpointId: string; label?: string }) => void
  onAgentSettingsSaved?: (result: RuntimeSettingsResult) => void
  onStartAgentDevelopment?: (agent: RuntimeAgent) => Promise<void>
  onOpenAgentExecution?: (execution: WorkbenchExecution) => void
  agentDevelopmentDisabled?: boolean
  agentStateRevision?: number
}

/** 并排展示产物菜单与接口、实体详情，菜单选择不改变开发工作流。 */
export default function DevelopmentArtifactsPanel({
  theme,
  useBuiltinDatabase,
  detailLabel,
  apiTarget,
  workspaceRoot,
  apiDesignRefreshKey,
  onConfigureApi,
  onEndpointRecovery,
  onStartAgent,
  onAgentRefresh,
  agentActionsDisabled,
  onAgentSettingsSaved,
  onStartAgentDevelopment,
  onOpenAgentExecution,
  agentDevelopmentDisabled,
  agentStateRevision,
  ...outlineProps
}: Props): ReactElement {
  const [agents, setAgents] = useState<RuntimeAgent[]>([])
  const [agentId, setAgentId] = useState('')
  const [agentError, setAgentError] = useState('')
  const [agentRefresh, setAgentRefresh] = useState(0)
  const selectedAgent = outlineProps.agents?.find((item) => item.agentId === outlineProps.selectedAgentId)
  const agent = agents.find((item) => item.agentId === (agentId || selectedAgent?.agentId))
  useEffect(() => { setAgents([]); setAgentId('') }, [workspaceRoot])
  useEffect(() => {
    let active = true
    setAgentError('')
    if (workspaceRoot) void requestRuntimeWorkbench(workspaceRoot, 'list').then((result) => {
      if (active) setAgents(result.agents || [])
    }).catch((cause) => {
      if (active) setAgentError(cause instanceof Error ? cause.message : 'Agent 目录读取失败。')
    })
    return () => { active = false }
  }, [workspaceRoot, apiDesignRefreshKey, agentRefresh, agentStateRevision])
  const selectedEntity = outlineProps.entities.find(
    (entity) => entity.id === outlineProps.selectedEntityId
  )
  const { detail, error, loading, reload } = useEndpointDesignDetail(
    workspaceRoot,
    apiTarget,
    apiDesignRefreshKey,
    onEndpointRecovery
  )
  return (
    <div className={cx('development-artifacts-panel')}>
      <aside aria-label="开发产物菜单" className={cx('development-artifacts-menu')}>
        <ApplicationOutline {...outlineProps}
          onAgentSelect={(item) => { setAgentId(''); outlineProps.onAgentSelect?.(item) }}
          onPageSelect={(item) => { setAgentId(''); outlineProps.onAgentSelect?.(undefined); outlineProps.onPageSelect(item) }}
          onEntitySelect={(item) => { setAgentId(''); outlineProps.onAgentSelect?.(undefined); outlineProps.onEntitySelect(item) }}
          onApiEndpointSelect={(item) => { setAgentId(''); outlineProps.onAgentSelect?.(undefined); outlineProps.onApiEndpointSelect(item) }} />
        <div className={cx('runtime-agent-outline')}>
          <strong>智能体</strong>
          <Button size="small" onClick={() => { setAgentRefresh((value) => value + 1); onAgentRefresh?.() }}>刷新</Button>
          {agentError && <Alert type="error" message={agentError} />}
          {!outlineProps.agents?.length && agents.map((item) => <Button key={item.agentId} type={item.agentId === agentId ? 'primary' : 'text'}
            block onClick={() => setAgentId(item.agentId)}>{item.name}</Button>)}
        </div>
      </aside>
      <section
        aria-label={detailLabel ? `${detailLabel}详情` : '开发产物详情'}
        className={cx('development-artifacts-detail')}
      >
        {!agent && !selectedAgent && apiTarget && onConfigureApi ? (
          <Button onClick={() => onConfigureApi({ ...apiTarget, label: detailLabel })}>
            打开字段映射
          </Button>
        ) : null}
        {agent && workspaceRoot ? <RuntimeAgentDetail key={`${workspaceRoot}:${agent.agentId}`} agent={agent} workspaceRoot={workspaceRoot}
          disabled={agentDevelopmentDisabled || agentActionsDisabled} onStartDevelopment={onStartAgentDevelopment} onOpenExecution={onOpenAgentExecution}
          onSaved={(result) => { onAgentSettingsSaved?.(result); onAgentRefresh?.(); setAgentRefresh((value) => value + 1) }} /> : selectedAgent && workspaceRoot ? (
          <AgentDevelopmentDetail
            key={`${workspaceRoot}:${selectedAgent.agentId}`}
            agent={selectedAgent}
            workspaceRoot={workspaceRoot}
            disabled={Boolean(agentActionsDisabled)}
            onStart={() => onStartAgent?.(selectedAgent)}
            onRefresh={() => { onAgentRefresh?.(); setAgentRefresh((value) => value + 1) }}
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
              {onEndpointRecovery ? (
                <p>请使用下方重试入口同步映射结果。</p>
              ) : (
                <>
                  <p>{error}</p>
                  <Button onClick={reload} type="primary">
                    重试
                  </Button>
                </>
              )}
            </div>
          ) : (
            <EndpointDesignResult detail={detail} />
          )
        ) : selectedEntity ? (
          <EntityInfoPanel
            entity={selectedEntity}
            theme={theme}
            workspaceRoot={workspaceRoot}
            useBuiltinDatabase={useBuiltinDatabase}
          />
        ) : (
          <div aria-atomic="true" className={cx('development-artifacts-placeholder')} role="status">
            <h3>{detailLabel || '请选择开发产物'}</h3>
            <p>{detailLabel ? '详情内容待设计' : '点击左侧菜单查看详情'}</p>
          </div>
        )}
      </section>
    </div>
  )
}
