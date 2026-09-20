import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isViewingHistoricalVersion } from '../src/renderer/src/service/applicationVersions'

/**
 * 工作台内容区的只读口径：只有回看**非活跃版本**才是历史版本。
 *
 * 这里刻意不看版本状态。当前版本发布后也是 released、阶段同样不可点，但它仍是
 * 用户正在推进的应用，必须保留对话区的执行情况视图——曾经用"阶段锁定"判断，
 * 结果刚发布的当前版本被换成了只读的应用文件/应用预览，用户看不到执行情况。
 */
test('活跃版本（含刚发布的 released）不算历史版本', () => {
  assert.equal(isViewingHistoricalVersion('app-v1-2', 'app-v1-2'), false)
})

test('切到非活跃版本才算历史版本', () => {
  assert.equal(isViewingHistoricalVersion('app-v1-2', 'app-v1-0'), true)
})

test('未指定查看版本时按活跃版本处理', () => {
  // viewingVersionId 为空表示跟随版本链头，即当前迭代。
  assert.equal(isViewingHistoricalVersion('app-v1-2', undefined), false)
  assert.equal(isViewingHistoricalVersion('app-v1-2', ''), false)
})
