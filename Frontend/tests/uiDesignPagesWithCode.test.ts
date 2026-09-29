import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeUiDesignPageSources } from '../src/renderer/src/hooks/useUiDesignPagesWithCode'

/** 重新打开工作区时快照里的页面没有 code，必须从磁盘补回来，否则预览区一片空白。 */
test('缺 code 的页面从磁盘补回来', () => {
  const merged = mergeUiDesignPageSources(
    [{ pageId: 'home', status: 'confirmed' }],
    { home: 'export default function PageHome() {}' },
    { home: 'confirmed' }
  )

  assert.equal(merged[0].code, 'export default function PageHome() {}')
})

/** 生成池刚写完的 code 比磁盘新，不能被覆盖。 */
test('已有 code 的页面不被磁盘覆盖', () => {
  const merged = mergeUiDesignPageSources(
    [{ pageId: 'home', status: 'confirmed', code: '// 内存里的最新版本' }],
    { home: '// 磁盘上的旧版本' },
    {}
  )

  assert.equal(merged[0].code, '// 内存里的最新版本')
})

/** 磁盘 status 是生成池写下的权威事实，快照可能还停在 queued/generating。 */
test('status 以磁盘为准', () => {
  const merged = mergeUiDesignPageSources(
    [{ pageId: 'home', status: 'generating' }],
    {},
    { home: 'confirmed' }
  )

  assert.equal(merged[0].status, 'confirmed')
})

/** 磁盘上没有这一页（本轮还没生成）：保持原样，按钮禁用是正确表现。 */
test('磁盘没有该页时保持原样', () => {
  const source = [{ pageId: 'hello_agent', status: 'pending' }]
  const merged = mergeUiDesignPageSources(source, {}, {})

  assert.deepEqual(merged, source)
  assert.equal(merged[0].code, undefined)
})

/** 两者都没变化时返回原对象，避免无谓的重渲染。 */
test('无变化时返回同一对象引用', () => {
  const page = { pageId: 'home', status: 'confirmed', code: 'x' }
  const merged = mergeUiDesignPageSources([page], { home: 'y' }, { home: 'confirmed' })

  assert.equal(merged[0], page)
})

/** 缺 pageId 的条目无法索引，原样保留。 */
test('缺 pageId 的条目原样保留', () => {
  const page = { status: 'pending' }
  const merged = mergeUiDesignPageSources([page], { home: 'x' }, { home: 'confirmed' })

  assert.equal(merged[0], page)
})

/** 其余字段（名称、模板等）必须原样带过来，不能被合并丢掉。 */
test('其余字段原样保留', () => {
  const merged = mergeUiDesignPageSources(
    [{ pageId: 'home', name: '欢迎页', template_id: 'commonTable', status: 'confirmed' }],
    { home: 'code' },
    {}
  )

  assert.equal(merged[0].name, '欢迎页')
  assert.equal(merged[0].template_id, 'commonTable')
})
