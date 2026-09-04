// 共享单调 lifecycle revision 计数器：planning.ts(需求分析/项目规划阶段)与 workbench.ts(开发阶段)共用，
// 保证同一会话内任何后发 lifecycle 的 revision 都严格更大，避免 latestApplicationLifecycle
// 按 revision 拒绝合并导致 stage 冻住（曾出现设计 2001+ / 工作台 50001+ 的断层）。
let lifecycleRevision = 50000

export function nextLifecycleRevision(): number {
  lifecycleRevision += 1
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
