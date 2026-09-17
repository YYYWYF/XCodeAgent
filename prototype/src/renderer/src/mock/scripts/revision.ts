// 共享单调 lifecycle revision 计数器：planning.ts(需求分析/项目计划阶段)与 workbench.ts(开发阶段)共用，
// 保证同一会话内任何后发 lifecycle 的 revision 都严格更大，避免 latestApplicationLifecycle
// 按 revision 拒绝合并导致 stage 冻住（曾出现设计 2001+ / 工作台 50001+ 的断层）。
//
// 高水位持久化到 localStorage：Vite HMR 会重新求值本模块，若内存计数器回退到基线，
// 长时间打开的页面仍持有旧的大 revision，后续所有 lifecycle 都会被拒收——症状是
// 确认后对话正常回复、领域记录已推进，但阶段条与工作台不切换。
const REVISION_STORAGE_KEY = 'aistudio:prototype:lifecycle-revision-hwm'
const REVISION_BASELINE = 50000

/** 读取持久化的 revision 高水位；没有存储或值不合法时回到基线。 */
function readHighWaterMark(): number {
  try {
    const stored = Number(window.localStorage.getItem(REVISION_STORAGE_KEY))
    return Number.isFinite(stored) && stored > REVISION_BASELINE ? stored : REVISION_BASELINE
  } catch {
    return REVISION_BASELINE
  }
}

/** 写入 revision 高水位；存储不可用（隐私模式等）时退化为纯内存计数，本会话内仍然单调。 */
function writeHighWaterMark(value: number): void {
  try {
    window.localStorage.setItem(REVISION_STORAGE_KEY, String(value))
  } catch {
    // 忽略写入失败：仅丢失跨模块重载的连续性，不影响当前会话。
  }
}

let lifecycleRevision = readHighWaterMark()

export function nextLifecycleRevision(): number {
  // 模块被 HMR 重新求值后从持久化高水位恢复；与内存计数取 max 兜底同一会话的连续性。
  lifecycleRevision = Math.max(lifecycleRevision, readHighWaterMark()) + 1
  writeHighWaterMark(lifecycleRevision)
  return lifecycleRevision
}

/**
 * 为前端本地合成的 lifecycle 快照（进入测试/审查、验收通过等）分配 revision。
 * 必须抬高共享计数器后再取值：若沿用 `当前 revision + 1`，会与剧本即将发出的下一帧
 * 撞号而被 latestApplicationLifecycle 拒绝合并，单帧终态事件（如审查通过）会永久丢失。
 */
export function nextSyntheticLifecycleRevision(currentRevision: number): number {
  if (lifecycleRevision <= currentRevision) {
    lifecycleRevision = currentRevision
  }
  return nextLifecycleRevision()
}
