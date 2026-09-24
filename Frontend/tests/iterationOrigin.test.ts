import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  UNKNOWN_ORIGIN_LABEL,
  designOriginKind,
  designOriginLabel,
  designOriginTone,
  designedOriginLabel,
  iterationOriginLabel,
  iterationOriginOf,
  uiDesignPendingLabel,
  undesignedOriginLabel
} from '../src/renderer/src/service/iterationOrigin'

test('归属为空 → unknown（不误标成旧迭代）', () => {
  assert.equal(iterationOriginOf(undefined, 'v1.1'), 'unknown')
  assert.equal(iterationOriginOf(null, 'v1.1'), 'unknown')
  assert.equal(iterationOriginOf('', 'v1.1'), 'unknown')
  // 当前分支未知时同样不猜
  assert.equal(iterationOriginOf('v1.0', undefined), 'unknown')
  assert.equal(iterationOriginOf('v1.0', ''), 'unknown')
})

test('归属等于当前分支 → current', () => {
  assert.equal(iterationOriginOf('v1.1', 'v1.1'), 'current')
  assert.equal(iterationOriginOf('  v1.1  ', 'v1.1'), 'current', '两侧空白要容忍')
})

test('归属是别的分支 → previous', () => {
  assert.equal(iterationOriginOf('v1.0', 'v1.1'), 'previous')
})

test('标注文案', () => {
  assert.equal(iterationOriginLabel('current', 'v1.1'), '当前版本')
  assert.equal(iterationOriginLabel('previous', 'v1.0'), 'v1.0 已完成')
  // 归属未知时不渲染徽章
  assert.equal(iterationOriginLabel('unknown', 'v1.0'), '')
  assert.equal(iterationOriginLabel('unknown', undefined), '')
  // 有归属但拿不到分支名：退化成不带版本号的文案，不能出现「undefined 已完成」
  assert.equal(iterationOriginLabel('previous', undefined), '旧迭代已完成')
  assert.equal(iterationOriginLabel('previous', '   '), '旧迭代已完成')
})

test('设计稿专用文案都带具体版本号', () => {
  assert.equal(undesignedOriginLabel('v1.0'), 'v1.0 该设计未设计')
  assert.equal(designedOriginLabel('v1.0'), 'v1.0 已设计过')
  assert.equal(undesignedOriginLabel(undefined), '该设计未设计')
  assert.equal(designedOriginLabel(''), '已设计过')
})

test('未知归属的兜底文案', () => {
  assert.equal(UNKNOWN_ORIGIN_LABEL, '已完成')
})

test('设计稿归属语义：两种事实都算 previous', () => {
  assert.equal(designOriginKind({ designedIn: 'v1.0' }), 'previous')
  assert.equal(designOriginKind({ plannedButUndesignedIn: 'v1.0' }), 'previous')
  // 两者都没有 → 本轮新增，不标注
  assert.equal(designOriginKind({}), 'unknown')
  assert.equal(designOriginKind(undefined), 'unknown')
})

test('设计稿视觉档位：设计过与未设计必须分开', () => {
  assert.equal(designOriginTone({ designedIn: 'v1.0' }), 'designed')
  assert.equal(designOriginTone({ plannedButUndesignedIn: 'v1.0' }), 'undesigned')
  assert.equal(designOriginTone({}), undefined)
  assert.equal(designOriginTone(undefined), undefined)
  // 同时有两种事实时以"设计过"为准，与 designOriginLabel 的口径一致
  assert.equal(
    designOriginTone({ designedIn: 'v1.0', plannedButUndesignedIn: 'v1.1' }),
    'designed'
  )
})

test('本轮无设计稿时的状态文案：区分"没做过"与"本轮待重做"', () => {
  // 上一轮设计过 → 本轮是重做，不能再说"未生成"
  assert.equal(uiDesignPendingLabel({ designedIn: 'v1.0' }), '本轮待生成')
  // 从来没设计过 / 读不到归属 → 保持原来的"未生成"
  assert.equal(uiDesignPendingLabel({ plannedButUndesignedIn: 'v1.0' }), '未生成')
  assert.equal(uiDesignPendingLabel({}), '未生成')
  assert.equal(uiDesignPendingLabel(undefined), '未生成')
})
