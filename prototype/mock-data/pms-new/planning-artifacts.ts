// 武汉分行需求回检系统 · 规划产物（两个页面、一个接口、两个开发任务）。
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity
} from '../../src/renderer/src/typings'

export const PMS_PAGES = [
  {
    pageId: 'recheck-introduction',
    key: 'recheck-introduction',
    label: '回检介绍',
    path: '/recheck-introduction',
    purpose: '介绍需求回检的适用场景、流程和使用说明',
    designed: true,
    hasDetailPlan: true,
    detailPlanStatus: 'confirmed'
  },
  {
    pageId: 'my-rechecks',
    key: 'my-rechecks',
    label: '我的回检',
    path: '/my-rechecks',
    purpose: '回检填报人提交需求回检单，跟踪回检状态',
    designed: true,
    hasDetailPlan: true,
    detailPlanStatus: 'confirmed'
  }
]

export const PMS_PAGE_TREE = [
  {
    key: 'rechecks',
    type: 'menu',
    label: '需求回检',
    children: [
      {
        key: 'recheck-introduction',
        type: 'page',
        label: '回检介绍',
        path: '/recheck-introduction',
        pageId: 'recheck-introduction',
        designed: true,
        hasDetailPlan: true,
        detailPlanStatus: 'confirmed'
      },
      {
        key: 'my-rechecks',
        type: 'page',
        label: '我的回检',
        path: '/my-rechecks',
        pageId: 'my-rechecks',
        designed: true,
        hasDetailPlan: true,
        detailPlanStatus: 'confirmed'
      }
    ]
  }
]

export const PMS_API_CONTRACTS: DevelopmentPlanningApiContract[] = [
  {
    id: 'rechecks',
    label: '需求回检',
    dataSourceIds: [],
    endpoints: [
      {
        id: 'ep-my-rechecks',
        method: 'GET',
        path: '/api/rechecks/my',
        summary: '我的回检单分页查询',
        designed: true,
        hasDetailPlan: true
      }
    ]
  }
]

/** 当前只保留实体概念提示，不生成具体实体产物或实体工作流。 */
export const PMS_ENTITIES: DevelopmentPlanningEntity[] = []

export const mockPlanningArtifacts = {
  ready: true,
  hasPageDesigns: true,
  missing: [],
  invalid: [],
  pages: PMS_PAGES,
  pageTree: PMS_PAGE_TREE,
  apiContracts: PMS_API_CONTRACTS,
  entities: PMS_ENTITIES
}
