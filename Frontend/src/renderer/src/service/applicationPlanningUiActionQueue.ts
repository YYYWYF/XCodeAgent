/** Runtime 持有 UI 动作队列，组件重挂载不会丢失串行顺序或停止上一条消息流。 */
export class ApplicationPlanningUiActionQueue {
  private tail: Promise<void> = Promise.resolve()
  private transport: Promise<void> = Promise.resolve()
  private generation = 0

  /** 等先前动作和当前传输收尾后再读取最新确认门；失败不阻塞后续显式动作。 */
  enqueue(operation: () => Promise<void>): Promise<void> {
    const generation = this.generation
    const result = this.tail.then(async () => {
      let transport: Promise<void>
      do {
        transport = this.transport
        await transport
      } while (transport !== this.transport)
      if (generation !== this.generation) throw new Error('当前排队的 UI 操作已取消。')
      await operation()
    })
    this.tail = result.catch(() => undefined)
    return result
  }

  /** 显式停止、结束或释放 Runtime 时撤销尚未发送的动作，防止收尾后再次启动生成。 */
  cancelPending(): void {
    this.generation += 1
  }

  /** 登记一次实际传输，调用方必须在 finally 中通知收尾。 */
  beginTransport(): () => void {
    let settle: () => void = () => undefined
    this.transport = new Promise<void>((resolve) => { settle = resolve })
    return settle
  }
}
