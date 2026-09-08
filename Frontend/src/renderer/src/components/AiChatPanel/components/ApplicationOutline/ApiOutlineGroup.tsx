import { ApiOutlined, CaretDownOutlined } from '@ant-design/icons'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { ApplicationOutlineProps } from './index'
import { apiEndpointSelectionKey } from './outlineUtils'
import DevelopmentStatusDot from './DevelopmentStatusDot'

type Props = {
  contract: DevelopmentPlanningApiContract
  allEndpoints: DevelopmentPlanningApiContract['endpoints']
  developmentArtifacts?: DevelopmentArtifacts
  expanded: boolean
  onToggle: () => void
  onSelect: ApplicationOutlineProps['onApiEndpointSelect']
  selectedKey: string
}

/** 展示接口分组与三态圆点，分组计数始终包含被搜索条件隐藏的接口。 */
export default function ApiOutlineGroup({
  contract,
  allEndpoints,
  developmentArtifacts,
  expanded,
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
  return (
    <div>
      <button
        aria-expanded={expanded}
        className={cx('api-group-title')}
        onClick={onToggle}
        type="button"
      >
        <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
        <ApiOutlined />
        <code>{contract.label}</code>
        <span className={cx('development-count')}>
          {completed}/{allEndpoints.length}
        </span>
      </button>
      {expanded ? (
        <div className={cx('api-list')}>
          {contract.endpoints.map((endpoint) => {
            const endpointId = endpoint.id
            const apiContractId = endpoint.apiContractId || contract.id
            const endpointKey = apiEndpointSelectionKey(apiContractId, endpointId)
            const displayPath = apiEndpointDisplayPath(endpoint.path, contract.label)
            return (
              <div className={cx('api-node')} key={endpointKey}>
                <button
                  aria-current={selectedKey === endpointKey ? 'true' : undefined}
                  className={cx('api-row', selectedKey === endpointKey && 'selected')}
                  onClick={() =>
                    onSelect({
                      apiContractId,
                      endpointId,
                      endpointKey,
                      label: `${endpoint.method} ${displayPath}`.trim()
                    })
                  }
                  title={endpoint.summary}
                  type="button"
                >
                  <span className={cx('api-method', endpoint.method.toLocaleLowerCase())}>
                    {endpoint.method}
                  </span>
                  <code>{displayPath}</code>
                  <DevelopmentStatusDot
                    progress={developmentArtifacts?.endpoints[apiContractId]?.[endpointId]}
                  />
                </button>
              </div>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}
