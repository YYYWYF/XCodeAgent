import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Button } from 'antd'
import ArtifactDocument from '../src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/ArtifactDocument'
import UiDesignPreviewPanel from '../src/renderer/src/components/AiChatPanel/components/UiDesignPreviewPanel'
import DesignRenderer from '../src/renderer/src/components/DesignRenderer/DesignRenderer'
import { installRendererDom } from './rendererDomHarness'

const pages = [
  { pageId: 'a', name: '页面甲', status: 'confirmed', code: 'export default () => <div>页面甲</div>' },
  { pageId: 'b', name: '页面乙', status: 'confirmed', code: 'export default () => <div>页面乙</div>' }
]
type Page = (typeof pages)[number] & { codeReadError?: string }

/** 使用真实文档组件与共享读取 Hook，只在文件/IPC 边界控制结果。 */
async function withBrowser(run: (context: {
  root: () => ReactTestRenderer
  state: { manifest: Array<Omit<Page, 'code'>>; source: Page[]; failed: boolean; reads: number;
    hold?: (resolve: (value: { uiDesigns: { pages: Page[] } }) => void) => void }
  render: (pageId?: string, catalogLoading?: boolean) => Promise<void>
}) => Promise<void>): Promise<void> {
  const restore = installRendererDom()
  const savedFetch = globalThis.fetch
  const state = { manifest: pages.map(({ code: _code, ...page }) => page), source: pages,
    failed: false, reads: 0,
    hold: undefined as ((resolve: (value: { uiDesigns: { pages: Page[] } }) => void) => void) | undefined }
  Object.defineProperty(window, 'devAgentStudio', { value: { workspace: {
    /** 生产源码读取器必须接收到真实的失败/恢复回执，不能直接修改组件状态。 */
    readUiDesigns: async () => {
      state.reads++
      if (state.failed) throw new Error('设计稿源码读取暂时失败')
      if (state.hold) return new Promise(state.hold)
      return { uiDesigns: { pages: state.source } }
    }
  } } })
  globalThis.fetch = async () => Response.json({ content: JSON.stringify({ pages: state.manifest }), truncated: false })
  let root: ReactTestRenderer | undefined
  /** 通过目录刷新和页面定位更新真实组件，保持实例以覆盖失效选择。 */
  const render = async (pageId = 'a', catalogLoading = false): Promise<void> => {
    await act(async () => {
      const view = createElement(ArtifactDocument, { workspaceRoot: '/test-design-browse',
        documentKey: 'ui-design', initialUiDesignPageId: pageId, catalogLoading })
      if (root) root.update(view)
      else root = create(view)
    })
  }
  try { await run({ root: () => root!, state, render }) } finally {
    if (root) await act(async () => { root!.unmount() })
    globalThis.fetch = savedFetch
    restore()
  }
}

test('源码读取失败显示原位重试，相同清单不变也可重读，成功后恢复预览', async () => {
  await withBrowser(async ({ root, state, render }) => {
    state.failed = true
    await render()
    assert.match(JSON.stringify(root().toJSON()), /设计稿读取失败/)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /暂无设计稿/)
    const retry = root().root.findByType(Button)
    assert.equal(retry.props.children, '重新读取')
    state.failed = false
    let release!: (value: { uiDesigns: { pages: Page[] } }) => void
    state.hold = (resolve) => { release = resolve }
    const previous = state.reads
    await act(async () => { retry.props.onClick() })
    assert.equal(state.reads, previous + 1)
    assert.equal(root().root.findByType(Button).props.disabled, true)
    assert.equal(root().root.findByType(Button).props.loading, true)
    await act(async () => { release({ uiDesigns: { pages } }) })
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /设计稿读取失败|暂无设计稿/)
    assert.equal(root().root.findByType(DesignRenderer).props.code, pages[0].code)
  })
})

test('当前页移除后保留其他设计稿列表，明确提示失效，选择其他页面后可继续浏览', async () => {
  await withBrowser(async ({ root, state, render }) => {
    await render('')
    state.manifest = [state.manifest[1]]
    state.source = [pages[1]]
    await render('', true)
    await render('', false)
    assert.match(JSON.stringify(root().toJSON()), /原选页面已不存在/)
    assert.match(JSON.stringify(root().toJSON()), /页面乙/)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /当前应用尚未生成 UI 设计稿/)
    assert.equal(root().root.findAllByType(DesignRenderer).length, 0, '不能把其他页面冒充失效目标')
    const target = root().root.findAllByType('button').find((button) =>
      button.findAllByType('span').some((span) => span.children.includes('页面乙')))!
    await act(async () => { target.props.onClick() })
    assert.equal(root().root.findByType(DesignRenderer).props.code, pages[1].code)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /原选页面已不存在/)
    await render('missing')
    assert.equal(root().root.findByType(UiDesignPreviewPanel).props.selectionMissing, true,
      '新的页面定位请求必须更新当前选择')
  })
})

test('单页源码故障不阻挡其他页面，真实未生成页仍保留正常空态', async () => {
  await withBrowser(async ({ root, state, render }) => {
    state.source = [pages[0], { ...pages[1], code: '', codeReadError: '本页文件读取失败' }]
    await render('b')
    assert.match(JSON.stringify(root().toJSON()), /本页文件读取失败/)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /暂无设计稿/)
    const target = root().root.findAllByType('button').find((button) =>
      button.findAllByType('span').some((span) => span.children.includes('页面甲')))!
    await act(async () => { target.props.onClick() })
    assert.equal(root().root.findByType(DesignRenderer).props.code, pages[0].code)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /本页文件读取失败/)
    state.source = [{ ...pages[1], code: '', status: 'pending' }]
    state.manifest = [{ pageId: 'b', name: '页面乙', status: 'pending' }]
    await render('b', true)
    await render('b')
    const pendingPage = root().root.findAllByType('button').find((button) =>
      button.findAllByType('span').some((span) => span.children.includes('页面乙')))!
    await act(async () => { pendingPage.props.onClick() })
    assert.match(JSON.stringify(root().toJSON()), /暂无设计稿/)
    assert.doesNotMatch(JSON.stringify(root().toJSON()), /设计稿读取失败/)
  })
})
