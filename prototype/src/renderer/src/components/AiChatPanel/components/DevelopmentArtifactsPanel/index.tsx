import { useMemo, useState, type ReactElement } from 'react'
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import AppApiDevelopmentPanel from '../../../AppApis/AppApiDevelopmentPanel'
import RichLoading from '../DesignProgress/RichLoading'
import { useAppApis } from '../../../AppApis/store'
import type { WorkbenchArtifactStatus } from '../../../../workbenchDomain'
import { cx } from '../../../../utils'
import { DevelopmentArtifactTree } from '../SessionSidebar/DevelopmentArtifactTree'
import './DevelopmentArtifactsPanel.less'

export type DevelopmentArtifactItem = {
  groupId?: string
  groupLabel?: string
  id: string
  kind: 'app-api' | 'page'
  label: string
  path: string
  status: WorkbenchArtifactStatus
}

type Props = {
  activeId?: string
  apiContracts: DevelopmentPlanningApiContract[]
  application: ApplicationConfig
  requirementSpec: Record<string, unknown>
  /** 技术规划方案：为接口提供计划阶段确定的数据实现意向。 */
  technicalPlan: Record<string, unknown>
  items: DevelopmentArtifactItem[]
  onSelect: (item: DevelopmentArtifactItem) => void
  pagePreviewUrl: string
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  /** 当前选中的应用API适配生成进行中（来自开发工作流状态）。 */
  appApiGenerating?: boolean
  /** 直连绑定入口：从产物详情直接进入字段映射工作台；只读版本不提供。 */
  onOpenFieldMapping?: (objectId: string) => void
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

/** 应用页面产物是否已可预览：后台实现启动预览后（待验收）与验收完成（已完成）均可打开。 */
function pagePreviewReady(status: WorkbenchArtifactStatus): boolean {
  return status === 'completed' || status === 'awaiting-review'
}

/** 按当前产物类型呈现页面预览或应用API的数据绑定工作台。 */
function DevelopmentArtifactContent({
  activeItem,
  appApiGenerating = false,
  onOpenFieldMapping,
  pagePreviewUrl,
  requirementSpec,
  technicalPlan,
  versionKey
}: {
  activeItem?: DevelopmentArtifactItem
  /** 当前选中的是应用API且其适配生成进行中。 */
  appApiGenerating?: boolean
  /** 直连绑定入口：未确认绑定的应用API可直接进入字段映射工作台。 */
  onOpenFieldMapping?: (objectId: string) => void
  pagePreviewUrl: string
  requirementSpec: Record<string, unknown>
  technicalPlan: Record<string, unknown>
  versionKey: string
}): ReactElement {
  if (activeItem?.kind === 'app-api') {
    return (
      <AppApiDevelopmentPanel
        objectId={activeItem.id.replace(/^app-api:/, '')}
        requirementSpec={requirementSpec}
        versionKey={versionKey}
        technicalPlan={technicalPlan}
        generating={appApiGenerating}
        onOpenFieldMapping={onOpenFieldMapping}
      />
    )
  }
  if (!activeItem) {
    return (
      <div className={cx('development-artifact-content-empty')}>从左侧目录选择一个开发产物</div>
    )
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
    if (activeItem.status === 'implementing' || activeItem.status === 'impl-queued') {
      // 与设计/计划阶段同一生成态视觉：页面在后台实现中，这里给出富加载页。
      return (
        <div className={cx('development-artifact-content-empty')}>
          <RichLoading
            title={`正在实现「${activeItem.label}」`}
            hint="后台任务完成后即可在这里预览与验收页面效果。"
          />
        </div>
      )
    }
    return (
      <div className={cx('development-artifact-content-empty')}>
        <strong>{activeItem.label}</strong>
        <span>开始详细设计并派发后台实现任务后，这里会显示最终页面效果。</span>
      </div>
    )
  }
  return <PageArtifactPreview item={activeItem} previewUrl={pagePreviewUrl} />
}

/** 承载旧版产物目录和当前产物内容，复用“应用文件”的右目录布局。 */
export default function DevelopmentArtifactsPanel({
  activeId,
  appApiGenerating = false,
  apiContracts,
  application,
  requirementSpec,
  technicalPlan,
  items,
  onSelect,
  onOpenFieldMapping,
  pagePreviewUrl,
  pages,
  pageTree,
}: Props): ReactElement {
  const itemById = useMemo(() => new Map(items.map((item) => [item.id, item])), [items])
  const artifactStatusById = useMemo(
    () => Object.fromEntries(items.map((item) => [item.id, item.status])),
    [items]
  )
  const activeItem = activeId ? itemById.get(activeId) : undefined
  // 应用API绑定按版本隔离：产物目录与应用API工作台都读当前工作版本自己的缓存键。
  const apiVersionKey = application.currentVersionId || 'current'
  const [appApis] = useAppApis(requirementSpec, apiVersionKey, technicalPlan)

  /** 将旧产物树的页面选择转换为开发产物内容区的当前项。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    const item = itemById.get(`page:${page.pageId}`)
    if (item) onSelect(item)
  }

  /** 应用API选择直接打开契约与数据绑定工作台。 */
  const handleAppApiSelect = (objectId: string): void => {
    const item = itemById.get(`app-api:${objectId}`)
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
              appApis={appApis}
              artifactStatusById={artifactStatusById}
              onApiEndpointSelect={() => undefined}
              onAppApiSelect={handleAppApiSelect}
              onPageSelect={handlePageSelect}
              pages={pages}
              pageTree={pageTree}
              selectedApiEndpointKey=""
              selectedAppApiId={
                activeItem?.kind === 'app-api'
                  ? activeItem.id.replace(/^app-api:/, '')
                  : ''
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
            appApiGenerating={appApiGenerating}
            onOpenFieldMapping={onOpenFieldMapping}
            pagePreviewUrl={pagePreviewUrl}
            requirementSpec={requirementSpec}
            technicalPlan={technicalPlan}
            versionKey={apiVersionKey}
          />
        </main>
      </div>
    </section>
  )
}
