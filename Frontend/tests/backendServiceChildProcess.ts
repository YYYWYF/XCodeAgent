import { createRequire } from 'node:module'
import type { ChildProcess, SpawnOptions } from 'node:child_process'

const native = createRequire(import.meta.url)(
  'node:child_process'
) as typeof import('node:child_process')
export const launches: Array<{ child: ChildProcess; options: SpawnOptions }> = []
const waiting = new Set<() => void>()

/** 用本机 Node 执行临时 Backend 脚本，保留生产代码传入的管道、环境和进程组参数。 */
export function spawn(program: string, argv: string[], options: SpawnOptions): ChildProcess {
  if (program === 'taskkill') return native.spawn(program, argv, options)
  const child = native.spawn(process.execPath, [program, ...argv], options)
  launches.push({ child, options })
  waiting.forEach((resolve) => resolve())
  waiting.clear()
  return child
}

/** 精确等到启动入口创建真实进程，供退出与启动并发测试控制时序。 */
export async function waitForLaunch(index: number): Promise<void> {
  while (launches.length <= index) await new Promise<void>((resolve) => waiting.add(resolve))
}
