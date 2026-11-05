import { ApiOutlined, RobotOutlined, CaretDownOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { ApplicationOutlineProps } from './index'
import { apiEndpointSelectionKey } from './outlineUtils'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import IterationBadge from '../../../../components/IterationBadge'
import { developmentCompletedCount, endpointDevelopmentProgress, endpointDevelopmentStatusLabel, isAgentInvocationEndpoint } from '../../../../developmentArtifacts'

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

/** 展示接口实际开发状态，分组计数始终包含被搜索条件隐藏的接口。 */
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
  const completed = developmentCompletedCount(allEndpoints.map((endpoint) =>
    endpointDevelopmentProgress(developmentArtifacts, contract.id, endpoint)))
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
          <strong>{contract.name || '未命名接口分组'}</strong>
        </span>
        <span className={cx('development-count')} title="当前开发已完成且无变更的接口数量">
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
                      className={cx('api-row', selectedKey === endpointKey && 'selected')}
                      onClick={() =>
                        onSelect({
                          apiContractId,
                          endpointId,
                          endpointKey,
                          label: `${endpoint.name || displayPath} · ${endpoint.method} ${displayPath}`.trim()
                        })
                      }
                      type="button"
                    >
                      {isAgentInvocationEndpoint(endpoint) ? <RobotOutlined className={cx('api-endpoint-icon')} /> : <ApiOutlined className={cx('api-endpoint-icon')} />}
                      <span className={cx('api-copy')}>
                        <strong>{endpoint.name || displayPath}</strong>
                      </span>
                      <DevelopmentStatusDot
                        statusLabel={endpointDevelopmentStatusLabel(developmentArtifacts, contract.id, endpoint)}
                        progress={endpointDevelopmentProgress(developmentArtifacts, contract.id, endpoint)}
                      />
                      <IterationBadge
                        artifactBranch={
                          endpointDevelopmentProgress(developmentArtifacts, contract.id, endpoint)
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
