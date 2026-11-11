import { useEffect, useState } from 'react'
import { readWorkspaceFile } from '../service/workspaceTools'

const BUILD_TASK_PLAN_PATH = '.devagentstudio/plans/build-task-plan.json'

/**
 * 读取构建计划里"被某个模块任务认领"的文件集合。
 *
 * 用于「检查遗漏变更」：未提交文件里**不在这份集合中**的，就是没有归属到任何模块的零散改动，
 * 值得在项目收尾时让用户确认一次。
 *
 * 计划文件不存在（尚未构建过、或迭代已清空）时返回空集合 —— 调用方据此不做遗漏判断，
 * 而不是把所有文件都误报成"未关联"。
 */
export function useModuleOwnedFiles(workspaceRoot: string, refreshKey?: string): Set<string> {
  const [owned, setOwned] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!workspaceRoot) {
      setOwned(new Set())
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
        setOwned(moduleOwnedFilesFromPlan(result.content || ''))
      })
      .catch(() => {
        // 计划不可用时不做遗漏判断（见上方说明），静默留空。
        if (!cancelled) setOwned(new Set())
      })
    return () => {
      cancelled = true
    }
  }, [workspaceRoot, refreshKey])

  return owned
}

/**
 * 归一化工作区内的相对路径，供"认领文件集合"与"未提交文件"两侧对齐。
 *
 * 两侧来源不同（构建计划 JSON / Git status），同一个文件可能写成 `frontend/a.ts`、
 * `./frontend/a.ts` 或 `frontend\a.ts`。不归一化就会整片对不上，把所有文件都误报成
 * "未关联到任何模块" —— 那比不提示更糟。
 */
function normalizeWorkspacePath(path: string): string {
  return String(path || '')
    .trim()
    .replace(/\\/g, '/')
    .replace(/^(?:\.?\/)+/, '')
}

/** 从构建计划 JSON 文本里抽出全部任务声明的目标文件。 */
export function moduleOwnedFilesFromPlan(content: string): Set<string> {
  const owned = new Set<string>()
  let plan: unknown
  try {
    plan = JSON.parse(content)
  } catch {
    return owned
  }
  if (!plan || typeof plan !== 'object') return owned
  const registry = (plan as Record<string, unknown>).task_registry
  if (!registry || typeof registry !== 'object') return owned
  for (const task of Object.values(registry as Record<string, unknown>)) {
    if (!task || typeof task !== 'object') continue
    const record = task as Record<string, unknown>
    const files = record.target_files ?? record.targetFiles
    if (!Array.isArray(files)) continue
    for (const file of files) {
      const path = normalizeWorkspacePath(String(file || ''))
      if (path) owned.add(path)
    }
  }
  return owned
}

/**
 * 从"未提交文件"里挑出没有归属到任何模块的零散改动。
 *
 * 模块集合为空时返回空数组：那是"还不知道哪些文件属于模块"，不是"全部都没归属"。
 *
 * 工程级配置/锁文件（如 package.json、pnpm-lock.yaml、pnpm-workspace.yaml、tsconfig*.json）
 * 永远不属于任何业务模块的 target_files —— 它们由模板/Lint/依赖安装器维护，不属于"业务代码孤儿"。
 * 把它们报成"未关联到任何模块"是误报，会让用户在验收阶段对着锁文件去"确认归属"，没有可操作的出口。
 * 这里直接白名单过滤掉。
 */
const INFRASTRUCTURE_FILE_PATTERNS: ReadonlyArray<RegExp> = [
  // 各层 package.json 与锁文件
  /(?:^|\/)package\.json$/,
  /(?:^|\/)package-lock\.json$/,
  /(?:^|\/)pnpm-lock\.yaml$/,
  /(?:^|\/)yarn\.lock$/,
  /(?:^|\/)pnpm-workspace\.yaml$/,
  // TS / 构建配置
  /(?:^|\/)tsconfig[^/]*\.json$/,
  /(?:^|\/)vite\.config\.[tj]s$/,
  /(?:^|\/)vitest\.config\.[tj]s$/,
  /(?:^|\/)webpack\.config\.[tj]s$/,
  /(?:^|\/)rollup\.config\.[tj]s$/,
  /(?:^|\/)babel\.config\.[tj]s$/,
  /(?:^|\/)eslint\.config\.[tj]s$/,
  /(?:^|\/)\.eslintrc(?:\.[^/]+)?$/,
  /(?:^|\/)\.prettierrc(?:\.[^/]+)?$/,
  /(?:^|\/)\.gitignore$/,
  /(?:^|\/)\.gitattributes$/,
  /(?:^|\/)\.editorconfig$/,
  // 环境变量与说明文件
  /(?:^|\/)\.env(?:\.[^/]+)?$/,
  /(?:^|\/)README(?:\.[^/]+)?$/i
]

/** 判定路径是否为工程级配置/锁文件，这类文件不参与"业务模块孤儿"判定。 */
function isInfrastructurePath(path: string): boolean {
  const normalized = normalizeWorkspacePath(path)
  if (!normalized) return false
  return INFRASTRUCTURE_FILE_PATTERNS.some((pattern) => pattern.test(normalized))
}

export function orphanUncommittedPaths(input: {
  uncommittedPaths: readonly string[]
  moduleOwnedFiles: ReadonlySet<string>
}): string[] {
  if (input.moduleOwnedFiles.size === 0) return []
  return input.uncommittedPaths.filter(
    (path) =>
      !isInfrastructurePath(path) &&
      !input.moduleOwnedFiles.has(normalizeWorkspacePath(path))
  )
}

/**
 * 把遗漏文件列表压成一行摘要，供提醒卡副标题使用。
 *
 * 只列前几个再收尾：这里的作用是让用户一眼认出"是哪些文件"，完整清单在提交弹窗里。
 * 全部铺开会把提醒卡撑成一大块，反而盖住它真正要说的一句话。
 */
export function summarizePaths(paths: readonly string[], limit = 3): string {
  if (paths.length <= limit) return paths.join('、')
  return `${paths.slice(0, limit).join('、')} 等 ${paths.length} 个文件`
}
