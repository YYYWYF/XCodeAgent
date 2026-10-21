import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseDesignMentions } from '../src/renderer/src/service/designMention'

/** 复刻出问题的那份页面清单：展示名带空格，pageId 是下划线形式。 */
const HELLO_WORLD_PAGES = [{ pageId: 'hello_world', name: 'Hello World' }]

test('页面名带空格时也能解析出 pageId', () => {
  // 这是回归用例：浮层插入的就是 `@Hello World `，早先按空白切分会抓成 `Hello`，
  // 匹配不到页面 → pageIds 为空 → 卡片上该页不显示「生成中」。
  const { pageIds, instruction } = parseDesignMentions({
    text: '@Hello World 帮我把这个页面的文案改成粉色',
    pages: HELLO_WORLD_PAGES
  })
  assert.deepEqual(pageIds, ['hello_world'])
  assert.equal(instruction, '帮我把这个页面的文案改成粉色')
})

test('手打 pageId 同样认', () => {
  const { pageIds, instruction } = parseDesignMentions({
    text: '@hello_world 改成粉色',
    pages: HELLO_WORLD_PAGES
  })
  assert.deepEqual(pageIds, ['hello_world'])
  assert.equal(instruction, '改成粉色')
})

test('页面名大小写与清单不一致时仍能命中', () => {
  const { pageIds } = parseDesignMentions({
    text: '@hello world 改成粉色',
    pages: HELLO_WORLD_PAGES
  })
  assert.deepEqual(pageIds, ['hello_world'])
})

test('最长优先：同为前缀时长的赢', () => {
  // `Hello` 与 `Hello World` 都存在时，`@Hello World ...` 必须命中后者，
  // 否则会把 " World ..." 误当成调整指令。
  const pages = [
    { pageId: 'hello', name: 'Hello' },
    { pageId: 'hello_world', name: 'Hello World' }
  ]
  assert.deepEqual(parseDesignMentions({ text: '@Hello World 改成粉色', pages }).pageIds, [
    'hello_world'
  ])
  // 短的仍能单独命中（后面接的不是更长的名字）。
  assert.deepEqual(parseDesignMentions({ text: '@Hello 改成粉色', pages }).pageIds, ['hello'])
})

test('多个提及去重并全部映射', () => {
  const pages = [
    { pageId: 'hello_world', name: 'Hello World' },
    { pageId: 'overview', name: '概览页' }
  ]
  const { pageIds, instruction } = parseDesignMentions({
    text: '@Hello World 和 @概览页 都改成卡片布局，@Hello World 再放大字号',
    pages
  })
  assert.deepEqual(pageIds, ['hello_world', 'overview'])
  assert.equal(instruction, '和 都改成卡片布局，再放大字号')
})

test('没有提及：pageIds 为空，指令原样保留', () => {
  // 空 pageIds 是合法输入（后端按 instruction 自行判断调整哪些页），
  // 但这不是"提及解析失败"该有的样子 —— 它只用于用户确实没指定页面的场景。
  const { pageIds, instruction } = parseDesignMentions({
    text: '把所有页面的主色调改成粉色',
    pages: HELLO_WORLD_PAGES
  })
  assert.deepEqual(pageIds, [])
  assert.equal(instruction, '把所有页面的主色调改成粉色')
})

test('@ 后面不是已知页面时不误伤指令文本', () => {
  const { pageIds, instruction } = parseDesignMentions({
    text: '把邮箱 a@b.com 的提示文案改掉',
    pages: HELLO_WORLD_PAGES
  })
  assert.deepEqual(pageIds, [])
  assert.equal(instruction, '把邮箱 a@b.com 的提示文案改掉')
})

test('页面清单为空时不崩，且不吞文本', () => {
  const { pageIds, instruction } = parseDesignMentions({ text: '@任意内容 改一下', pages: [] })
  assert.deepEqual(pageIds, [])
  assert.equal(instruction, '@任意内容 改一下')
})
