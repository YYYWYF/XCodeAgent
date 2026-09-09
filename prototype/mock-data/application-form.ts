import type { ApplicationDraft } from '@renderer/typings'

// 演示案例数据：新建应用表单的预填字段。
// 换演示案例时替换这里，新建应用弹框会自动带上新案例的基础信息。
// 注意：这里不放 projectPath——演示目录已被存量应用占用（目录不可复用），
// 新建弹窗打开时会经 mock 目录选择器自动预填一个全新目录。
// menus 由 constants.ts 的 initialApplicationDraft 主体独占，避免 spread 与字面量同名（TS2783）。
export const applicationFormPrefill: Partial<ApplicationDraft> = {
  appName: '武汉分行需求回检系统',
  appIcon: 'ProjectOutlined',
  senario: '需求回检填报与审核',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#6b3cf0' },
  datasource: {
    type: 'None',
    db: {
      useBuiltin: false,
      connectionMode: 'plant',
      plantMode: {
        domain: '10.16.18.21',
        port: 3306,
        userName: 'pms_admin',
        pwd: '********',
        schema: 'pms_wuhan'
      }
    }
  }
}
