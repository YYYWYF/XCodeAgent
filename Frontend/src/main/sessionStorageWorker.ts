import { parentPort } from 'node:worker_threads'
import { readSessionPage, readSessionSummary, saveSession } from './sessionHistory'

// 单 Worker 顺序执行尾读与流式保存，避免自身并发写入改变正在读取的正文。
let queue = Promise.resolve()
parentPort!.on('message', (request) => {
  queue = queue.then(async () => {
    try {
      const { operation, file, before, limit, session, loadedMessageIds } = request
      const result =
        operation === 'summary'
          ? await readSessionSummary(file)
          : operation === 'read'
            ? await readSessionPage(file, before, limit)
            : await saveSession(file, session, loadedMessageIds)
      parentPort!.postMessage({ id: request.id, result })
    } catch (error) {
      parentPort!.postMessage({
        id: request.id,
        error: error instanceof Error ? error.message : String(error)
      })
    }
  })
})
