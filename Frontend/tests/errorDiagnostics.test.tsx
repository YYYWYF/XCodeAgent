import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create } from 'react-test-renderer'
import AgentErrorCard from '../src/renderer/src/components/AgentErrorCard'
import { errorDiagnosticText } from '../src/renderer/src/components/AgentErrorCard/diagnosticText'
import { useDiagnosticClipboard } from '../src/renderer/src/components/AgentErrorCard/useDiagnosticClipboard'

/** 带结构化查询诊断的网络错误也不能被错误归因为模型服务。 */
test('恢复卡显示具体原因和可复制诊断，不显示代码位置', () => {
  const diagnostic = {
    diagnosticId: 'diagnostic-A',
    operation: '构建任务工作流',
    exceptionType: 'ValueError',
    reason: "Found edge starting at unknown node 'builtin_authorization_bootstrap'",
    sourceRunId: 'run-A',
    threadId: 'thread-A',
    file: '/private/code.py',
    line: 42
  }
  const markup = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      title: '任务恢复状态读取失败',
      error: '工作流配置错误：引用了未注册的节点',
      diagnostic,
      onRetry: () => undefined,
      retryLabel: '重新同步'
    })
  )
  assert.match(markup, /未注册的节点/)
  assert.match(markup, /builtin_authorization_bootstrap/)
  assert.match(markup, /诊断编号：diagnostic-A/)
  assert.match(markup, /aria-label="复制诊断信息"/)
  assert.doesNotMatch(markup, />复制诊断信息</)
  assert.match(markup, /重新同步/)
  assert.doesNotMatch(markup, /private\/code.py|代码位置|第 42 行/)
  const network = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      error: '恢复状态读取失败：network error',
      diagnostic: { ...diagnostic, reason: 'network error' }
    })
  )
  assert.doesNotMatch(network, /模型服务异常|暂时无法连接模型服务/)
  assert.match(errorDiagnosticText('具体错误', diagnostic), /关联任务：run-A/)
})

/** 渲染复制 Hook 验证原生调用，内容更新和失败都不能显示错误的成功反馈。 */
test('诊断复制使用当前可见文本，失败保留手动复制入口', async () => {
  const originalWindow = globalThis.window
  const copied: string[] = []
  let rejectCopy = false
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    writable: true,
    value: {
      devAgentStudio: {
        clipboard: {
          /** 记录复制文本，并模拟剪贴板失败。 */
          writeText: async (text: string): Promise<void> => {
            if (rejectCopy) throw new Error('clipboard unavailable')
            copied.push(text)
          }
        }
      }
    }
  })
  let renderer: ReturnType<typeof create> | undefined
  let state: ReturnType<typeof useDiagnosticClipboard>
  /** 用无 DOM 的 Hook 宿主隔离图标样式注入，实际按钮结构由上面的组件渲染验证。 */
  function Probe({ text }: { text: string }): null {
    state = useDiagnosticClipboard(text)
    return null
  }
  try {
    await act(async () => {
      renderer = create(createElement(Probe, { text: '原因 A' }))
    })
    await act(async () => {
      await state.copyDiagnostic()
    })
    assert.deepEqual(copied, ['原因 A'])
    assert.equal(state!.copied, true)
    await act(async () => {
      renderer!.update(createElement(Probe, { text: '原因 B' }))
    })
    assert.equal(state!.copied, false)
    rejectCopy = true
    await act(async () => {
      await state.copyDiagnostic()
    })
    assert.equal(state!.copyFailed, true)
    assert.equal(state!.copied, false)
    assert.deepEqual(copied, ['原因 A'])
  } finally {
    act(() => renderer?.unmount())
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      writable: true,
      value: originalWindow
    })
  }
})
