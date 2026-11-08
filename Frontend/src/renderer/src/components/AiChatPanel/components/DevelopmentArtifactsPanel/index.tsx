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
import ArtifactDocument from './ArtifactDocument'
import PageArtifactOverview from './PageArtifactOverview'

type Props = ApplicationOutlineProps & {
  theme: 'light' | 'dark'
  useBuiltinDatabase?: boolean
  initialEntityTab?: 'fields' | 'database'
  onBackToFieldMapping?: () => void
  onEndpointRecovery?: EndpointRecoveryReporter
  detailLabel?: string
  apiTarget?: { apiContractId: string; endpointId: string }
  workspaceRoot?: string
  apiDesignRefreshKey?: number
  pageArtifactRefreshKey?: string
  initialUiDesignPageId?: string
  onPreviewArtifactPage?: (path: string) => void
  onShowPageDesign: (pageId: string) => void
  onShowArtifactFiles: () => void
  onConfigureApi?: (target: { apiContractId: string; endpointId: string; label?: string }) => void
}

/** 并排展示产物菜单与接口、实体详情，菜单选择不改变开发工作流。 */
export default function DevelopmentArtifactsPanel({
  theme,
  useBuiltinDatabase,
  initialEntityTab,
  onBackToFieldMapping,
  detailLabel,
  apiTarget,
  workspaceRoot,
  apiDesignRefreshKey,
  pageArtifactRefreshKey,
  initialUiDesignPageId,
  onPreviewArtifactPage,
  onShowPageDesign,
  onShowArtifactFiles,
  onConfigureApi,
  onEndpointRecovery,
  ...outlineProps
}: Props): ReactElement {
  const selectedEntity = outlineProps.entities.find(
    (entity) => entity.id === outlineProps.selectedEntityId
  )
  const selectedPage = outlineProps.pages.find((page) => page.pageId === outlineProps.selectedPageId)
  const { detail, error, loading, reload } = useEndpointDesignDetail(
    workspaceRoot,
    apiTarget,
    apiDesignRefreshKey,
    onEndpointRecovery
  )
  return (
    <div className={cx('development-artifacts-panel')}>
      <aside aria-label="应用产物菜单" className={cx('development-artifacts-menu')}>
        <ApplicationOutline {...outlineProps} />
      </aside>
      <section
        aria-label={detailLabel ? `${detailLabel}详情` : '应用产物详情'}
        className={cx('development-artifacts-detail')}
      >
        {onBackToFieldMapping && <Button className={cx('development-artifacts-return')} onClick={onBackToFieldMapping}>返回字段映射</Button>}
        {apiTarget && onConfigureApi ? (
          <Button onClick={() => onConfigureApi({ ...apiTarget, label: detailLabel })}>
            打开字段映射
          </Button>
        ) : null}
        {outlineProps.selectedDocumentKey && workspaceRoot ? (
          <ArtifactDocument
            key={`${workspaceRoot}:${outlineProps.selectedDocumentKey}`}
            workspaceRoot={workspaceRoot}
            documentKey={outlineProps.selectedDocumentKey}
            catalogLoading={outlineProps.planningCatalogStatus?.loading}
            initialUiDesignPageId={initialUiDesignPageId}
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
        ) : selectedPage && workspaceRoot ? (
          <PageArtifactOverview
            key={`${workspaceRoot}:${selectedPage.pageId}`}
            workspaceRoot={workspaceRoot}
            pageId={selectedPage.pageId}
            refreshKey={pageArtifactRefreshKey}
            catalogLoading={outlineProps.planningCatalogStatus?.loading}
            onPreviewPage={onPreviewArtifactPage}
            onShowDesign={onShowPageDesign}
            onShowFiles={onShowArtifactFiles}
            onEndpointSelect={(endpoint) => outlineProps.onApiEndpointSelect({
              apiContractId: endpoint.apiContractId,
              endpointId: endpoint.endpointId,
              endpointKey: `${endpoint.apiContractId}:${endpoint.endpointId}`,
              label: endpoint.name
            })}
          />
        ) : selectedEntity ? (
          <EntityInfoPanel
            entity={selectedEntity}
            theme={theme}
            workspaceRoot={workspaceRoot}
            useBuiltinDatabase={useBuiltinDatabase}
            initialTab={initialEntityTab}
          />
        ) : (
          <div aria-atomic="true" className={cx('development-artifacts-placeholder')} role="status">
            <h3>{detailLabel || '请选择应用产物'}</h3>
            <p>{detailLabel ? '详情内容待设计' : '点击左侧菜单查看详情'}</p>
          </div>
        )}
      </section>
    </div>
  )
}
