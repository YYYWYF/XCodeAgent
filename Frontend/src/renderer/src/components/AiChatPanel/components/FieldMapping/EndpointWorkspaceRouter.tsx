import { Alert, Button, Empty, Spin } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningApiContract, EndpointDesignPreparation, EndpointDesignSaveResult } from '../../../../typings'
import { isRuntimeEndpointPreparation } from '../../../../typings/endpointDesign'
import type { EndpointPreparation } from '../../../../typings/endpointDesign'
import { requestEndpointPreparation } from '../../../../service/endpointDesigns'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import AgentEndpointReview from './AgentEndpointReview'
import ApiContractDirectory from './ApiContractDirectory'

type Route = { key: string; generation: number; preparation?: EndpointPreparation; error?: string }
type Props = {
  workspaceRoot: string; target?: ApiDesignConfigTarget; contracts: DevelopmentPlanningApiContract[]
  onSelect: (target: ApiDesignConfigTarget) => void
  onSaved: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>
  renderBusiness: (target: ApiDesignConfigTarget | undefined, preparation?: EndpointDesignPreparation) => ReactElement
}

/** 正式准备结果先于编辑器挂载；保留业务编辑器实例，切换接口不丢失未提交输入。 */
export default function EndpointWorkspaceRouter(props: Props): ReactElement {
  const { workspaceRoot, target, contracts, onSelect, onSaved, renderBusiness } = props
  const key = target ? JSON.stringify([workspaceRoot, target.apiContractId, target.endpointId]) : ''
  const [resolved, setResolved] = useState<Route>()
  const [generation, setGeneration] = useState(0)
  const [hasBusiness, setHasBusiness] = useState(false)
  useEffect(() => {
    if (!target) return
    let disposed = false
    requestEndpointPreparation(workspaceRoot, target.apiContractId, target.endpointId).then((preparation) => {
      if (disposed) return
      if (!isRuntimeEndpointPreparation(preparation)) setHasBusiness(true)
      setResolved({ key, generation, preparation })
    }).catch((reason: unknown) => {
      if (!disposed) setResolved({ key, generation,
        error: reason instanceof Error ? reason.message : '读取接口设计失败。'
      })
    })
    return () => { disposed = true }
  }, [workspaceRoot, target?.apiContractId, target?.endpointId, generation])
  const route = resolved?.key === key && resolved.generation === generation ? resolved : undefined
  const preparation = route?.preparation
  const runtime = preparation && isRuntimeEndpointPreparation(preparation) ? preparation : undefined
  const business = preparation && !isRuntimeEndpointPreparation(preparation) ? preparation : undefined
  return <>
    {!business && <div className="binding-workspace">
      <ApiContractDirectory contracts={contracts} target={target} onSelect={onSelect} />
      <main className="binding-editor">
        {!target ? <Empty description="请选择应用 API" /> : runtime ? <AgentEndpointReview
          key={key} workspaceRoot={workspaceRoot} target={target} preparation={runtime} onSaved={onSaved} />
          : route?.error ? <Alert type="error" showIcon message={route.error}
            action={<Button onClick={() => setGeneration((value) => value + 1)}>重新读取</Button>} />
            : <Spin tip="正在读取接口设计…" />}
      </main>
    </div>}
    {hasBusiness && <div style={{ display: business ? 'contents' : 'none' }}>
      {renderBusiness(business ? target : undefined, business)}
    </div>}
  </>
}
