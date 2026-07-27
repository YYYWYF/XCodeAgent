import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  isMessageListNearBottom,
  shouldShowScrollToBottom
} from '../src/renderer/src/components/AiChatPanel/components/MessageList/scrollState'

test('内容不足一屏时视为位于底部并保持自动跟随', () => {
  assert.equal(
    isMessageListNearBottom({ clientHeight: 600, scrollHeight: 500, scrollTop: 0 }),
    true
  )
})

test('可滚动内容位于底部时保持自动跟随', () => {
  assert.equal(
    isMessageListNearBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 600 }),
    true
  )
})

test('距离底部未超过容差时保持自动跟随', () => {
  assert.equal(
    isMessageListNearBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 576 }),
    true
  )
})

test('明显离开底部时暂停自动跟随', () => {
  assert.equal(
    isMessageListNearBottom({ clientHeight: 600, scrollHeight: 1200, scrollTop: 500 }),
    false
  )
})

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
