// 武汉分行项目管理系统 · 新建应用生命周期（刚创建，需求收集前）。
import type { ApplicationLifecycle } from '../../src/renderer/src/typings'

export const pmsNewLifecycle: ApplicationLifecycle = {
  schemaVersion: '1.2.0',
  application: { id: 'app-pms-new', name: '武汉分行项目管理系统' },
  updatedAt: new Date().toISOString(),
  revision: 3,
  initialization: { stage: 'collecting_requirement', status: 'running' },
  activeExecutions: {}
} as ApplicationLifecycle
