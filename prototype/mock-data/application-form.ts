import type { ApplicationDraft } from '@renderer/typings'

// 演示案例数据：新建应用表单的预填字段。
// 换演示案例时替换这里，新建应用弹框会自动带上新案例的基础信息。
// 注意：这里不放 projectPath——演示目录已被存量应用占用（目录不可复用），
// 新建弹窗打开时会经 mock 目录选择器自动预填一个全新目录。
// 数据源不在新建时配置：数据来源滞后到开发阶段（左侧「数据来源」菜单）再绑定，
// 设计/计划阶段只面向 API 契约，不涉及真实数据来源。
// menus 由 constants.ts 的 initialApplicationDraft 主体独占，避免 spread 与字面量同名（TS2783）。
export const applicationFormPrefill: Partial<ApplicationDraft> = {
  appName: '武汉分行需求回检系统',
  appIcon: 'ProjectOutlined',
  senario: '需求回检填报与审核',
  // 行内码云仓库演示地址：与存量应用 v1.0-v1.2 已打的提交/Tag 指向同一仓库。
  gitRepoUrl: 'https://gitee.example.com/wuhan-branch/pms-requirement-recheck.git',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#6b3cf0' }
}
