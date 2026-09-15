// 剧本节奏时钟：交互演示保持真实节奏；无头历史回放（historyReplay）置为快进后，
// 所有剧本等待立即兑现，用于在应用启动初期一次性生成静态历史会话。
// workbenchShared.delay 与 planningRuntime.planningDelay 都经由这里，避免两套开关漂移。

let fastForward = false

/** 开关快进模式；仅在历史回放生成期间置位，生成结束必须复位。 */
export function setReplayFastForward(enabled: boolean): void {
  fastForward = enabled
}

/** 剧本通用等待：快进模式下立即兑现。 */
export function replayDelay(ms: number, signal?: AbortSignal): Promise<void> {
  if (fastForward) return Promise.resolve()
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error('生成已停止。'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    /** 取消演示计时并停止当前节点，避免停止后的迟到写入。 */
    const abort = (): void => {
      clearTimeout(timer)
      reject(new Error('生成已停止。'))
    }
    signal?.addEventListener('abort', abort)
  })
}
