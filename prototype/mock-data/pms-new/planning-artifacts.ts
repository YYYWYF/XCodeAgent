// 武汉分行需求回检系统 · 规划产物（两个页面、一个接口、两个开发任务）。
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity
} from '../../src/renderer/src/typings'
import type { DevelopmentPlanningAgent } from '../../src/renderer/src/agentDevelopment'

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
export const PMS_AGENTS: DevelopmentPlanningAgent[] = [
  {
    id: 'recheck-assistant',
    label: '回检填报助手',
    purpose: '解释当前用户的回检状态，并基于可见回检单给出下一步操作建议',
    model: '项目默认模型',
    apiDependencies: ['GET /api/rechecks/my'],
    pageIds: ['my-rechecks'],
    tools: ['查询我的回检单'],
    permissions: ['只读当前用户可见数据', '禁止代替用户提交或修改回检单'],
    acceptanceCriteria: [
      '回答包含工具调用摘要和数据来源说明',
      '只能读取当前用户的回检单',
      '工具失败时展示可重试错误，不编造业务数据'
    ],
    designed: true,
    hasDetailPlan: true,
    detailPlanStatus: 'confirmed'
  }
]

export const mockPlanningArtifacts = {
  ready: true,
  hasPageDesigns: true,
  missing: [],
  invalid: [],
  pages: PMS_PAGES,
  pageTree: PMS_PAGE_TREE,
  apiContracts: PMS_API_CONTRACTS,
  entities: PMS_ENTITIES,
  agents: PMS_AGENTS
}
