export type PageTemplateCategory = 'business' | 'agent'

export type PageSurfaceType = 'standard_page' | 'floating_panel' | 'standalone_page'

export interface AgentUiTemplateManifestEvidence {
  module: '@xcodeagent/agent-ui-design'
  component: 'AgentConversationTemplate'
  version: 'agent-ui.v1'
}

export interface CompatibleTemplateManifest {
  category: PageTemplateCategory
  supportedSurfaces: PageSurfaceType[]
  agentUi?: AgentUiTemplateManifestEvidence
}

type UiDesignPageLike = {
  bindings?: {
    agent_surfaces?: Array<{ type?: unknown }>
  }
}

const KNOWN_CATEGORIES = new Set<PageTemplateCategory>(['business', 'agent'])
const KNOWN_SURFACES = new Set<PageSurfaceType>([
  'standard_page',
  'floating_panel',
  'standalone_page'
])

/** 严格校验模板声明，只接受当前已知分类和 Surface。 */
export function isCompatibleTemplateManifest(
  manifest: Partial<CompatibleTemplateManifest>
): manifest is CompatibleTemplateManifest {
  const baseValid =
    KNOWN_CATEGORIES.has(manifest.category as PageTemplateCategory) &&
    Array.isArray(manifest.supportedSurfaces) &&
    manifest.supportedSurfaces.length > 0 &&
    manifest.supportedSurfaces.every(
      (surface) => typeof surface === 'string' && KNOWN_SURFACES.has(surface)
    )
  if (!baseValid) return false
  if (manifest.category !== 'agent') return manifest.agentUi === undefined
  return (
    manifest.agentUi?.module === '@xcodeagent/agent-ui-design' &&
    manifest.agentUi.component === 'AgentConversationTemplate' &&
    manifest.agentUi.version === 'agent-ui.v1'
  )
}

/** 从 UiManifest 当前页证据确定唯一模板 Surface，异常或多 Surface 时安全拒绝。 */
export function resolvePageSurface(page?: UiDesignPageLike): PageSurfaceType | undefined {
  const surfaces = page?.bindings?.agent_surfaces
  if (!Array.isArray(surfaces) || surfaces.length === 0) return 'standard_page'
  if (surfaces.length !== 1) return undefined
  const type = surfaces[0]?.type
  return typeof type === 'string' && KNOWN_SURFACES.has(type as PageSurfaceType)
    ? (type as PageSurfaceType)
    : undefined
}

/** 按服务端投影的页面 Surface 过滤模板，不对未知事实做宽松回退。 */
export function filterCompatibleTemplates<
  T extends { manifest: Partial<CompatibleTemplateManifest> }
>(templates: T[], surface: PageSurfaceType | undefined): T[] {
  if (!surface) return []
  return templates.filter(({ manifest }) => {
    if (!isCompatibleTemplateManifest(manifest)) return false
    const expectedCategory: PageTemplateCategory =
      surface === 'standalone_page' ? 'agent' : 'business'
    return manifest.category === expectedCategory && manifest.supportedSurfaces.includes(surface)
  })
}
