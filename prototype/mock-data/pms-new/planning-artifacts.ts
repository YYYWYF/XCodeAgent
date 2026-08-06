// 武汉分行项目管理系统 · 规划产物（设计镜像：全部待设计）。
import type { DevelopmentPlanningApiContract } from '../../src/renderer/src/typings'

export const PMS_PAGES = [
  { pageId: 'my-projects', key: 'my-projects', label: '我的项目', path: '/my-projects', purpose: '项目经理查看本人负责的项目列表、状态与进度，进入项目详情', designed: false, hasDetailPlan: false, detailPlanStatus: 'pending' },
  { pageId: 'my-rechecks', key: 'my-rechecks', label: '我的回检', path: '/my-rechecks', purpose: '回检填报人提交需求回检单，跟踪回检状态', designed: false, hasDetailPlan: false, detailPlanStatus: 'pending' },
  { pageId: 'recheck-review', key: 'recheck-review', label: '回检审核', path: '/recheck-review', purpose: '回检审核人对待审核回检单进行通过/驳回处理', designed: false, hasDetailPlan: false, detailPlanStatus: 'pending' }
]

export const PMS_PAGE_TREE = [
  {
    key: 'projects', type: 'menu', label: '项目管理',
    children: [{ key: 'my-projects', type: 'page', label: '我的项目', path: '/my-projects', pageId: 'my-projects', designed: false }]
  },
  {
    key: 'rechecks', type: 'menu', label: '需求回检',
    children: [
      { key: 'my-rechecks', type: 'page', label: '我的回检', path: '/my-rechecks', pageId: 'my-rechecks', designed: false },
      { key: 'recheck-review', type: 'page', label: '回检审核', path: '/recheck-review', pageId: 'recheck-review', designed: false }
    ]
  }
]

export const PMS_API_CONTRACTS: DevelopmentPlanningApiContract[] = [
  {
    id: 'projects', label: '项目管理', dataSourceIds: [],
    endpoints: [
      { id: 'ep-my-projects', method: 'GET', path: '/api/projects/my', summary: '我的项目分页查询（按状态/关键字筛选）' },
      { id: 'ep-project-create', method: 'POST', path: '/api/projects', summary: '创建项目（含基础信息与负责人）' }
    ]
  },
  {
    id: 'rechecks', label: '需求回检', dataSourceIds: [],
    endpoints: [
      { id: 'ep-my-rechecks', method: 'GET', path: '/api/rechecks/my', summary: '我的回检单分页查询' },
      { id: 'ep-recheck-create', method: 'POST', path: '/api/rechecks', summary: '提交需求回检单' },
      { id: 'ep-recheck-pending', method: 'GET', path: '/api/rechecks/pending', summary: '待审核回检单列表' },
      { id: 'ep-recheck-review', method: 'PUT', path: '/api/rechecks/{id}/review', summary: '审核回检单（通过/驳回）' }
    ]
  }
]

export const mockPlanningArtifacts = {
  ready: true,
  hasPageDesigns: false,
  missing: [],
  invalid: [],
  pages: PMS_PAGES,
  pageTree: PMS_PAGE_TREE,
  apiContracts: PMS_API_CONTRACTS
}
