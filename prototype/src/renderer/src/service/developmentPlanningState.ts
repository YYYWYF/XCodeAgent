import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../typings'

/** 把页面规划选项重置为新迭代的待设计状态。 */
export function resetDevelopmentPlanningPages(
  pages: DevelopmentPlanningPageOption[]
): DevelopmentPlanningPageOption[] {
  return pages.map((page) => ({
    ...page,
    designed: false,
    hasDetailPlan: false,
    detailPlanStatus: 'pending'
  }))
}

/** 递归把页面规划树重置为新迭代的待设计状态，保留菜单层级。 */
export function resetDevelopmentPlanningPageTree(
  tree: DevelopmentPlanningPageTreeNode[]
): DevelopmentPlanningPageTreeNode[] {
  const resetNode = (node: DevelopmentPlanningPageTreeNode): DevelopmentPlanningPageTreeNode => ({
    ...node,
    ...(node.children?.length
      ? { children: node.children.map(resetNode) }
      : { designed: false, hasDetailPlan: false, detailPlanStatus: 'pending' })
  })
  return tree.map(resetNode)
}

/** 把接口规划契约重置为新迭代的待设计状态，保留契约和端点标识。 */
export function resetDevelopmentPlanningApiContracts(
  contracts: DevelopmentPlanningApiContract[]
): DevelopmentPlanningApiContract[] {
  return contracts.map((contract) => ({
    ...contract,
    endpoints: contract.endpoints.map((endpoint) => ({
      ...endpoint,
      designed: false,
      hasDetailPlan: false
    }))
  }))
}
