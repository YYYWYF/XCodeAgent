import { ApiOutlined, CaretDownOutlined, FolderOpenOutlined, RobotOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningApiContract } from '../../../../typings'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import { apiEndpointDisplayPath } from '../../utils'
import { cx } from '../../../../utils'
import { isAgentInvocationEndpoint } from '../../../../developmentArtifacts'

/** 为业务映射和 Agent 接入设计共用目录，切换分支后仍可选择其他接口。 */
export default function ApiContractDirectory({ contracts, target, disabled, onSelect }: {
  contracts: DevelopmentPlanningApiContract[]; target?: ApiDesignConfigTarget; disabled?: boolean
  onSelect: (target: ApiDesignConfigTarget) => void
}): ReactElement {
  return <nav className="binding-directory" aria-label="应用 API"><h4>应用 API</h4>
    {contracts.map((contract) => <section className="binding-contract" key={contract.id}>
      <div className="binding-contract-heading"><CaretDownOutlined /><FolderOpenOutlined />
        <strong>{contract.name || '未命名接口分组'}</strong></div>
      <div className="binding-endpoint-list">{contract.endpoints.map((endpoint) => {
        const path = endpoint.path || '/'
        const name = endpoint.name || apiEndpointDisplayPath(path, contract.label) || '未命名接口'
        const apiContractId = endpoint.apiContractId || contract.id
        const selected = target?.apiContractId === apiContractId && target.endpointId === endpoint.id
        const Icon = isAgentInvocationEndpoint(endpoint) ? RobotOutlined : ApiOutlined
        return <Tooltip key={`${apiContractId}:${endpoint.id}`} placement="right" mouseEnterDelay={1}
          mouseLeaveDelay={0.08} align={{ offset: [4, 0] }} overlayClassName={cx('api-hover-tooltip')}
          title={<span className={cx('api-hover-tooltip-content')}><span className={cx('api-hover-tooltip-method')}>
            {endpoint.method}</span><code>{path}</code></span>}>
          <span className={cx('api-tooltip-anchor')}><button aria-current={selected ? 'true' : undefined}
            aria-label={`${contract.name || '未命名接口分组'}，${name}，${endpoint.method} ${path}`}
            className={selected ? 'selected' : ''} disabled={disabled}
            onClick={() => onSelect({ apiContractId, endpointId: endpoint.id, label: name })}>
            <Icon className="binding-endpoint-icon" /><span className="binding-endpoint-copy"><strong>{name}</strong></span>
          </button></span></Tooltip>
      })}</div>
    </section>)}
  </nav>
}
