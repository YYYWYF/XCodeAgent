// 演示案例数据：新建应用表单的预填字段。
// 换演示案例时替换这里，新建应用弹框会自动带上新案例的基础信息。
export const applicationFormPrefill = {
  appName: '武汉分行需求回检系统',
  appIcon: 'ProjectOutlined',
  senario: '需求回检填报与审核',
  projectPath: 'C:\\Users\\WX\\Documents\\ExampleWorkspace\\wh-branch-pms-new',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#6b3cf0' },
  menus: { enable: true, rootPath: '/page' },
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
