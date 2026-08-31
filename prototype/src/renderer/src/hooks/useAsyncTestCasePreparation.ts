import { useEffect, useMemo } from 'react'
import {
  dispatchTestCaseQueue,
  retryFailedTestCaseTasks,
  type BackgroundTask
} from '../backgroundTasks'
import {
  createInitialTestCasePreparation,
  TEST_CASE_BLUEPRINTS,
  TEST_CASE_ESTIMATE_GROUPS,
  type TestCaseGenerationTaskType,
  type TestCaseGroup,
  type TestCasePrepCase,
  type TestCasePreparationSnapshot
} from '../testCasePreparation'
import { ensureBackgroundTaskEngine } from '../mock/backgroundTaskEngine'
import { useBackgroundTasks } from './useBackgroundTasks'

type AsyncTestCasePreparation = {
  retry: () => void
  snapshot: TestCasePreparationSnapshot
}

/** 依据用例队列回填分组进度，保证右侧目录树与顺序队列始终一致。 */
function deriveGroups(cases: TestCasePrepCase[]): TestCaseGroup[] {
  return TEST_CASE_ESTIMATE_GROUPS.map((group) => {
    const groupCases = cases.filter((item) => item.groupId === group.id)
    const generated = groupCases.filter((item) => item.status === 'ready').length
    const started = groupCases.some((item) => item.status !== 'queued')
    const completed = groupCases.length > 0 && groupCases.every((item) => item.status === 'ready')
    return {
      id: group.id,
      label: group.label,
      generated,
      total: groupCases.length || group.total,
      status: completed ? 'completed' : started ? 'generating' : 'queued'
    }
  })
}

/** 把统一任务流水中的用例任务状态映射为顺序队列的生成状态。 */
function mapCaseStatus(task: BackgroundTask): TestCasePrepCase['status'] {
  if (task.status === 'completed') return 'ready'
  if (task.status === 'failed') return 'failed'
  if (task.phase === 'validating') return 'validating'
  if (task.phase === 'generating') return 'generating'
  return 'queued'
}

/**
 * 模拟测试用例后台任务（统一 TaskStore 适配层）：计划确认并选定任务类型后按稳定用例
 * 基线建队（每条用例一个任务），后台引擎逐条推进「排队 → 生成 → 校验 → 就绪」。
 * 组件侧只消费派生快照，生成节奏与状态由 Store/引擎承载。
 */
export function useAsyncTestCasePreparation(
  applicationId: string,
  versionId: string,
  enabled: boolean,
  taskType?: TestCaseGenerationTaskType
): AsyncTestCasePreparation {
  const tasks = useBackgroundTasks()
  const caseTasks = useMemo(
    () =>
      tasks
        .filter(
          (task) =>
            task.kind === 'test_case_generation' &&
            task.applicationId === applicationId &&
            task.versionId === versionId
        )
        .sort((left, right) => left.createdAt - right.createdAt),
    [applicationId, tasks, versionId]
  )

  useEffect(() => {
    // 任务类型在开发准入门弹框中选定，选定即视为创建后台任务并建队；Store 按版本幂等。
    if (!enabled || !taskType || caseTasks.length > 0) return
    dispatchTestCaseQueue({
      applicationId,
      versionId,
      system: taskType,
      cases: TEST_CASE_BLUEPRINTS.map((blueprint) => ({
        id: blueprint.id,
        title: blueprint.title,
        groupId: blueprint.groupId,
        scenario: blueprint.scenario
      }))
    })
    ensureBackgroundTaskEngine()
  }, [applicationId, caseTasks.length, enabled, taskType, versionId])

  const snapshot = useMemo<TestCasePreparationSnapshot>(() => {
    if (!enabled) {
      // 计划未确认或版本切换后不展示旧版本生成进度；任务流水保留在 Store 中不清理。
      return createInitialTestCasePreparation()
    }
    if (caseTasks.length === 0) return createInitialTestCasePreparation()
    const cases = caseTasks.map<TestCasePrepCase>((task) => ({
      groupId: task.groupId || '',
      id: task.testCaseId || task.id,
      scenario: task.scenario || '',
      status: mapCaseStatus(task),
      taskType: task.pool,
      title: task.title
    }))
    const generated = cases.filter((item) => item.status === 'ready').length
    const anyValidating = cases.some((item) => item.status === 'validating')
    const anyPending = cases.some((item) => item.status === 'queued' || item.status === 'generating')
    const status: TestCasePreparationSnapshot['status'] = cases.some(
      (item) => item.status === 'failed'
    )
      ? 'failed'
      : generated === cases.length
        ? 'ready'
        : anyValidating
          ? 'validating'
          : anyPending
            ? 'generating'
            : 'queued'
    return {
      cases,
      generated,
      groups: deriveGroups(cases),
      status,
      taskType: caseTasks[0]?.pool,
      total: cases.length,
      updatedAt: caseTasks.reduce(
        (latest, task) => Math.max(latest, task.updatedAt),
        Date.now()
      )
    }
  }, [caseTasks, enabled])

  /** 失败后重新排队失败用例；沿用原任务类型与节奏，已就绪用例不受影响。 */
  const retry = (): void => {
    retryFailedTestCaseTasks(applicationId, versionId)
  }

  return { retry, snapshot }
}
