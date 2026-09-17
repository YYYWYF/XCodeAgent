import type { DevelopmentPlanningPageTreeNode } from '../../typings'
import type { AppApi } from './model'

/**
 * 应用API的产物树分组：与应用页面共用业务模块（页面菜单）作为一级分组，
 * 让“接口即产物”的目录与页面目录保持同一套业务视角。
 */

/** 一个应用API分组：对齐页面菜单节点的一个业务模块。 */
export type AppApiGroup = {
  key: string
  label: string
  apis: AppApi[]
}

/** 递归收集菜单节点下全部页面标识（pageId 与页面名），供接口按调用页面归组。 */
function collectPageKeys(node: DevelopmentPlanningPageTreeNode, keys: Set<string>): void {
  if (node.type === 'page') {
    if (node.pageId) keys.add(node.pageId)
    if (node.label) keys.add(node.label)
    return
  }
  ;(node.children || []).forEach((child) => collectPageKeys(child, keys))
}

/**
 * 把应用API按页面菜单分组：接口经由“调用页面”落到对应业务模块；
 * 没有可识别调用页面的接口统一落到末尾的“未分组”，保证全部接口仍然可见。
 */
export function groupAppApis(
  appApis: AppApi[],
  pageTree: DevelopmentPlanningPageTreeNode[]
): AppApiGroup[] {
  const pageKeysByMenu = pageTree
    .filter((node) => node.type === 'menu')
    .map((menu) => {
      const keys = new Set<string>()
      collectPageKeys(menu, keys)
      return { key: menu.key, label: menu.label, keys }
    })
  const groups = new Map<string, AppApiGroup>()
  const fallback: AppApiGroup = { key: 'ungrouped', label: '未分组', apis: [] }
  appApis.forEach((object) => {
    const menu = pageKeysByMenu.find((item) => object.pages.some((page) => item.keys.has(page)))
    if (menu) {
      const group = groups.get(menu.key) || { key: menu.key, label: menu.label, apis: [] }
      group.apis.push(object)
      groups.set(menu.key, group)
      return
    }
    fallback.apis.push(object)
  })
  const ordered = pageKeysByMenu
    .map((menu) => groups.get(menu.key))
    .filter((group): group is AppApiGroup => Boolean(group))
  if (fallback.apis.length) ordered.push(fallback)
  return ordered
}
