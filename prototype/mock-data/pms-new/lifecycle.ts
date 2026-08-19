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

/**
 * 构造一个设计已完成、但仍处于当前迭代开发阶段的生命周期。
 * 该状态没有测试报告和审查完成态，因此版本仍可继续修改，直到用户真正生成版本。
 */
export function makeEditableDevelopmentLifecycle(
  appId: string,
  appName: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  return {
    schemaVersion: '1.2.0',
    application: { id: appId, name: appName },
    updatedAt: now,
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      phaseValidity: {
        analysis: 'valid',
        planning: 'valid',
        development: 'valid',
        testing: 'unreached',
        review: 'unreached'
      }
    }
  } as unknown as ApplicationLifecycle
}

// 最近项目演示停在“设计已完成、版本仍在迭代”的开发阶段，允许继续修改。
export const pmsNewLifecycle: ApplicationLifecycle = makeEditableDevelopmentLifecycle(
  'app-pms-new',
  '武汉分行需求回检系统'
)
