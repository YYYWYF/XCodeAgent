// 武汉分行需求回检系统 · 页面 label/path/purpose 表（workbench 剧本按 pageId 取）。
export const WORKBENCH_PAGES: Record<string, { label: string; path: string; purpose: string }> = {
  'recheck-introduction': {
    label: '回检介绍',
    path: '/recheck-introduction',
    purpose: '介绍需求回检的适用场景、处理流程和使用方式'
  },
  'my-rechecks': {
    label: '我的回检',
    path: '/my-rechecks',
    purpose: '回检填报人提交需求回检单，跟踪回检状态'
  }
}
