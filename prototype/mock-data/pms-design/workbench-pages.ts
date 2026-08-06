// 武汉分行项目管理系统 · 页面 label/path/purpose 表（workbench 剧本按 pageId 取）。
export const WORKBENCH_PAGES: Record<string, { label: string; path: string; purpose: string }> = {
  'my-projects': {
    label: '我的项目',
    path: '/my-projects',
    purpose: '项目经理查看本人负责的项目列表、状态与进度，进入项目详情'
  },
  'my-rechecks': {
    label: '我的回检',
    path: '/my-rechecks',
    purpose: '回检填报人提交需求回检单，跟踪回检状态'
  },
  'recheck-review': {
    label: '回检审核',
    path: '/recheck-review',
    purpose: '回检审核人对待审核回检单进行通过/驳回处理'
  }
}
