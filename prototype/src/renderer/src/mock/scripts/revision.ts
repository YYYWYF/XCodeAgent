// 共享单调 lifecycle revision 计数器：planning.ts(设计阶段)与 workbench.ts(开发阶段)共用，
// 保证同一会话内任何后发 lifecycle 的 revision 都严格更大，避免 latestApplicationLifecycle
// 按 revision 拒绝合并导致 stage 冻住（曾出现设计 2001+ / 工作台 50001+ 的断层）。
let lifecycleRevision = 50000

export function nextLifecycleRevision(): number {
  lifecycleRevision += 1
  return lifecycleRevision
}
