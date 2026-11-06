import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import DesignPagePreview from '../src/renderer/src/components/DesignRenderer/DesignPagePreview'
import UiDesignConfirmationPanel from '../src/renderer/src/components/Welcome/UiDesignConfirmationPanel'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import {
  projectPreviewUrl,
  projectPreviewMessageMatches,
  designPageGenerationLabel,
  isDesignReadyForConfirmation,
  type DesignPreviewPage,
  type AgentUiProjectPreview
} from '../src/renderer/src/components/DesignRenderer/projectPreview'

const preview = {
  kind: 'agent-ui-project' as const,
  workspace: '/workspace',
  pageId: 'home',
  revision: 'a'.repeat(64),
  launchId: 'b'.repeat(32),
  sourceDigest: 'c'.repeat(64),
  buildDigest: 'd'.repeat(64),
  source: 'official-mock' as const,
  url: `http://127.0.0.1:12345/${'b'.repeat(32)}/`
}

/** ready 仅允许用户提交正式确认，不能被展示成已经确认。 */
test('有效多文件 ready 可提交确认，失败或未生成页面保持禁用', () => {
  assert.equal(isDesignReadyForConfirmation(previewPage()), true)
  assert.equal(isDesignReadyForConfirmation({ status: 'ready' }), false)
  assert.equal(isDesignReadyForConfirmation({ ...previewPage(), status: 'generation_failed' }), false)
  assert.equal(isDesignReadyForConfirmation({ ...previewPage(), status: 'queued' }), false)
  assert.equal(isDesignReadyForConfirmation({ status: 'confirmed' }), true)
})

/** 确认按钮放行 ready 草稿，但卡片仍明确显示待确认。 */
test('Agent UI 草稿卡允许下一步且不宣称设计已确认', () => {
  const workflow = { runId: 'ready', events: [], summary: { status: 'requires_user_input',
    clarification: { mode: 'ui_design_confirmation', pages: [previewPage()] }
  } } as unknown as WorkflowRunPayload
  const html = renderToStaticMarkup(createElement(UiDesignConfirmationPanel, {
    workflow, showPreview: false, onSubmit: (): void => {}
  }))
  assert.match(html, /待确认/)
  assert.ok(!html.includes('尚待接通正式确认'))
  const button = html.match(/<button[^>]*class="[^"]*ui-design-confirm-all-btn[^>]*>/)?.[0]
  assert.ok(button)
  assert.ok(!button.includes('disabled'))
})

/** 官方输入框需接收提交事件；iframe 仍禁止弹窗、顶层导航和敏感设备访问。 */
test('受管画布允许表单事件且保留最小 sandbox 和设备权限', () => {
  const html = renderToStaticMarkup(
    createElement(DesignPagePreview, {
      workspaceRoot: preview.workspace,
      page: {
        pageId: preview.pageId,
        design_asset: {
          kind: 'agent-ui-project',
          revision: preview.revision,
          draft: 'fixture',
          pageIds: [preview.pageId],
          sourceDigest: preview.sourceDigest,
          buildDigest: preview.buildDigest
        },
        project_preview: preview
      }
    })
  )
  const sandbox = html.match(/sandbox="([^"]+)"/)?.[1].split(/\s+/)
  assert.deepEqual(new Set(sandbox), new Set(['allow-scripts', 'allow-same-origin', 'allow-forms']))
  assert.match(html, /referrerPolicy="no-referrer"/)
  assert.match(html, /camera &#x27;none&#x27;/)
  assert.match(html, /microphone &#x27;none&#x27;/)
})

/** 排队和模型执行分别反映后台事实，本地点击只代表提交中。 */
test('逐页生成明确区分提交、排队与执行', () => {
  assert.equal(designPageGenerationLabel({}, true), '提交中')
  assert.equal(designPageGenerationLabel({ status: 'queued' }, true), '排队中')
  assert.equal(designPageGenerationLabel({ status: 'generating' }), '生成中')
  assert.equal(designPageGenerationLabel({ status: 'ready' }), undefined)
})

type FrameWindow = { contentWindow: object }

/** 用真实 React 树观察 iframe 挂载，仅替代浏览器消息边界，不执行测试页面代码。 */
function withPreviewFrames(run: (harness: {
  root: ReactTestRenderer
  mounts: FrameWindow[]
  update: (page: DesignPreviewPage, workspace?: string) => void
  report: (value: AgentUiProjectPreview, frame: FrameWindow, origin?: string) => void
  expire: () => void
}) => void): void {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalHtmlElement = Object.getOwnPropertyDescriptor(globalThis, 'HTMLElement')
  const originalSvgElement = Object.getOwnPropertyDescriptor(globalThis, 'SVGElement')
  // Ant Design 的尺寸观察器仅检测 DOM 身份；测试画布不创建实际 DOM 元素。
  class TestElement {}
  Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: TestElement })
  Object.defineProperty(globalThis, 'SVGElement', { configurable: true, value: TestElement })
  const listeners = new Set<(event: MessageEvent) => void>()
  const timers = new Map<number, () => void>()
  let timerId = 0
  const mounts: FrameWindow[] = []
  const windows = new Map<string, FrameWindow>()
  let root: ReactTestRenderer | undefined
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    /** 持有本画布的真实消息订阅，卸载后必须被清理。 */
    addEventListener: (_type: string, listener: (event: MessageEvent) => void): void => { listeners.add(listener) },
    /** 模拟浏览器移除当前消息订阅。 */
    removeEventListener: (_type: string, listener: (event: MessageEvent) => void): void => { listeners.delete(listener) },
    /** 用可控时钟触发真实超时分支，不创建跨用例的真实定时器。 */
    setTimeout: (callback: () => void): number => {
      timers.set(++timerId, callback)
      return timerId
    },
    /** 卸载或切换预览后移除旧时钟。 */
    clearTimeout: (id: number): void => { timers.delete(id) }
  } })
  try {
    act(() => {
      root = create(createElement(DesignPagePreview, { page: {}, workspaceRoot: preview.workspace }), {
        /** 为每个文档分配稳定窗口，父层重渲染及 hash 切页不会改变来源身份。 */
        createNodeMock: (element): FrameWindow | null => {
          if (element.type !== 'iframe') return null
          const documentUrl = String(element.props.src).split('#')[0]
          const existing = windows.get(documentUrl)
          if (existing) return existing
          const frame = { contentWindow: {} }
          windows.set(documentUrl, frame)
          mounts.push(frame)
          return frame
        }
      })
    })
    run({ root: root!, mounts,
      /** 触发仍存活的就绪超时，用于验证已验证文档不会误报断连。 */
      expire: (): void => { act(() => { for (const callback of timers.values()) callback() }) },
      /** 通过父层 props 更新同一画布，不重新创建 React 根。 */
      update: (page, workspace = preview.workspace): void => {
        act(() => root!.update(createElement(DesignPagePreview, { page, workspaceRoot: workspace })))
      },
      /** 发送绑定指定窗口、来源及 launch 的就绪消息。 */
      report: (value, frame, origin = new URL(value.url).origin): void => {
        act(() => {
          const event = { source: frame.contentWindow, origin, data: {
            type: 'agent-ui-design-ready', launchId: value.launchId, revision: value.revision, pageId: value.pageId
          } } as MessageEvent
          for (const listener of listeners) listener(event)
        })
      }
    })
  } finally {
    act(() => root?.unmount())
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
    if (originalHtmlElement) Object.defineProperty(globalThis, 'HTMLElement', originalHtmlElement)
    else Reflect.deleteProperty(globalThis, 'HTMLElement')
    if (originalSvgElement) Object.defineProperty(globalThis, 'SVGElement', originalSvgElement)
    else Reflect.deleteProperty(globalThis, 'SVGElement')
  }
  assert.equal(listeners.size, 0)
}

/** 构造与当前受管 launch 完全一致的页面资产。 */
function previewPage(value: AgentUiProjectPreview = preview): DesignPreviewPage {
  return { pageId: value.pageId, status: 'ready', project_preview: value, design_asset: {
    kind: 'agent-ui-project', draft: 'fixture', pageIds: ['home', 'chat'], revision: value.revision,
    sourceDigest: value.sourceDigest, buildDigest: value.buildDigest
  } }
}

/** 紧凑确认卡必须展示生成失败原因，即使该页仍保留可查看的上一版工程。 */
test('紧凑页面列表展示生成错误，重试开始后移除旧错误', () => {
  const reason = '多文件 Agent UI 环境未配置：请设置独立 Node、pnpm CLI 和工具配置根目录。'
  for (const status of ['generation_failed', 'queued']) {
    const workflow = { runId: 'run-error', threadId: 'thread-error', events: [],
      summary: { status: 'requires_user_input', clarification: {
        mode: 'ui_design_confirmation', pages: [{ ...previewPage(), status, error: reason }]
      } }
    } as WorkflowRunPayload
    const html = renderToStaticMarkup(createElement(UiDesignConfirmationPanel, {
      workflow, workspaceRoot: preview.workspace, showPreview: false,
      /** 只验证错误展示，不触发真实生成。 */
      onSubmit: (): void => {}
    }))
    if (status === 'generation_failed') {
      assert.match(html, /role="alert"/)
      assert.ok(html.includes(reason))
    } else {
      assert.ok(!html.includes(reason))
    }
  }
})

/** 确认卡的加载分支也必须保留已有画布，不能只修复 iframe 子组件。 */
test('确认卡排队或重新生成期间仍渲染已有预览', () => {
  for (const status of ['queued', 'generating']) {
    const workflow = { runId: 'run-preview', threadId: 'thread-preview', events: [],
      summary: { status: 'requires_user_input', clarification: {
        mode: 'ui_design_confirmation', pages: [{ ...previewPage(), status }]
      } }
    } as WorkflowRunPayload
    const html = renderToStaticMarkup(createElement(UiDesignConfirmationPanel, {
      workflow, workspaceRoot: preview.workspace, activePageId: preview.pageId,
      /** 只检查渲染结果，禁止提交任何真实业务动作。 */
      onSubmit: (): void => {}
    }))
    assert.match(html, /<iframe/)
    assert.match(html, /暂时展示上一版设计稿/)
    assert.match(html, status === 'queued' ? /排队中/ : /生成中/)
  }
})

/** 生成状态、临时投影缺失和失败提示都不能卸载已展示的 iframe。 */
test('保留上一版画布直到新文档发出身份匹配的就绪回执', () => {
  withPreviewFrames(({ root, mounts, update, report }) => {
    const page = previewPage()
    update(page)
    report(preview, mounts[0])
    const first = root.root.findByType('iframe')
    update({ ...page, status: 'generating', project_preview: undefined, preview_error: '新预览正在交接' })
    assert.equal(root.root.findByType('iframe'), first)
    assert.equal(mounts.length, 1)
    assert.match(root.root.findByProps({ role: 'alert' }).props.className, /is-retaining-preview/)
    const next = { ...preview, launchId: 'e'.repeat(32), sourceDigest: 'f'.repeat(64),
      buildDigest: '1'.repeat(64), url: `http://127.0.0.1:12346/${'e'.repeat(32)}/` }
    update(previewPage(next))
    assert.equal(root.root.findAllByType('iframe').length, 2)
    assert.equal(root.root.findAllByType('iframe')[0], first)
    assert.match(root.root.findAllByType('iframe')[1].props.className, /pending/)
    report(next, mounts[0])
    report(next, mounts[1], 'http://invalid.example')
    assert.equal(root.root.findAllByType('iframe').length, 2)
    report(next, mounts[1])
    assert.equal(root.root.findAllByType('iframe').length, 1)
    assert.equal(root.root.findByType('iframe').props.src, `${next.url}#pageId=home`)
    assert.equal(mounts.length, 2)
  })
})

/** 进度快照短暂缺 URL 后复用同一 iframe，不会再次收到文档首次加载回执。 */
test('已验证文档恢复相同投影后不会在超时后误报未就绪', () => {
  withPreviewFrames(({ root, mounts, update, report, expire }) => {
    const page = previewPage()
    update(page)
    report(preview, mounts[0])
    update({ ...page, project_preview: undefined })
    update(page)
    expire()
    assert.equal(mounts.length, 1)
    assert.equal(root.root.findAllByProps({ role: 'alert' }).length, 0)
    const chat = { ...preview, pageId: 'chat' }
    update(previewPage(chat))
    expire()
    assert.equal(root.root.findAllByProps({ role: 'alert' }).length, 1)
    report(chat, mounts[0])
    assert.equal(root.root.findAllByProps({ role: 'alert' }).length, 0)
    const nextRevision = { ...chat, revision: 'e'.repeat(64) }
    update(previewPage(nextRevision))
    expire()
    assert.equal(root.root.findAllByProps({ role: 'alert' }).length, 1)
    report(nextRevision, mounts[0])
    assert.equal(root.root.findAllByProps({ role: 'alert' }).length, 0)
  })
})

/** 同一工程的页面切换只更新 hash，工作区切换则必须释放原窗口。 */
test('同 launch 切页保留窗口，跨工作区不复用旧画布', () => {
  withPreviewFrames(({ root, mounts, update, report }) => {
    update(previewPage())
    report(preview, mounts[0])
    const first = root.root.findByType('iframe')
    const chat = { ...preview, pageId: 'chat' }
    update(previewPage(chat))
    assert.equal(root.root.findByType('iframe'), first)
    assert.equal(mounts.length, 1)
    report(preview, mounts[0])
    assert.equal(root.root.findAllByProps({ role: 'status' }).length > 0, true)
    report(chat, mounts[0])
    assert.equal(root.root.findAllByProps({ role: 'status' }).length, 0)
    update({ ...previewPage(), project_preview: undefined }, '/other-workspace')
    assert.equal(root.root.findAllByType('iframe').length, 0)
  })
})

/** URL 只用于当前进程的 iframe，不能变成文件读取、远程导航或另一轮预览。 */
test('只接受当前 workspace/page/revision 的受管 loopback launch', () => {
  const expected = {
    workspace: '/workspace',
    pageId: 'home',
    revision: preview.revision,
    sourceDigest: preview.sourceDigest,
    buildDigest: preview.buildDigest
  }
  assert.equal(projectPreviewUrl(preview, expected), `${preview.url}#pageId=home`)
  for (const invalid of [
    { ...preview, workspace: '/other' },
    { ...preview, pageId: 'chat' },
    { ...preview, revision: 'e'.repeat(64) },
    { ...preview, sourceDigest: 'e'.repeat(64) },
    { ...preview, buildDigest: 'e'.repeat(64) },
    { ...preview, url: 'file:///etc/passwd' },
    { ...preview, url: `http://example.test:12345/${preview.launchId}/` },
    { ...preview, url: `http://user@127.0.0.1:12345/${preview.launchId}/` },
    { ...preview, url: `${preview.url}?sourcePath=/etc/passwd` },
    { ...preview, url: `${preview.url}#pageId=chat` },
    { ...preview, source: 'real-runtime' },
    { ...preview, launchId: 'x' }
  ])
    assert.equal(projectPreviewUrl(invalid, expected), undefined)
})

/** 即使窗口和来源正确，迟到的上一页、上一版或伪造消息也不能更新当前画布。 */
test('消息绑定当前 launch、revision 和 page；来源由组件再校验', () => {
  const message = {
    type: 'agent-ui-design-ready',
    launchId: preview.launchId,
    revision: preview.revision,
    pageId: preview.pageId
  }
  assert.equal(projectPreviewMessageMatches(message, preview), true)
  for (const invalid of [
    null,
    [],
    {},
    { ...message, pageId: 'chat' },
    { ...message, revision: 'e'.repeat(64) },
    { ...message, launchId: 'f'.repeat(32) },
    { ...message, type: 'read-file', sourcePath: '/etc/passwd' }
  ]) {
    assert.equal(projectPreviewMessageMatches(invalid, preview), false)
  }
})
