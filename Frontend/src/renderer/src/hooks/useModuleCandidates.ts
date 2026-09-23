import { useEffect, useState } from 'react'
import { readWorkspaceFile } from '../service/workspaceTools'

const BUILD_TASK_PLAN_PATH = '.devagentstudio/plans/build-task-plan.json'

/** 一个已完成、可供提前提交的模块（候选提交点）。 */
export type ModuleCandidate = {
  /** 模块标识，如 `page:page_welcome`。 */
  unitId: string
  /** 模块类型：page / frontend / backend / application 等。 */
  kind: string
  /** 展示名，取页面/接口标识，退化到任务标题。 */
  label: string
  /** 该模块本轮写入的文件。 */
  files: string[]
}

/**
 * 读取构建计划里"已经做完、可以提前提交"的模块。
 *
 * 为什么从计划文件而不是从对话消息里取：消息按 `workspaceRoot + editorMode + sessionId`
 * 分桶，**不含版本**，同一个工作区各版本的会话混在一起，按消息聚合会把 v1.0 的模块算到
 * v1.1 头上。计划文件天然按迭代隔离（新建迭代会清空 `.devagentstudio/plans`）。
 *
 * 计划不可用时返回空数组 —— 调用方据此不显示角标，而不是报错。
 */

/**
 * 计划文件会**在用户看着的时候变化**（构建任务逐个完成，计划随之更新），
 * 而只按 workspaceRoot 读一次会永远停在挂载那一刻的快照 —— 构建跑完也不会出现
 * 候选模块。所以额外接受一个 refreshKey，由调用方传入会随构建/文件变化而变的信号
 * （lifecycle revision + Git 指纹），变化时重读。
 */
export function useModuleCandidates(workspaceRoot: string, refreshKey?: string): ModuleCandidate[] {
  const [candidates, setCandidates] = useState<ModuleCandidate[]>([])

  useEffect(() => {
    if (!workspaceRoot) {
      setCandidates([])
      return
    }
    let cancelled = false
    void readWorkspaceFile({
      workspace_root: workspaceRoot,
      path: BUILD_TASK_PLAN_PATH,
      max_chars: 200000,
      // max_lines 必须显式给足：接口默认只返回 400 行，而 build-task-plan.json 里
      // task_registry 排在 build_units/unit_graph 之后，实测 439 行的文件会被切在第 400 行，
      // JSON.parse 直接失败 —— 静默表现为"没有候选模块/没有遗漏文件"。
      max_lines: 5000
    })
      .then((result) => {
        if (cancelled) return
        setCandidates(moduleCandidatesFromPlan(result.content || ''))
      })
      .catch(() => {
        // 计划读不到（尚未构建过、或迭代已清空）时静默留空，见上方说明。
        if (!cancelled) setCandidates([])
      })
    return () => {
      cancelled = true
    }
  }, [workspaceRoot, refreshKey])

  return candidates
}

/**
 * 筛出"现在真的可以先提交"的模块。
 *
 * 计划文件只说模块**做完过**，不说它的文件**还没提交**。提交之后模块在计划里仍是
 * `completed`，不做这层过滤的话角标会一直挂着，暗示"可以先提交"而实际无事可做。
 * 与提交后角标要刷新是同一个道理：状态源必须跟实际事实对齐。
 */
export function actionableCandidates(input: {
  candidates: readonly ModuleCandidate[]
  uncommittedPaths: readonly string[]
}): ModuleCandidate[] {
  if (input.uncommittedPaths.length === 0) return []
  const pending = new Set(input.uncommittedPaths)
  return input.candidates.filter((candidate) => candidate.files.some((file) => pending.has(file)))
}

/** 从构建计划 JSON 文本里抽出已完成的模块。 */
export function moduleCandidatesFromPlan(content: string): ModuleCandidate[] {
  const plan = parsePlan(content)
  if (!plan) return []
  const units = asRecord(plan.build_units)
  const registry = asRecord(plan.task_registry)
  if (!units) return []

  const candidates: ModuleCandidate[] = []
  for (const [unitId, rawUnit] of Object.entries(units)) {
    const unit = asRecord(rawUnit)
    if (!unit) continue
    const taskIds = Array.isArray(unit.task_ids) ? unit.task_ids.map(String) : []
    if (taskIds.length === 0) continue

    const tasks = taskIds
      .map((taskId) => asRecord(registry?.[taskId]))
      .filter((task): task is Record<string, unknown> => task !== undefined)
    // 任务表里查不到的任务不能当作"已完成" —— 宁可不提示，也不误报。
    if (tasks.length !== taskIds.length) continue
    // 模块做完了：全部任务 completed。
    if (!tasks.every((task) => task.status === 'completed')) continue

    const files = collectTargetFiles(tasks)
    // 与任务级检查点同一口径：没写过文件就不构成可提交的东西。
    if (files.length === 0) continue

    candidates.push({
      unitId,
      kind: String(unit.kind || ''),
      label: moduleLabel(unit, tasks, unitId),
      files
    })
  }
  return candidates
}

function parsePlan(content: string): Record<string, unknown> | undefined {
  try {
    const parsed: unknown = JSON.parse(content)
    return asRecord(parsed)
  } catch {
    return undefined
  }
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** 汇总模块下所有任务声明的目标文件，去重并归一化路径。 */
function collectTargetFiles(tasks: Record<string, unknown>[]): string[] {
  const files: string[] = []
  for (const task of tasks) {
    const declared = task.target_files ?? task.targetFiles
    if (!Array.isArray(declared)) continue
    for (const entry of declared) {
      const path = String(entry || '')
        .trim()
        .replace(/\\/g, '/')
        .replace(/^(?:\.?\/)+/, '')
      if (path && !files.includes(path)) files.push(path)
    }
  }
  return files
}

/**
 * 模块展示名。
 *
 * 优先用页面/接口标识（用户在设计与对话里就是这么看到它的），退化到任务标题，
 * 最后才是内部 unit id —— 内部 id 对用户没有意义，只作为兜底。
 */
function moduleLabel(
  unit: Record<string, unknown>,
  tasks: Record<string, unknown>[],
  unitId: string
): string {
  for (const key of ['page_id', 'endpoint_id', 'entity_id', 'data_source_id']) {
    const value = String(unit[key] || '').trim()
    if (value) return value
  }
  for (const task of tasks) {
    const title = String(task.title || '').trim()
    if (title) return title
  }
  return unitId
}
