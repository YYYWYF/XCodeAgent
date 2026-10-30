import { Button } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import ApplicationOutline from '../ApplicationOutline'
import type { ApplicationOutlineProps } from '../ApplicationOutline'
import EndpointDesignResult from '../EndpointDesignResult'
import EntityInfoPanel from '../EntityInfoPanel'
import { useEndpointDesignDetail } from '../../hooks/useEndpointDesignDetail'
import type { EndpointRecoveryReporter } from '../../hooks/useEndpointDesignRecovery'
import './DevelopmentArtifactsPanel.less'

type Props = ApplicationOutlineProps & {
  theme: 'light' | 'dark'
  useBuiltinDatabase?: boolean
  onEndpointRecovery?: EndpointRecoveryReporter
  detailLabel?: string
  apiTarget?: { apiContractId: string; endpointId: string }
  workspaceRoot?: string
  apiDesignRefreshKey?: number
  onConfigureApi?: (target: { apiContractId: string; endpointId: string; label?: string }) => void
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
  ...outlineProps
}: Props): ReactElement {
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
        <ApplicationOutline {...outlineProps} />
      </aside>
      <section
        aria-label={detailLabel ? `${detailLabel}详情` : '开发产物详情'}
        className={cx('development-artifacts-detail')}
      >
        {apiTarget && onConfigureApi ? (
          <Button onClick={() => onConfigureApi({ ...apiTarget, label: detailLabel })}>
            打开字段映射
          </Button>
        ) : null}
        {apiTarget ? (
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
