import { ApiOutlined, CaretDownOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { ApplicationOutlineProps } from './index'
import { apiEndpointSelectionKey } from './outlineUtils'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import IterationBadge from '../../../../components/IterationBadge'
import { WorkbenchChangeBadge, isDeletedStatus } from './WorkbenchChangeBadge'

type Props = {
  contract: DevelopmentPlanningApiContract
  allEndpoints: DevelopmentPlanningApiContract['endpoints']
  /** 当前迭代的分支名，用于标注接口归属。 */
  currentBranch?: string
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
  currentBranch,
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
        aria-label={`${contract.name || '未命名接口分组'}，${contract.label}`}
        className={cx('api-group-title')}
        onClick={onToggle}
        type="button"
      >
        <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
        <FolderOpenOutlined />
        <span className={cx('api-group-label')}>
          {/* 分组只作为目录，不展示变更标识：变更只落在组内具体接口上。 */}
          <strong>{contract.name || '未命名接口分组'}</strong>
        </span>
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
            // 已删除的接口仍可点选：点进去执行的是清理该接口产物的删除动作，
            // 与页面的待删除项保持一致（整组删除时组内接口同样视为删除）。
            const deleted = isDeletedStatus(endpoint.changeStatus) || isDeletedStatus(contract.changeStatus)
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
                      aria-label={`${endpoint.name || displayPath}，${endpoint.method} ${endpoint.path}${endpoint.summary ? `，${endpoint.summary}` : ''}`}
                      className={cx('api-row', selectedKey === endpointKey && 'selected', deleted && 'is-deleted')}
                      onClick={() => {
                        onSelect({
                          apiContractId,
                          endpointId,
                          endpointKey,
                          label: `${endpoint.name || displayPath} · ${endpoint.method} ${displayPath}`.trim()
                        })
                      }}
                      title={deleted ? '本轮待删除，进入开发流程后会清理该接口的代码' : undefined}
                      type="button"
                    >
                      <ApiOutlined className={cx('api-endpoint-icon')} />
                      <span className={cx('api-copy')}>
                        {/* 名称与变更徽标同一行：徽标跟在名称后，而不是另起一行占满宽度。 */}
                        <span className={cx('api-endpoint-title-row')}>
                          <strong>{endpoint.name || displayPath}</strong>
                          <WorkbenchChangeBadge
                            status={endpoint.changeStatus || (deleted ? 'deleted' : undefined)}
                          />
                        </span>
                      </span>
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
