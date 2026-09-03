import type { ApplicationMenuItem, DevelopmentPlanningPageTreeNode } from '../../../../typings'

/** 把页面树节点递归转换为大纲复用的菜单项结构。 */
export function pageTreeItems(nodes: DevelopmentPlanningPageTreeNode[]): ApplicationMenuItem[] {
  const items: ApplicationMenuItem[] = []
  nodes.forEach((node) => {
    if (node.type === 'menu') {
      const children = pageTreeItems(node.children || [])
      if (children.length === 0) return
      items.push({
        key: node.key,
        path: node.uniquePath || node.path || node.key,
        label: node.label,
        type: 'menu',
        children
      })
      return
    }
    const pageKey = node.pageId || node.key
    if (!pageKey) return
    items.push({
      key: pageKey,
      pageKey,
      path: node.path,
      label: node.label,
      type: 'page',
      purpose: node.purpose,
      keyFeatures: [],
      designed: Boolean(node.designed),
      detailPlanStatus: node.detailPlanStatus,
      hasDetailPlan: node.hasDetailPlan
    })
  })
  return items
}

/** 根据搜索词收集应展示的页面与其父级目录键。 */
export function collectVisibleKeys(items: ApplicationMenuItem[], query: string): Set<string> {
  const visible = new Set<string>()
  const normalizedQuery = query.trim().toLocaleLowerCase()

  /** 递归判断当前节点或任一后代是否命中搜索。 */
  const visit = (item: ApplicationMenuItem): boolean => {
    let childMatches = false
    item.children?.forEach((child) => {
      if (visit(child)) childMatches = true
    })
    const selfMatches = !normalizedQuery || item.label.toLocaleLowerCase().includes(normalizedQuery)
    if (selfMatches || childMatches) visible.add(item.key)
    return selfMatches || childMatches
  }

  items.forEach(visit)
  return visible
}

/** 收集当前页面的祖先与后代键，用于“只显示相关”筛选。 */
export function collectRelatedKeys(items: ApplicationMenuItem[], selectedKey: string): Set<string> {
  const related = new Set<string>()
  /** 递归加入当前节点的全部后代。 */
  const addDescendants = (item: ApplicationMenuItem): void => {
    related.add(item.key)
    item.children?.forEach(addDescendants)
  }
  /** 递归定位选中节点并保留其祖先链。 */
  const visit = (item: ApplicationMenuItem, ancestors: string[]): boolean => {
    if (item.key === selectedKey) {
      ancestors.forEach((key) => related.add(key))
      addDescendants(item)
      return true
    }
    return item.children?.some((child) => visit(child, [...ancestors, item.key])) || false
  }

  if (!selectedKey) items.forEach(addDescendants)
  else items.some((item) => visit(item, []))
  return related
}

/** 判断页面树中是否存在指定键。 */
export function containsMenuKey(items: ApplicationMenuItem[], key: string): boolean {
  return items.some((item) => item.key === key || containsMenuKey(item.children || [], key))
}

/** 生成 API endpoint 在应用大纲中的稳定选中键。 */
export function apiEndpointSelectionKey(contractId: string, endpointId: string): string {
  return `${contractId}:${endpointId}`
}
