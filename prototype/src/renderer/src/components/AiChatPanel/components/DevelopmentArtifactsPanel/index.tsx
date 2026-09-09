import { useMemo, useState, type ReactElement } from 'react'
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import type { WorkbenchArtifactStatus } from '../../../../workbenchDomain'
import { cx } from '../../../../utils'
import { DevelopmentArtifactTree } from '../SessionSidebar/DevelopmentArtifactTree'
import './DevelopmentArtifactsPanel.less'

export type DevelopmentArtifactItem = {
  groupId?: string
  groupLabel?: string
  id: string
  kind: 'endpoint' | 'entity' | 'page'
  label: string
  path: string
  status: WorkbenchArtifactStatus
}

type Props = {
  activeId?: string
  apiContracts: DevelopmentPlanningApiContract[]
  application: ApplicationConfig
  entities: DevelopmentPlanningEntity[]
  items: DevelopmentArtifactItem[]
  onSelect: (item: DevelopmentArtifactItem) => void
  pagePreviewUrl: string
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
}

/** 将领域状态转换为接口内容区使用的中文文案；实现阶段状态由后台任务流水推导，用词与任务抽屉一致。 */
function statusLabel(status: WorkbenchArtifactStatus): string {
  if (status === 'completed') return '已完成'
  if (status === 'awaiting-review') return '待继续'
  if (status === 'implementing') return '执行中'
  if (status === 'impl-queued') return '排队中'
  if (status === 'failed') return '失败'
  if (status === 'in-progress') return '进行中'
  return '未开始'
}

/** 渲染精简的接口调试内容，保持原型可交互但不连接真实后端。 */
function EndpointDebugContent({ item }: { item: DevelopmentArtifactItem }): ReactElement {
  const [requestBody, setRequestBody] = useState(`{
  "page": 1,
  "pageSize": 20
}`)
  const [responseBody, setResponseBody] = useState('')
  const method = item.label.split(' ')[0] || 'GET'

  /** 用固定演示结果模拟请求响应，供接口产物内容区快速演示。 */
  const handleSend = (): void => {
    setResponseBody(
      JSON.stringify(
        {
          code: 200,
          data: { items: [], page: 1, pageSize: 20, total: 0 },
          message: 'success'
        },
        null,
        2
      )
    )
  }

  return (
    <section aria-label="接口调试" className={cx('development-artifact-endpoint-content')}>
      <header className={cx('development-artifact-content-header')}>
        <div>
          <span className={cx('development-artifact-method', method.toLowerCase())}>{method}</span>
          <strong>{item.path}</strong>
          <small>{item.label}</small>
        </div>
        <span className={cx('development-artifact-content-status')}>{statusLabel(item.status)}</span>
      </header>
      <div className={cx('development-artifact-request-bar')}>
        <span className={cx('development-artifact-request-method')}>{method}</span>
        <code>{item.path}</code>
        <button type="button" onClick={handleSend}>发送</button>
      </div>
      <div className={cx('development-artifact-debug-grid')}>
        <label>
          <span>请求参数</span>
          <textarea aria-label="请求参数" onChange={(event) => setRequestBody(event.target.value)} value={requestBody} />
        </label>
        <label>
          <span>响应结果</span>
          <pre aria-label="响应结果">{responseBody || '点击发送后查看响应结果'}</pre>
        </label>
      </div>
    </section>
  )
}

/** 直接嵌入原型随 Vite 启动的 5190 预览应用，避免浏览器工作台的工具栏和加载遮罩。 */
function PageArtifactPreview({
  item,
  previewUrl
}: {
  item: DevelopmentArtifactItem
  previewUrl: string
}): ReactElement {
  const [loaded, setLoaded] = useState(false)
  const fallbackUrl = `http://127.0.0.1:5190${item.path}`
  const source = previewUrl || fallbackUrl
  return (
    <section aria-label={`${item.label}页面预览`} className={cx('development-artifact-page-preview')}>
      {!loaded ? <div className={cx('development-artifact-page-preview-loading')}>正在加载页面预览…</div> : null}
      <iframe
        className={cx('development-artifact-page-preview-frame')}
        onLoad={() => setLoaded(true)}
        sandbox="allow-forms allow-modals allow-popups allow-same-origin allow-scripts"
        src={source}
        title={`${item.label}页面预览`}
      />
    </section>
  )
}

/** 页面产物是否已可预览：后台实现启动预览后（待验收）与验收完成（已完成）均可打开。 */
function pagePreviewReady(status: WorkbenchArtifactStatus): boolean {
  return status === 'completed' || status === 'awaiting-review'
}

/** 按当前产物类型呈现页面预览、接口调试或实体占位内容。 */
function DevelopmentArtifactContent({
  activeItem,
  pagePreviewUrl
}: {
  activeItem?: DevelopmentArtifactItem
  pagePreviewUrl: string
}): ReactElement {
  if (!activeItem) {
    return <div className={cx('development-artifact-content-empty')}>从左侧目录选择一个开发产物</div>
  }
  if (activeItem.kind === 'endpoint') return <EndpointDebugContent item={activeItem} />
  if (activeItem.kind === 'entity') {
    return <div className={cx('development-artifact-content-empty')}>实体设计敬请期待</div>
  }
  if (activeItem.status === 'failed') {
    return (
      <div className={cx('development-artifact-content-empty')}>
        <strong>{activeItem.label}</strong>
        <span>后台实现任务失败，可在「后台任务」中查看详情并重试。</span>
      </div>
    )
  }
  if (!pagePreviewReady(activeItem.status)) {
    return (
      <div className={cx('development-artifact-content-empty')}>
        <strong>{activeItem.label}</strong>
        <span>
          {activeItem.status === 'not-started'
            ? '开始详细设计并派发后台实现任务后，这里会显示最终页面效果。'
            : '后台正在实现当前页面，任务进入待继续后即可在这里预览。'}
        </span>
      </div>
    )
  }
  return <PageArtifactPreview item={activeItem} previewUrl={pagePreviewUrl} />
}

/** 承载旧版产物目录和当前产物内容，复用“应用文件”的右目录布局。 */
export default function DevelopmentArtifactsPanel({
  activeId,
  apiContracts,
  application,
  entities,
  items,
  onSelect,
  pagePreviewUrl,
  pages,
  pageTree
}: Props): ReactElement {
  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items])
  const artifactStatusById = useMemo(
    () => Object.fromEntries(items.map((item) => [item.id, item.status])),
    [items]
  )
  const activeItem = activeId ? itemById.get(activeId) : undefined

  /** 将旧产物树的页面选择转换为开发产物内容区的当前项。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    const item = itemById.get(`page:${page.pageId}`)
    if (item) onSelect(item)
  }

  /** 将旧产物树的接口选择转换为开发产物内容区的当前项。 */
  const handleEndpointSelect = (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }): void => {
    const item = itemById.get(`endpoint:${target.apiContractId}:${target.endpointId}`)
    if (item) onSelect(item)
  }

  return (
    <section aria-label="开发产物" className={cx('development-artifacts-panel')}>
      <div className={cx('development-artifacts-workspace')}>
        <aside aria-label="开发产物目录" className={cx('development-artifacts-directory')}>
          <div className={cx('development-artifacts-body')}>
            <DevelopmentArtifactTree
              apiContracts={apiContracts}
              applicationName={application.name}
              artifactStatusById={artifactStatusById}
              entities={entities}
              onApiEndpointSelect={handleEndpointSelect}
              onPageSelect={handlePageSelect}
              pages={pages}
              pageTree={pageTree}
              selectedApiEndpointKey={
                activeItem?.kind === 'endpoint' ? activeItem.id.replace(/^endpoint:/, '') : ''
              }
              selectedPageId={
                activeItem?.kind === 'page' ? activeItem.id.replace(/^page:/, '') : ''
              }
            />
          </div>
        </aside>
        <main className={cx('development-artifacts-content')}>
          <DevelopmentArtifactContent
            activeItem={activeItem}
            pagePreviewUrl={pagePreviewUrl}
          />
        </main>
      </div>
    </section>
  )
}
