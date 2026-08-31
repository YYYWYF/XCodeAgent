// 武汉分行需求回检系统 · 新建应用生命周期（刚创建，需求收集前）。
import type { ApplicationLifecycle } from '../../src/renderer/src/typings'

/**
 * 验收完成态 lifecycle(ready_for_workbench + acceptance completed)。
 * 用于版本演示:达到此态的迭代版本已经具备发布条件。
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

// 最近项目演示从当前已发布版本进入，实时 lifecycle 与 v1.3 一致停在审查完成态。
export const pmsNewLifecycle: ApplicationLifecycle = makeCompleteLifecycle(
  'app-pms-new',
  '武汉分行需求回检系统'
)
