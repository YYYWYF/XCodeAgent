// 武汉分行需求回检系统 · 预置版本生命周期基线（验收完成态：当前迭代与已发布历史共用）。
import type { ApplicationLifecycle } from '../../src/renderer/src/typings'

/**
 * 验收完成态 lifecycle(ready_for_workbench + acceptance completed)。
 * 当前迭代 v1.3 用它表达"旅程已走完、测试/审查/验收全部通过、停在验收阶段
 * 等待用户生成版本"；已发布历史版本(v1.0-v1.2)用它表达发布时的旅程终态。
 * 六个阶段全部到达：顶部阶段条可任意切换，静态回看任一阶段的样貌。
 */
export function makeCompleteLifecycle(appId: string, appName: string): ApplicationLifecycle {
  const now = new Date().toISOString()
  return {
    schemaVersion: '1.2.0',
    application: { id: appId, name: appName },
    updatedAt: now,
    revision: 5,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {
      'app-acceptance': {
        scope: 'application',
        targetId: appId,
        threadId: `${appId}-acceptance`,
        runId: `${appId}-acceptance-run`,
        phase: 'acceptance',
        status: 'completed',
        startedAt: now,
        updatedAt: now
      }
    },
    extensions: {
    testExecutionStatus: 'passed',
    testCasesCompleted: 6,
    testCasesTotal: 6,
      reviewStatus: 'passed',
      acceptanceStatus: 'passed',
      phaseValidity: {
        analysis: 'valid',
        planning: 'valid',
        development: 'valid',
        testing: 'valid',
        review: 'valid',
        acceptance: 'valid'
      }
    }
  } as unknown as ApplicationLifecycle
}

// 实时 lifecycle 与当前迭代 v1.3 一致：全部阶段通过，定位验收阶段，等待用户生成版本。
export const pmsNewLifecycle: ApplicationLifecycle = makeCompleteLifecycle(
  'app-pms-new',
  '武汉分行需求回检系统'
)
