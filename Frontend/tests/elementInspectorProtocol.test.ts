import assert from 'node:assert/strict'
import {
  ELEMENT_INSPECTOR_CHANNEL,
  ELEMENT_INSPECTOR_VERSION,
  createElementInspectorCommand,
  elementInspectorPreviewOrigin,
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

console.log('elementInspectorProtocol tests passed')
