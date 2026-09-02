import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import {
  ELEMENT_INSPECTOR_CHANNEL,
  ELEMENT_INSPECTOR_VERSION,
  createElementInspectorCommand,
  elementInspectorPreviewOrigin,
  inspectedElementContextFromMessage,
  isExpectedElementInspectorOrigin,
  parseElementInspectorMessage
} from '../src/renderer/src/components/BrowserPreviewPanel/elementInspectorProtocol'

const ready = parseElementInspectorMessage({
  channel: ELEMENT_INSPECTOR_CHANNEL,
  version: ELEMENT_INSPECTOR_VERSION,
  type: 'ready'
})
assert.equal(ready?.type, 'ready')

const selection = parseElementInspectorMessage({
  channel: ELEMENT_INSPECTOR_CHANNEL,
  version: ELEMENT_INSPECTOR_VERSION,
  type: 'element-selected',
  tagName: 'DIV',
  sourceLocation: { sourcePath: '/src/page/index.tsx', line: 15, column: 9 }
})
assert.deepEqual(selection, {
  channel: ELEMENT_INSPECTOR_CHANNEL,
  version: ELEMENT_INSPECTOR_VERSION,
  type: 'element-selected',
  tagName: 'div',
  sourceLocation: { sourcePath: '/src/page/index.tsx', line: 15, column: 9 }
})
assert.deepEqual(selection && inspectedElementContextFromMessage(selection), {
  tagName: 'div',
  sourcePath: '/src/page/index.tsx',
  line: 15,
  column: 9
})

assert.equal(
  inspectedElementContextFromMessage(
    parseElementInspectorMessage({
      channel: ELEMENT_INSPECTOR_CHANNEL,
      version: ELEMENT_INSPECTOR_VERSION,
      type: 'element-selected',
      tagName: 'span',
      sourceLocation: null
    })!
  ),
  null
)
assert.equal(
  parseElementInspectorMessage({
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: ELEMENT_INSPECTOR_VERSION,
    type: 'element-selected',
    tagName: 'span',
    sourceLocation: null
  })?.type,
  'element-selected'
)
assert.equal(
  parseElementInspectorMessage({
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: 2,
    type: 'ready'
  }),
  null
)
assert.equal(
  parseElementInspectorMessage({
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: ELEMENT_INSPECTOR_VERSION,
    type: 'element-selected',
    tagName: 'div',
    sourceLocation: { sourcePath: '/src/../secret.tsx', line: 1, column: 1 }
  }),
  null
)
assert.equal(
  parseElementInspectorMessage({
    channel: ELEMENT_INSPECTOR_CHANNEL,
    version: ELEMENT_INSPECTOR_VERSION,
    type: 'element-selected',
    tagName: 'div',
    sourceLocation: { sourcePath: '/src/page.tsx', line: 0, column: 1 }
  }),
  null
)
assert.deepEqual(createElementInspectorCommand(true), {
  channel: ELEMENT_INSPECTOR_CHANNEL,
  version: ELEMENT_INSPECTOR_VERSION,
  type: 'set-active',
  active: true
})
assert.equal(elementInspectorPreviewOrigin('http://localhost:3000/page/home'), 'http://localhost:3000')
assert.equal(elementInspectorPreviewOrigin('about:blank'), '')
assert.equal(
  isExpectedElementInspectorOrigin('http://localhost:3000', 'http://localhost:3000/page/home'),
  true
)
assert.equal(
  isExpectedElementInspectorOrigin('http://127.0.0.1:3000', 'http://localhost:3000/page/home'),
  false
)

const frontendRoot = process.cwd()
const aiChatPanelSource = readFileSync(
  path.join(frontendRoot, 'src/renderer/src/components/AiChatPanel/AiChatPanel.tsx'),
  'utf8'
)
const aiChatPanelStyles = readFileSync(
  path.join(frontendRoot, 'src/renderer/src/components/AiChatPanel/AiChatPanel.less'),
  'utf8'
)
const inspectorHookSource = readFileSync(
  path.join(
    frontendRoot,
    'src/renderer/src/components/BrowserPreviewPanel/useElementInspector.ts'
  ),
  'utf8'
)

// 审查模式不得覆盖或禁用左侧对话区，用户选择 DOM 后仍需能够输入修改要求。
assert.equal(aiChatPanelSource.includes('element-inspection-interaction-mask'), false)
assert.equal(aiChatPanelStyles.includes('element-inspection-interaction-mask'), false)
assert.equal(aiChatPanelSource.includes('element-inspection-interaction-guard'), false)
assert.equal(aiChatPanelStyles.includes('element-inspection-interaction-guard'), false)

// 仅 iframe 导航或刷新可以清理定位；关闭预览触发的 hook 卸载必须保留已选元素。
assert.equal(inspectorHookSource.match(/elementContextChangeRef\.current\?\.\(undefined\)/g)?.length, 1)

console.log('elementInspectorProtocol tests passed')
