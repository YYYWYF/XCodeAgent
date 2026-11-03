import { parentPort } from 'node:worker_threads'
import { readIndexedPage, readIndexedSummary, saveIndexedSession } from './sessionHistoryIndex'

// 单 Worker 顺序执行读写，原子发布的正文与派生索引不会被自身并发请求交错使用。
let queue = Promise.resolve()
parentPort!.on('message', (request) => {
  queue = queue.then(async () => {
    try {
      const { operation, file, before, limit, session, loadedMessageIds } = request
      const result =
        operation === 'summary'
          ? await readIndexedSummary(file)
          : operation === 'read'
            ? await readIndexedPage(file, before, limit)
            : await saveIndexedSession(file, session, loadedMessageIds)
      parentPort!.postMessage({ id: request.id, result })
    } catch (error) {
      parentPort!.postMessage({
        id: request.id,
        error: error instanceof Error ? error.message : String(error)
      })
    }
  })
})
