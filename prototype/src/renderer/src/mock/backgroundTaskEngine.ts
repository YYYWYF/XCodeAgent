// 后台任务执行引擎：模拟 Worker 对两套任务流水的无人值守推进。
// 异步任务系统（常规算力域）与潮汐任务系统（闲时算力域）各自独立存储、独立调度，
// 两套系统并行推进互不等待；每条队列内一次只执行一个任务，按顺序逐个推进
// （用例生成、代码实现都排在各自队列里，不区分任务类型）。
// 代码实现任务按「排队 → Build DAG → 生成代码 → 构建及单元检查 → 页面预览 → 完成」推进，
// 用例生成任务按「排队 → 生成 → 校验 → 就绪」推进。
// 同步执行不产生任务记录：由工作流剧本在对话内当场执行，不经本引擎。

import {
  getBackgroundTaskStore,
  type BackgroundTask,
  type BackgroundTaskSystem
} from '../backgroundTasks'

/** 引擎轮询间隔：推进判断均基于 updatedAt + 阶段延时，刷新后可按剩余时间恢复。 */
const ENGINE_TICK_MS = 320

/** 代码实现任务的阶段演示节奏（毫秒）与阶段入口进度基线；两套系统共用节奏，仅调度域不同。 */
const ARTIFACT_PHASE_DELAYS: Record<string, number> = {
  queued: 900,
  build_dag: 2400,
  generating: 5600,
  building: 2600,
  preview: 1500
}

/** 代码实现任务各阶段区间的 [起始进度, 结束进度]；阶段内按耗时线性推进。 */
const ARTIFACT_PHASE_PROGRESS: Record<string, [number, number]> = {
  build_dag: [6, 20],
  generating: [20, 62],
  building: [62, 86],
  preview: [86, 96]
}

/** 用例生成任务按系统区分节奏：潮汐系统刻意长于开发演示时长。 */
const SYSTEM_CASE_DELAYS: Record<
  BackgroundTaskSystem,
  { caseGenerate: number; caseValidate: number; queueStart: number }
> = {
  async: { queueStart: 1200, caseGenerate: 5000, caseValidate: 1200 },
  tide: { queueStart: 6000, caseGenerate: 26000, caseValidate: 4000 }
}

/** 用例生成任务各阶段区间；排队段进度保持 0。 */
const CASE_PHASE_PROGRESS: Record<string, [number, number]> = {
  generating: [12, 84],
  validating: [84, 96]
}

/** 读取代码实现任务当前阶段的停留时长。 */
function artifactPhaseDelay(phase: string): number {
  return ARTIFACT_PHASE_DELAYS[phase] ?? 1000
}

/** 读取用例生成任务当前阶段的停留时长，节奏由所属任务系统决定。 */
function casePhaseDelay(system: BackgroundTaskSystem, phase: string): number {
  const delays = SYSTEM_CASE_DELAYS[system]
  if (phase === 'queued') return delays.queueStart
  if (phase === 'generating') return delays.caseGenerate
  return delays.caseValidate
}

/**
 * 阶段内插值进度：按 updatedAt 起的耗时在区间内线性推进，不改动 updatedAt。
 * 让抽屉里的百分比随时间平滑增长，而不是每阶段跳变一次。
 */
function interpolatedProgress(
  task: BackgroundTask,
  range: [number, number],
  delay: number
): number {
  const fraction = Math.min(1, Math.max(0, (Date.now() - task.updatedAt) / delay))
  return Math.round(range[0] + (range[1] - range[0]) * fraction)
}

/**
 * 判断任务所在应用与版本的队列是否已有任务在执行。
 * 一个队列一次只执行一个任务（不区分用例生成/代码实现，按顺序推进）；
 * 潮汐与异步是两条并行的队列，互不等待；跨应用、跨版本的任务互不阻塞。
 */
function laneBusy(task: BackgroundTask, tasks: BackgroundTask[]): boolean {
  return tasks.some(
    (item) =>
      item.id !== task.id &&
      item.applicationId === task.applicationId &&
      item.versionId === task.versionId &&
      item.status === 'running'
  )
}

/**
 * 推进一个代码实现任务：执行到「完成」即落终态；
 * 验收是完成后的后续步骤，由任务入口在主会话启动验收工作流。
 */
function advanceArtifactTask(
  store: ReturnType<typeof getBackgroundTaskStore>,
  task: BackgroundTask,
  tasks: BackgroundTask[]
): void {
  if (task.status === 'queued') {
    // 同队列内一次只执行一个任务：队头在执行时，后续任务按顺序继续排队。
    if (laneBusy(task, tasks)) return
    store.patchTask(task.id, { status: 'running', phase: 'build_dag', progress: 6 })
    return
  }
  if (task.status !== 'running') return
  const delay = artifactPhaseDelay(task.phase)
  // 运行中任务先同步插值进度，保持百分比平滑；未到阶段延时时不做状态迁移。
  const range = ARTIFACT_PHASE_PROGRESS[task.phase]
  if (range) {
    const progress = interpolatedProgress(task, range, delay)
    if (progress !== task.progress) {
      store.patchTask(task.id, { progress, updatedAt: task.updatedAt })
    }
  }
  if (Date.now() - task.updatedAt < delay) return
  switch (task.phase) {
    case 'build_dag':
      store.patchTask(task.id, { phase: 'generating', progress: 20 })
      return
    case 'generating':
      store.patchTask(task.id, { phase: 'building', progress: 62 })
      return
    case 'building':
      store.patchTask(task.id, { phase: 'preview', progress: 86 })
      return
    case 'preview':
      // 后台无人值守执行到「完成」；产物验收是后续步骤，入口挂在任务条目上。
      store.patchTask(task.id, {
        status: 'completed',
        phase: 'completed',
        progress: 100,
        nextStep: { type: 'artifact_acceptance', done: false }
      })
      return
    default:
      return
  }
}

/** 推进一个用例生成任务：生成与校验在所属系统内逐条串行。 */
function advanceTestCaseTask(
  store: ReturnType<typeof getBackgroundTaskStore>,
  system: BackgroundTaskSystem,
  task: BackgroundTask,
  tasks: BackgroundTask[]
): void {
  if (task.status === 'queued') {
    // 同队列内一次只执行一个任务：队头在执行时，后续任务按顺序继续排队。
    if (laneBusy(task, tasks)) return
    if (Date.now() - task.updatedAt < casePhaseDelay(system, 'queued')) return
    store.patchTask(task.id, { status: 'running', phase: 'generating', progress: 12 })
    return
  }
  if (task.status !== 'running') return
  const delay = casePhaseDelay(system, task.phase)
  const range = CASE_PHASE_PROGRESS[task.phase]
  if (range) {
    const progress = interpolatedProgress(task, range, delay)
    if (progress !== task.progress) {
      store.patchTask(task.id, { progress, updatedAt: task.updatedAt })
    }
  }
  if (Date.now() - task.updatedAt < delay) return
  if (task.phase === 'generating') {
    store.patchTask(task.id, { phase: 'validating', progress: 84 })
    return
  }
  if (task.phase === 'validating') {
    // 全部用例生成和校验完成后自动就绪，不增加用户重复确认步骤。
    store.patchTask(task.id, { status: 'completed', phase: 'ready', progress: 100 })
  }
}

/** 推进一套任务系统的流水：排队/运行中的后台任务按阶段延时分次迁移。 */
function tickSystem(system: BackgroundTaskSystem): void {
  const store = getBackgroundTaskStore(system)
  const tasks = store.getTasks()
  if (tasks.length === 0) return
  tasks.forEach((task) => {
    // 每次推进前重读最新流水：同一轮询里先处理的任务可能已经改变队列占用状态，
    // 沿用旧快照会让同批建队的排队任务被同时放行，破坏串行语义。
    const latest = store.getTasks()
    const current = latest.find((item) => item.id === task.id)
    if (!current) return
    if (current.status !== 'queued' && current.status !== 'running') return
    if (current.kind === 'artifact_implementation') advanceArtifactTask(store, current, latest)
    else if (current.kind === 'test_case_generation') {
      advanceTestCaseTask(store, system, current, latest)
    }
  })
}

/** 单次轮询：两套任务系统各自推进一次，互不等待。 */
function tick(): void {
  tickSystem('async')
  tickSystem('tide')
}

/**
 * 启动后台任务引擎（幂等）。两套系统共用一个循环但调度完全隔离，
 * 循环挂在 window 单例上，避免动态 import 产生的多模块实例重复推进同一任务。
 */
export function ensureBackgroundTaskEngine(): void {
  const host = window as unknown as { __xcodeAgentBackgroundTaskEngineRunning__?: boolean }
  if (host.__xcodeAgentBackgroundTaskEngineRunning__) return
  host.__xcodeAgentBackgroundTaskEngineRunning__ = true
  window.setInterval(tick, ENGINE_TICK_MS)
}
