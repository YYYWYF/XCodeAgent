// 武汉分行项目管理系统 · 设计镜像生命周期（规划期，需求澄清中）。
import type { ApplicationLifecycle } from '../../src/renderer/src/typings'

export const pmsDesignLifecycle: ApplicationLifecycle = {
  schemaVersion: '1.2.0',
  application: { id: 'app-pms-design', name: '武汉分行项目管理系统' },
  updatedAt: new Date().toISOString(),
  revision: 2,
  initialization: { stage: 'collecting_requirement', status: 'running' },
  activeExecutions: {}
} as ApplicationLifecycle
