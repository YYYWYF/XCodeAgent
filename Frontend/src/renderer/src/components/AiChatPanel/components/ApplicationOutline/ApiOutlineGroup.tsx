import { ApiOutlined, CaretDownOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Tag, Tooltip } from 'antd'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { ApplicationOutlineProps } from './index'
import { apiEndpointSelectionKey } from './outlineUtils'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import IterationBadge from '../../../../components/IterationBadge'

type Props = {
  contract: DevelopmentPlanningApiContract
  allEndpoints: DevelopmentPlanningApiContract['endpoints']
  /** 当前迭代的分支名，用于标注接口归属。 */
  currentBranch?: string
  developmentArtifacts?: DevelopmentArtifacts
  expanded: boolean
  gatewayEndpointKeys?: Set<string>
  onToggle: () => void
  onSelect: ApplicationOutlineProps['onApiEndpointSelect']
  selectedKey: string
}

/** 展示普通接口分组或单个智能体接口，并按未过滤的接口计算三态进度。 */
export default function ApiOutlineGroup({
  contract,
  allEndpoints,
  currentBranch,
  developmentArtifacts,
  expanded,
  gatewayEndpointKeys,
  onToggle,
  onSelect,
  selectedKey
}: Props): JSX.Element {
  const completed = developmentCompletedCount(
    allEndpoints.map(
      (endpoint) =>
        developmentArtifacts?.endpoints[endpoint.apiContractId || contract.id]?.[endpoint.id]
    )
  )
  // 只扁平展示完整 Contract 中唯一的智能体接口，避免搜索结果改变分组结构。
  const standaloneAgentGateway =
    allEndpoints.length === 1 &&
    gatewayEndpointKeys?.has(
      apiEndpointSelectionKey(allEndpoints[0].apiContractId || contract.id, allEndpoints[0].id)
    ) === true
  return (
    <div>
      {!standaloneAgentGateway ? (
        <button
          aria-expanded={expanded}
          aria-label={`${contract.name || '未命名接口分组'}，${contract.label}`}
          className={cx('api-group-title')}
          onClick={onToggle}
          type="button"
        >
          <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
          <FolderOpenOutlined />
          <span className={cx('api-group-label')}>
            <strong>{contract.name || '未命名接口分组'}</strong>
          </span>
          <span className={cx('development-count')}>
            {completed}/{allEndpoints.length}
          </span>
        </button>
      ) : null}
      {standaloneAgentGateway || expanded ? (
        <div className={cx('api-list', standaloneAgentGateway && 'api-list-flat')}>
          {contract.endpoints.map((endpoint) => {
            const endpointId = endpoint.id
            const apiContractId = endpoint.apiContractId || contract.id
            const endpointKey = apiEndpointSelectionKey(apiContractId, endpointId)
            const isAgentGateway = gatewayEndpointKeys?.has(endpointKey) === true
            const displayPath = apiEndpointDisplayPath(endpoint.path, contract.label)
            return (
              <div className={cx('api-node')} key={endpointKey}>
                <Tooltip
                  align={{ offset: [4, 0] }}
                  mouseEnterDelay={1}
                  mouseLeaveDelay={0.08}
                  overlayClassName={cx('api-hover-tooltip')}
                  placement="right"
                  title={
                    <span className={cx('api-hover-tooltip-content')}>
                      <span className={cx('api-hover-tooltip-method')}>{endpoint.method}</span>
                      <code>{endpoint.path}</code>
                    </span>
                  }
                >
                  <span className={cx('api-tooltip-anchor')}>
                    <button
                      aria-current={selectedKey === endpointKey ? 'true' : undefined}
                      aria-label={`${endpoint.name || displayPath}，${endpoint.method} ${endpoint.path}${isAgentGateway ? '，智能体接口' : ''}${endpoint.summary ? `，${endpoint.summary}` : ''}`}
                      className={cx('api-row', selectedKey === endpointKey && 'selected')}
                      onClick={() =>
                        onSelect({
                          apiContractId,
                          endpointId,
                          endpointKey,
                          label:
                            `${endpoint.name || displayPath} · ${endpoint.method} ${displayPath}`.trim()
                        })
                      }
                      type="button"
                    >
                      <ApiOutlined className={cx('api-endpoint-icon')} />
                      <span className={cx('api-copy')}>
                        <strong>{endpoint.name || displayPath}</strong>
                      </span>
                      {isAgentGateway ? (
                        <Tag className={cx('agent-gateway-tag')}>智能体接口</Tag>
                      ) : null}
                      <DevelopmentStatusDot
                        progress={developmentArtifacts?.endpoints[apiContractId]?.[endpointId]}
                      />
                      <IterationBadge
                        artifactBranch={
                          developmentArtifacts?.endpoints[apiContractId]?.[endpointId]
                            ?.completedBranchName
                        }
                        currentBranch={currentBranch}
                      />
                    </button>
                  </span>
                </Tooltip>
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
