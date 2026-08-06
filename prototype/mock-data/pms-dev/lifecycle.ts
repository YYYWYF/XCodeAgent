// 武汉分行项目管理系统 · 开发镜像生命周期（规划已完成，进入开发）。
import type { ApplicationLifecycle } from '../../src/renderer/src/typings'

export const pmsDevLifecycle: ApplicationLifecycle = {
  schemaVersion: '1.2.0',
  application: { id: 'app-pms-dev', name: '武汉分行项目管理系统' },
  updatedAt: new Date().toISOString(),
  revision: 1,
  initialization: { stage: 'ready_for_workbench', status: 'completed' },
  activeExecutions: {}
} as ApplicationLifecycle
