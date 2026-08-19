import { useEffect, useMemo, useState, type ReactElement } from 'react'
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import BusinessObjectDevelopmentPanel from '../../../BusinessObjects/BusinessObjectDevelopmentPanel'
import { useBusinessObjects } from '../../../BusinessObjects/store'
import type { WorkbenchArtifactStatus } from '../../../../workbenchDomain'
import type { DevelopmentPlanningAgent } from '../../../../agentDevelopment'
import { cx } from '../../../../utils'
import { DevelopmentArtifactTree } from '../SessionSidebar/DevelopmentArtifactTree'
import './DevelopmentArtifactsPanel.less'

export type DevelopmentArtifactItem = {
  groupId?: string
  groupLabel?: string
  id: string
  kind: 'agent' | 'business-object' | 'endpoint' | 'entity' | 'page'
  label: string
  path: string
  status: WorkbenchArtifactStatus
}

type Props = {
  activeId?: string
  agents: DevelopmentPlanningAgent[]
  apiContracts: DevelopmentPlanningApiContract[]
  application: ApplicationConfig
  requirementSpec: Record<string, unknown>
  entities: DevelopmentPlanningEntity[]
  items: DevelopmentArtifactItem[]
  onSelect: (item: DevelopmentArtifactItem) => void
  pagePreviewUrl: string
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
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
    <section
      aria-label={`${item.label}页面预览`}
      className={cx('development-artifact-page-preview')}
    >
      {!loaded ? (
        <div className={cx('development-artifact-page-preview-loading')}>正在加载页面预览…</div>
      ) : null}
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

/** 将领域状态转换为接口内容区使用的中文文案；实现阶段状态由后台任务流水推导。 */
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

/** 按当前产物类型呈现页面预览或实体的数据绑定工作台。 */
function DevelopmentArtifactContent({
  activeItem,
  addMethodTick,
  methodId,
  onMethodSelect,
  pagePreviewUrl,
  requirementSpec,
  versionKey
}: {
  activeItem?: DevelopmentArtifactItem
  addMethodTick: number
  methodId: string
  onMethodSelect: (methodId: string) => void
  pagePreviewUrl: string
  requirementSpec: Record<string, unknown>
  versionKey: string
}): ReactElement {
  if (activeItem?.kind === 'business-object') {
    return (
      <BusinessObjectDevelopmentPanel
        addMethodTick={addMethodTick}
        methodId={methodId}
        objectId={activeItem.id.replace(/^business-object:/, '')}
        onMethodSelect={onMethodSelect}
        requirementSpec={requirementSpec}
        versionKey={versionKey}
      />
    )
  }
  if (!activeItem) {
    return (
      <div className={cx('development-artifact-content-empty')}>从左侧目录选择一个开发产物</div>
    )
  }
  if (activeItem.kind === 'endpoint') return <EndpointDebugContent item={activeItem} />
  if (activeItem.kind === 'agent') {
    return (
      <div className={cx('development-artifact-content-empty')}>
        <strong>{activeItem.label}</strong>
        <span>在左侧对话中确认智能体详细设计，完成后可查看 Python Runtime 产物。</span>
      </div>
    )
  }
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
  agents,
  apiContracts,
  application,
  requirementSpec,
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
  const activeObjectId =
    activeItem?.kind === 'business-object' ? activeItem.id.replace(/^business-object:/, '') : ''
  // 实体绑定按版本隔离：产物目录与实体工作台都读当前工作版本自己的缓存键。
  const entityVersionKey = application.currentVersionId || 'current'
  const [businessObjects] = useBusinessObjects(requirementSpec, entityVersionKey)
  // 方法级选择留在产物面板内部：选中方法右侧打开其定义，选中对象则回到字段维护。
  const [selectedMethodId, setSelectedMethodId] = useState('')
  // 「新增方法」入口在目录树里，用自增计数把打开编辑弹窗的信号传给对象面板。
  const [addMethodTick, setAddMethodTick] = useState(0)

  // 只在切换到另一个实体（或离开实体）时退出方法定义视图；
  // 依赖对象 id 而非 activeId，避免同一对象内点方法时被误清空。
  useEffect(() => {
    setSelectedMethodId('')
  }, [activeObjectId])

  /** 将旧产物树的页面选择转换为开发产物内容区的当前项。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    const item = itemById.get(`page:${page.pageId}`)
    if (item) onSelect(item)
  }

  /** 实体选择直接打开字段维护，并清掉方法级选择。 */
  const handleBusinessObjectSelect = (objectId: string): void => {
    setSelectedMethodId('')
    const item = itemById.get(`business-object:${objectId}`)
    if (item) onSelect(item)
  }

  /** 方法选择：对象尚未激活时先激活它；已激活则不动产物级选中，只切换方法定义。 */
  const handleBusinessObjectMethodSelect = (objectId: string, methodId: string): void => {
    const item = itemById.get(`business-object:${objectId}`)
    if (item && activeId !== item.id) onSelect(item)
    setSelectedMethodId(methodId)
  }

  /** 目录树「新增方法」：先确保对象被选中（右侧面板挂载），再发打开编辑弹窗的信号。 */
  const handleBusinessObjectAddMethod = (objectId: string): void => {
    const item = itemById.get(`business-object:${objectId}`)
    if (item && activeId !== item.id) onSelect(item)
    setAddMethodTick((tick) => tick + 1)
  }

  return (
    <section aria-label="开发产物" className={cx('development-artifacts-panel')}>
      <div className={cx('development-artifacts-workspace')}>
        <aside aria-label="开发产物目录" className={cx('development-artifacts-directory')}>
          <div className={cx('development-artifacts-body')}>
            <DevelopmentArtifactTree
              apiContracts={apiContracts}
              applicationName={application.name}
              businessObjects={businessObjects}
              artifactStatusById={artifactStatusById}
              entities={entities}
              onApiEndpointSelect={() => undefined}
              onBusinessObjectSelect={handleBusinessObjectSelect}
              onBusinessObjectMethodSelect={handleBusinessObjectMethodSelect}
              onBusinessObjectAddMethod={handleBusinessObjectAddMethod}
              onPageSelect={handlePageSelect}
              pages={pages}
              pageTree={pageTree}
              selectedApiEndpointKey=""
              selectedBusinessObjectId={
                activeItem?.kind === 'business-object'
                  ? activeItem.id.replace(/^business-object:/, '')
                  : ''
              }
              selectedMethodId={selectedMethodId}
              selectedPageId={
                activeItem?.kind === 'page' ? activeItem.id.replace(/^page:/, '') : ''
              }
            />
            {agents.length > 0 ? (
              <div className={cx('development-artifact-agent-list')}>
                <strong>智能体</strong>
                {agents.map((agent) => {
                  const item = itemById.get(`agent:${agent.id}`)
                  if (!item) return null
                  return (
                    <button
                      className={cx(
                        'development-artifact-agent-item',
                        activeItem?.id === item.id && 'active'
                      )}
                      key={agent.id}
                      onClick={() => onSelect(item)}
                      type="button"
                    >
                      <span>{agent.label}</span>
                      <small>{statusLabel(item.status)}</small>
                    </button>
                  )
                })}
              </div>
            ) : null}
          </div>
        </aside>
        <main className={cx('development-artifacts-content')}>
          <DevelopmentArtifactContent
            activeItem={activeItem}
            addMethodTick={addMethodTick}
            methodId={selectedMethodId}
            onMethodSelect={setSelectedMethodId}
            pagePreviewUrl={pagePreviewUrl}
            requirementSpec={requirementSpec}
            versionKey={entityVersionKey}
          />
        </main>
      </div>
    </section>
  )
}
