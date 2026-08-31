import type { TestCaseGenerationTaskType } from './testCasePreparation'

// 两套后台任务系统：异步任务（常规算力域）与潮汐任务（闲时算力域）从存储到调度完全独立，
// 左侧菜单各有入口、各自一个抽屉，交互结构保持一致以降低认知成本。
// 同步执行不进任何任务池：由工作流在对话内当场执行，产物状态由工作流与已保存文件推导；
// 任务流水跟着应用走，不属于任何已知应用的任务在启动时清理。

export type BackgroundTaskKind = 'artifact_implementation' | 'test_case_generation'

/**
 * 任务系统（算力域）：async=异步任务（常规算力后台队列，同步执行记录也沉淀在此），
 * tide=潮汐任务（闲时算力后台队列）。两套系统各自独立存储、独立调度、独立入口。
 */
export type BackgroundTaskSystem = 'async' | 'tide'

/** 派发选择卡的选项取值：同步执行或进入某套后台任务系统。 */
export type BackgroundDispatchChoice = BackgroundTaskSystem | 'sync'

export type BackgroundTaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

/** 代码实现任务的后台执行阶段：排队 → Build DAG → 生成代码 → 构建及单元检查 → 页面预览 → 完成。 */
export type ArtifactImplementationPhase =
  | 'queued'
  | 'build_dag'
  | 'generating'
  | 'building'
  | 'preview'
  | 'completed'

/** 用例生成任务的后台执行阶段：排队 → 生成 → 校验 → 就绪。 */
export type TestCaseGenerationPhase = 'queued' | 'generating' | 'validating' | 'ready' | 'failed'

/** 代码实现任务的执行目标：页面（可携带依赖接口联合交付）或独立接口。 */
export type BackgroundTaskExecTarget =
  | { type: 'page'; pageId: string; includeEndpoint: boolean }
  | { type: 'endpoint'; apiContractId: string; endpointId: string }

/** 任务完成后的后续步骤；未执行时任务中心提供入口，在阶段主对话启动对应工作流。 */
export type BackgroundTaskNextStep = {
  type: 'artifact_acceptance'
  done: boolean
}

export type BackgroundTask = {
  id: string
  kind: BackgroundTaskKind
  /** 展示主标题，例如「页面「我的回检」代码实现」。 */
  title: string
  applicationId: string
  versionId: string
  /** 任务归属的任务系统；由创建它的存储写入，跨系统汇总是直接按此过滤。 */
  pool: BackgroundTaskSystem
  /** 任务覆盖的开发产物（page:x / endpoint:契约:接口）；页面任务可同时覆盖依赖接口。 */
  artifactIds: string[]
  /** 幂等与验收定位的主产物。 */
  primaryArtifactId?: string
  testCaseId?: string
  /** 用例任务所属业务场景分组。 */
  groupId?: string
  scenario?: string
  revision: number
  attempt: number
  status: BackgroundTaskStatus
  phase: string
  /** 0-100 的粗粒度进度，按阶段推进。 */
  progress: number
  /** 完成后的后续步骤；存在且未执行时，任务条目提供启动后续工作流的入口。 */
  nextStep?: BackgroundTaskNextStep
  /** 代码实现任务的执行目标；引擎据此生成对应产物。 */
  execTarget?: BackgroundTaskExecTarget
  createdAt: number
  updatedAt: number
}

/** 后续步骤的入口文案，任务抽屉共用。 */
export const BACKGROUND_TASK_NEXT_STEP_LABEL: Record<
  BackgroundTaskNextStep['type'],
  string
> = {
  artifact_acceptance: '验收'
}

/** 任务类型的轻量标签文案，抽屉逐条展示。 */
export const BACKGROUND_TASK_KIND_LABEL: Record<BackgroundTaskKind, string> = {
  artifact_implementation: '代码实现',
  test_case_generation: '用例生成'
}

/** 任务系统的展示名称：菜单入口、抽屉标题与派发详情共用一份文案。 */
export const BACKGROUND_TASK_SYSTEM_LABEL: Record<BackgroundTaskSystem, string> = {
  async: '异步任务',
  tide: '潮汐任务'
}

// —— 任务系统存储 ——
// 每套系统一份独立存储：window 单例隔离动态/静态 import 双实例，localStorage 键各自独立，
// 底层完全按两套任务设计；跨系统的读取（产物状态、待验收计数）走下方门面函数汇总。

type SharedTaskStoreState = {
  tasks: BackgroundTask[]
  listeners: Set<() => void>
  hydrated: boolean
}

const STORAGE_KEYS: Record<BackgroundTaskSystem, string> = {
  async: 'xcodeagent:prototype:async-tasks:v2',
  tide: 'xcodeagent:prototype:tide-tasks:v2'
}

const WINDOW_SLOT: Record<BackgroundTaskSystem, string> = {
  async: '__xcodeAgentAsyncTaskStore__',
  tide: '__xcodeAgentTideTaskStore__'
}

/** 读取某套系统的存储单例；首次访问时惰性创建。 */
function sharedStoreState(system: BackgroundTaskSystem): SharedTaskStoreState {
  const host = window as unknown as Record<string, SharedTaskStoreState | undefined>
  if (!host[WINDOW_SLOT[system]]) {
    host[WINDOW_SLOT[system]] = { tasks: [], listeners: new Set(), hydrated: false }
  }
  return host[WINDOW_SLOT[system]]!
}

/** 首次访问时从 localStorage 恢复该系统的任务流水；运行中任务按当前阶段继续由引擎推进。 */
function hydrate(system: BackgroundTaskSystem): void {
  const state = sharedStoreState(system)
  if (state.hydrated) return
  state.hydrated = true
  try {
    const stored = window.localStorage.getItem(STORAGE_KEYS[system])
    if (!stored) return
    const parsed = JSON.parse(stored) as { tasks?: unknown }
    if (!Array.isArray(parsed.tasks)) return
    state.tasks = parsed.tasks.filter(
      (task): task is BackgroundTask =>
        Boolean(task) &&
        typeof (task as BackgroundTask).id === 'string' &&
        typeof (task as BackgroundTask).status === 'string'
    )
  } catch {
    // 快照损坏时按空队列处理，演示旅程重新开始。
    state.tasks = []
  }
}

/** 该系统的任务流水落库；失败只影响刷新恢复，不阻塞当前演示。 */
function persist(system: BackgroundTaskSystem): void {
  try {
    window.localStorage.setItem(
      STORAGE_KEYS[system],
      JSON.stringify({ tasks: sharedStoreState(system).tasks })
    )
  } catch {
    // 忽略持久化失败（隐私模式/容量限制）。
  }
}

function notify(system: BackgroundTaskSystem): void {
  const state = sharedStoreState(system)
  state.listeners.forEach((listener) => listener())
}

function commit(system: BackgroundTaskSystem, nextTasks: BackgroundTask[]): void {
  const state = sharedStoreState(system)
  state.tasks = nextTasks
  persist(system)
  // 先失效跨系统汇总快照再通知：监听者（useSyncExternalStore）读到的必须是稳定且最新的引用。
  mergedTasksDirty = true
  notify(system)
}

// 跨系统汇总快照缓存：useSyncExternalStore 要求 getSnapshot 返回稳定引用，
// 因此合并结果只在任一系统 commit 后重建，而不是每次读取都新建数组。
let mergedTasksCache: BackgroundTask[] = []
let mergedTasksDirty = true

/** 生成任务 ID：同毫秒内多次派发也不会碰撞。 */
function createTaskId(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`
}

/** 单套任务系统的完整能力；引擎与抽屉按系统各自读写，互不干扰。 */
export type BackgroundTaskStore = {
  system: BackgroundTaskSystem
  getTasks(): BackgroundTask[]
  subscribe(listener: () => void): () => void
  tasksFor(applicationId: string, versionId: string): BackgroundTask[]
  dispatchArtifactImplementationTask(input: {
    applicationId: string
    versionId: string
    title: string
    artifactIds: string[]
    primaryArtifactId: string
    execTarget: BackgroundTaskExecTarget
  }): BackgroundTask
  dispatchTestCaseQueue(input: {
    applicationId: string
    versionId: string
    cases: Array<{ id: string; title: string; groupId: string; scenario: string }>
  }): void
  patchTask(id: string, patch: Partial<BackgroundTask>): void
  /** 取出仍处排队中的任务（跨队列迁移用）；运行中/已结束/同步记录不可取。 */
  takeQueuedTask(id: string): BackgroundTask | undefined
  /** 接收一条来自其它任务系统的排队任务：重置排队状态并追加到本队列尾部。 */
  admitQueuedTask(task: BackgroundTask): void
  /** 清理不属于任何已知应用的任务流水；应用随会话不持久化时其任务不应跨旅程残留。 */
  retainApplications(validApplicationIds: string[]): void
  /** 清空指定应用的全部任务流水（应用被删除时调用）。 */
  purgeApplication(applicationId: string): void
  acceptArtifactTask(id: string): boolean
  retryFailedTestCaseTasks(applicationId: string, versionId: string): void
  findAwaitingArtifactTask(
    artifactId: string,
    applicationId: string,
    versionId: string
  ): BackgroundTask | undefined
  readTestCaseTaskStatus(testCaseId: string): 'ready' | 'waiting' | 'unknown'
}

/** 创建一套任务系统的存储实现；两套系统各自实例化一份，逻辑完全同构。 */
function createBackgroundTaskStore(system: BackgroundTaskSystem): BackgroundTaskStore {
  /** 按应用与版本过滤本系统任务，保持派发次序稳定。 */
  const tasksFor = (applicationId: string, versionId: string): BackgroundTask[] =>
    sharedStoreState(system)
      .tasks.filter(
        (task) =>
          (!applicationId || task.applicationId === applicationId) &&
          (!versionId || task.versionId === versionId)
      )
      .sort((left, right) => left.createdAt - right.createdAt)

  const store: BackgroundTaskStore = {
    system,
    getTasks: () => {
      hydrate(system)
      return sharedStoreState(system).tasks
    },
    subscribe: (listener) => {
      const state = sharedStoreState(system)
      hydrate(system)
      state.listeners.add(listener)
      return () => {
        state.listeners.delete(listener)
      }
    },
    tasksFor,
    dispatchArtifactImplementationTask: (input) => {
      // 同一主产物已存在未取消/未失败的任务时幂等复用，避免续跑/重复派发压进两条实现任务。
      const existing = tasksFor(input.applicationId, input.versionId).find(
        (task) =>
          task.kind === 'artifact_implementation' &&
          task.primaryArtifactId === input.primaryArtifactId &&
          !['failed', 'cancelled'].includes(task.status)
      )
      if (existing) return existing
      const now = Date.now()
      const task: BackgroundTask = {
        id: createTaskId('task-impl'),
        kind: 'artifact_implementation',
        title: input.title,
        applicationId: input.applicationId,
        versionId: input.versionId,
        pool: system,
        artifactIds: input.artifactIds,
        primaryArtifactId: input.primaryArtifactId,
        revision: 1,
        attempt: 1,
        status: 'queued',
        phase: 'queued',
        progress: 0,
        execTarget: input.execTarget,
        createdAt: now,
        updatedAt: now
      }
      commit(system, [...sharedStoreState(system).tasks, task])
      return task
    },
    dispatchTestCaseQueue: (input) => {
      // 每条用例一个独立任务；该系统已有用例任务时幂等跳过。
      const existing = tasksFor(input.applicationId, input.versionId).filter(
        (task) => task.kind === 'test_case_generation'
      )
      if (existing.length > 0) return
      const now = Date.now()
      const tasks = input.cases.map<BackgroundTask>((item, index) => ({
        id: createTaskId(`task-case-${index}`),
        kind: 'test_case_generation',
        title: item.title,
        applicationId: input.applicationId,
        versionId: input.versionId,
        pool: system,
        artifactIds: [],
        testCaseId: item.id,
        groupId: item.groupId,
        scenario: item.scenario,
        revision: 1,
        attempt: 1,
        status: 'queued',
        phase: 'queued',
        progress: 0,
        createdAt: now + index,
        updatedAt: now + index
      }))
      commit(system, [...sharedStoreState(system).tasks, ...tasks])
    },
    patchTask: (id, patch) => {
      const tasks = sharedStoreState(system).tasks
      const index = tasks.findIndex((task) => task.id === id)
      if (index < 0) return
      // 未显式携带 updatedAt 时视为阶段切换并刷新时间戳。
      const next = { ...tasks[index], ...patch, updatedAt: patch.updatedAt ?? Date.now() }
      const nextTasks = [...tasks]
      nextTasks[index] = next
      commit(system, nextTasks)
    },
    takeQueuedTask: (id) => {
      hydrate(system)
      const tasks = sharedStoreState(system).tasks
      const task = tasks.find((item) => item.id === id && item.status === 'queued')
      if (!task) return undefined
      commit(
        system,
        tasks.filter((item) => item.id !== id)
      )
      return task
    },
    admitQueuedTask: (task) => {
      hydrate(system)
      const now = Date.now()
      // 接收时统一重置为排队态：进度清零，按目标队列的节奏重新开始；createdAt 保留原排队时间。
      commit(system, [
        ...sharedStoreState(system).tasks,
        { ...task, pool: system, status: 'queued', phase: 'queued', progress: 0, updatedAt: now }
      ])
    },
    retainApplications: (validApplicationIds) => {
      hydrate(system)
      const valid = new Set(validApplicationIds)
      const tasks = sharedStoreState(system).tasks
      const kept = tasks.filter((task) => valid.has(task.applicationId))
      if (kept.length === tasks.length) return
      commit(system, kept)
    },
    purgeApplication: (applicationId) => {
      hydrate(system)
      const tasks = sharedStoreState(system).tasks
      const kept = tasks.filter((task) => task.applicationId !== applicationId)
      if (kept.length === tasks.length) return
      commit(system, kept)
    },
    acceptArtifactTask: (id) => {
      const task = sharedStoreState(system).tasks.find((item) => item.id === id)
      if (!task || !task.nextStep || task.nextStep.done) return false
      // 任务状态本就是 completed，这里只落 nextStep.done；产物完成状态由统一流水推导。
      const nextTasks = sharedStoreState(system).tasks.map((item) =>
        item.id === id
          ? { ...item, nextStep: { ...task.nextStep!, done: true }, updatedAt: Date.now() }
          : item
      )
      commit(system, nextTasks)
      return true
    },
    retryFailedTestCaseTasks: (applicationId, versionId) => {
      const failed = tasksFor(applicationId, versionId).filter(
        (task) => task.kind === 'test_case_generation' && task.status === 'failed'
      )
      failed.forEach((task) => {
        store.patchTask(task.id, { status: 'queued', phase: 'queued', progress: 0 })
      })
    },
    findAwaitingArtifactTask: (artifactId, applicationId, versionId) =>
      tasksFor(applicationId, versionId).find(
        (task) =>
          task.kind === 'artifact_implementation' &&
          task.status === 'completed' &&
          task.nextStep?.type === 'artifact_acceptance' &&
          !task.nextStep.done &&
          task.artifactIds.includes(artifactId)
      ),
    readTestCaseTaskStatus: (testCaseId) => {
      const task = sharedStoreState(system).tasks.find(
        (item) => item.kind === 'test_case_generation' && item.testCaseId === testCaseId
      )
      if (!task) return 'unknown'
      // 失败任务视为可执行（由重新生成入口兜底），与原镜像语义一致。
      return task.status === 'completed' || task.status === 'failed' ? 'ready' : 'waiting'
    }
  }
  return store
}

/** 两套任务系统的单例；跨模块（动态/静态 import）各自拿到同一份 window 单例。 */
const TASK_STORES: Record<BackgroundTaskSystem, BackgroundTaskStore> = {
  async: createBackgroundTaskStore('async'),
  tide: createBackgroundTaskStore('tide')
}

/** 读取一套任务系统的存储；引擎与抽屉按系统直接读写。 */
export function getBackgroundTaskStore(system: BackgroundTaskSystem): BackgroundTaskStore {
  return TASK_STORES[system]
}

/** 跨系统汇总：读取全部任务（常规域 + 闲时域），供产物状态与计数推导。 */
export function getBackgroundTasks(): BackgroundTask[] {
  if (mergedTasksDirty) {
    mergedTasksCache = [...TASK_STORES.async.getTasks(), ...TASK_STORES.tide.getTasks()].sort(
      (left, right) => left.createdAt - right.createdAt
    )
    mergedTasksDirty = false
  }
  return mergedTasksCache
}

/** 跨系统订阅：任一系统变化都通知；供 useSyncExternalStore 使用。 */
export function subscribeBackgroundTasks(listener: () => void): () => void {
  const unsubscribeAsync = TASK_STORES.async.subscribe(listener)
  const unsubscribeTide = TASK_STORES.tide.subscribe(listener)
  return () => {
    unsubscribeAsync()
    unsubscribeTide()
  }
}

/** 跨系统按应用与版本过滤任务，保持派发次序稳定。 */
export function backgroundTasksFor(
  applicationId: string,
  versionId: string
): BackgroundTask[] {
  return getBackgroundTasks().filter(
    (task) =>
      (!applicationId || task.applicationId === applicationId) &&
      (!versionId || task.versionId === versionId)
  )
}

/**
 * 派发代码实现任务并路由到所选任务系统；同步执行（sync）沉淀在常规算力域。
 * 同一主产物的重复派发由所属系统幂等收敛。
 */
export function dispatchArtifactImplementationTask(input: {
  applicationId: string
  versionId: string
  title: string
  artifactIds: string[]
  primaryArtifactId: string
  execTarget: BackgroundTaskExecTarget
  system: BackgroundTaskSystem
}): BackgroundTask {
  return TASK_STORES[input.system].dispatchArtifactImplementationTask(input)
}

/** 按开发准入门选定的任务系统建用例生成队列；该系统已有用例任务时幂等跳过。 */
export function dispatchTestCaseQueue(input: {
  applicationId: string
  versionId: string
  system: TestCaseGenerationTaskType
  cases: Array<{ id: string; title: string; groupId: string; scenario: string }>
}): void {
  TASK_STORES[input.system].dispatchTestCaseQueue(input)
}

/** 跨系统打补丁：按任务 id 定位所属系统后推进（引擎与验收剧本共用）。 */
export function patchBackgroundTask(id: string, patch: Partial<BackgroundTask>): void {
  if (TASK_STORES.async.getTasks().some((task) => task.id === id)) {
    TASK_STORES.async.patchTask(id, patch)
    return
  }
  TASK_STORES.tide.patchTask(id, patch)
}

/**
 * 任务流水跟着应用走：清理不属于任何已知应用的任务（应用随会话不持久化时，
 * 其任务不应跨旅程残留）。在应用清单加载后调用一次即可。
 */
export function retainBackgroundTasksWithinApplications(validApplicationIds: string[]): void {
  TASK_STORES.async.retainApplications(validApplicationIds)
  TASK_STORES.tide.retainApplications(validApplicationIds)
}

/** 应用被删除时清空它的全部任务流水。 */
export function purgeApplicationTasks(applicationId: string): void {
  TASK_STORES.async.purgeApplication(applicationId)
  TASK_STORES.tide.purgeApplication(applicationId)
}

/**
 * 队列间切换：把仍处排队中的任务从当前算力域迁到目标算力域（潮汐↔异步）。
 * 只允许未开始的任务切换；迁移后按目标队列节奏重新排队，返回是否发生了迁移。
 */
export function switchBackgroundTaskQueue(
  id: string,
  target: BackgroundTaskSystem
): boolean {
  const source: BackgroundTaskSystem = TASK_STORES.async
    .getTasks()
    .some((task) => task.id === id)
    ? 'async'
    : 'tide'
  if (source === target) return false
  const task = TASK_STORES[source].takeQueuedTask(id)
  if (!task) return false
  TASK_STORES[target].admitQueuedTask(task)
  return true
}

/** 标记产物验收后续步骤已完成；按 id 定位所属系统执行。 */
export function acceptArtifactTask(id: string): boolean {
  if (TASK_STORES.async.acceptArtifactTask(id)) return true
  return TASK_STORES.tide.acceptArtifactTask(id)
}

/** 用例生成失败后重新排队失败任务；两套系统各自清理，沿用原系统与节奏。 */
export function retryFailedTestCaseTasks(applicationId: string, versionId: string): void {
  TASK_STORES.async.retryFailedTestCaseTasks(applicationId, versionId)
  TASK_STORES.tide.retryFailedTestCaseTasks(applicationId, versionId)
}

/**
 * 按产物跨系统定位「已完成且验收后续步骤未执行」的实现任务，供验收工作流建立任务关联。
 * 页面任务的 artifactIds 同时覆盖依赖接口，按任一产物身份都能找到同一条任务。
 */
export function findAwaitingArtifactTask(
  artifactId: string,
  applicationId: string,
  versionId: string
): BackgroundTask | undefined {
  return (
    TASK_STORES.async.findAwaitingArtifactTask(artifactId, applicationId, versionId) ||
    TASK_STORES.tide.findAwaitingArtifactTask(artifactId, applicationId, versionId)
  )
}

/**
 * 跨系统读取用例在生成队列中的就绪状态，供测试阶段剧本判断能否进入执行确认。
 * 返回 unknown 表示两套系统都没有该用例任务（调用方退化为直接执行）。
 */
export function readTestCaseTaskStatus(
  testCaseId: string
): 'ready' | 'waiting' | 'unknown' {
  const asyncStatus = TASK_STORES.async.readTestCaseTaskStatus(testCaseId)
  if (asyncStatus !== 'unknown') return asyncStatus
  return TASK_STORES.tide.readTestCaseTaskStatus(testCaseId)
}
