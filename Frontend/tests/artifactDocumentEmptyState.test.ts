import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readDocument } from '../src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/ArtifactDocument'
import { WorkspaceToolError } from '../src/renderer/src/service/workspaceTools'

test('UI 设计清单缺失和空清单返回空态，实际读取故障仍保留错误', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {} })
  try {
    globalThis.fetch = async () => Response.json({ detail: 'File does not exist' }, { status: 404 })
    assert.deepEqual(await readDocument('/workspace', 'ui-design'), { pages: [] })
    globalThis.fetch = async () => Response.json({ content: '{"pages":[]}', truncated: false })
    assert.deepEqual(await readDocument('/workspace', 'ui-design'), { pages: [] })

    // 只有缺失属于空态，不能把鉴权、服务、断网或损坏的 JSON 伪装成没有设计。
    for (const status of [403, 500]) {
      globalThis.fetch = async () => Response.json({ detail: '读取失败' }, { status })
      await assert.rejects(
        readDocument('/workspace', 'ui-design'),
        (error: unknown) => error instanceof WorkspaceToolError && error.status === status
      )
    }
    globalThis.fetch = async () => {
      throw new Error('网络不可用')
    }
    await assert.rejects(readDocument('/workspace', 'ui-design'), /网络不可用/)
    globalThis.fetch = async () => Response.json({ content: 'invalid json', truncated: false })
    await assert.rejects(readDocument('/workspace', 'ui-design'), SyntaxError)
    globalThis.fetch = async () => Response.json({ content: '{}', truncated: false })
    await assert.rejects(readDocument('/workspace', 'ui-design'), /缺少页面清单/)
    globalThis.fetch = async () => Response.json({ content: '{"pages":[]}', truncated: true })
    await assert.rejects(readDocument('/workspace', 'ui-design'), /超出读取范围/)
  } finally {
    globalThis.fetch = originalFetch
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
