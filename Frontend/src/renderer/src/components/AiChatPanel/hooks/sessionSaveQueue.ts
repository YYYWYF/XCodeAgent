const saves = new Map<string, Promise<unknown>>()

/** 同会话保存按提交顺序串行执行，防止慢旧写入覆盖较新的已保存正文；失败不阻塞后续重试。 */
export async function enqueueSessionSave<T>(key: string, write: () => Promise<T>): Promise<T> {
  const previous = saves.get(key) || Promise.resolve()
  const pending = previous.catch(() => undefined).then(write)
  saves.set(key, pending)
  try {
    return await pending
  } finally {
    if (saves.get(key) === pending) saves.delete(key)
  }
}
