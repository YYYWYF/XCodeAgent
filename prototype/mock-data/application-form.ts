import type { ApplicationDraft } from '@renderer/typings'

// 演示案例数据：新建应用表单的预填字段。
// 换演示案例时替换这里，新建应用弹框会自动带上新案例的基础信息。
// menus 由 constants.ts 的 initialApplicationDraft 主体独占，避免 spread 与字面量同名（TS2783）。
export const applicationFormPrefill: Partial<ApplicationDraft> = {
  appName: '武汉分行需求回检系统',
  appIcon: 'ProjectOutlined',
  senario: '需求回检填报与审核',
  projectPath: 'C:\\Users\\WX\\Documents\\ExampleWorkspace\\wh-branch-pms-new',
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
