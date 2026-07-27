import assert from 'node:assert/strict'
import { test } from 'node:test'
import { shouldShowScrollToBottom } from '../src/renderer/src/components/AiChatPanel/components/MessageList/scrollState'

test('内容不足一屏时不显示滚动到底部按钮', () => {
  assert.equal(
    shouldShowScrollToBottom({ clientHeight: 600, scrollHeight: 600, scrollTop: 0 }),
    false
  )
})

test('可滚动内容位于底部时不显示滚动到底部按钮', () => {
  assert.equal(
    shouldShowScrollToBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 600 }),
    false
  )
})

test('距离底部未超过容差时不显示滚动到底部按钮', () => {
  assert.equal(
    shouldShowScrollToBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 576 }),
    false
  )
})

test('明显离开底部时显示滚动到底部按钮', () => {
  assert.equal(
    shouldShowScrollToBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 500 }),
    true
  )
})
