// 武汉分行需求回检系统 · 规划产物（单模块：需求回检）。
import type { DevelopmentPlanningApiContract } from '../../src/renderer/src/typings'

export const PMS_PAGES = [
  { pageId: 'my-rechecks', key: 'my-rechecks', label: '我的回检', path: '/my-rechecks', purpose: '回检填报人提交需求回检单，跟踪回检状态', designed: true, hasDetailPlan: true, detailPlanStatus: 'confirmed' }
]

export const PMS_PAGE_TREE = [
  {
    key: 'rechecks', type: 'menu', label: '需求回检',
    children: [{ key: 'my-rechecks', type: 'page', label: '我的回检', path: '/my-rechecks', pageId: 'my-rechecks', designed: true, hasDetailPlan: true, detailPlanStatus: 'confirmed' }]
  }
]

export const PMS_API_CONTRACTS: DevelopmentPlanningApiContract[] = [
  {
    id: 'rechecks', label: '需求回检', dataSourceIds: [],
    endpoints: [
      { id: 'ep-my-rechecks', method: 'GET', path: '/api/rechecks/my', summary: '我的回检单分页查询', designed: true, hasDetailPlan: true }
    ]
  }
]

export const mockPlanningArtifacts = {
  ready: true,
  hasPageDesigns: true,
  missing: [],
  invalid: [],
  pages: PMS_PAGES,
  pageTree: PMS_PAGE_TREE,
  apiContracts: PMS_API_CONTRACTS
}
