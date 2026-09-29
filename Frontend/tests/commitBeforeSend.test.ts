import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  isStageAdvanceDecision,
  shouldGateSend
} from '../src/renderer/src/components/AiChatPanel/components/MilestoneCommitReminder/useCommitBeforeSend'

/** 构造判据输入：默认是"有 3 个文件可提交、用户还没选过稍后"的最常见拦截场景。 */
function decide(over: Partial<Parameters<typeof shouldGateSend>[0]> = {}): boolean {
  return shouldGateSend({
    eligibleCount: 3,
    dismissed: false,
    inspecting: false,
    artifactsSettling: false,
    ...over
  })
}

test('有未提交变更且用户没选过「稍后」时拦下发送', () => {
  assert.equal(decide(), true, '有可提交文件就该先问一次')
  assert.equal(decide({ eligibleCount: 1 }), true, '哪怕只有 1 个文件也要拦')
})

test('没有可提交文件时不拦', () => {
  // 设计阶段仓库可能还没建立，0 个文件时弹窗只会让用户无从选择。
  assert.equal(decide({ eligibleCount: 0 }), false)
})

test('用户对当前这份变更选过「稍后」就不再拦', () => {
  assert.equal(decide({ dismissed: true }), false)
})

test('提交过之后仍然会拦新产生的变更', () => {
  // 门禁不是一次性的：提交后快照里没东西可提交（eligibleCount=0）自然不拦，
  // 但用户又改了代码就还要拦 —— 判据里不能有"提交过就永久闭嘴"这一条。
  assert.equal(decide({ eligibleCount: 0 }), false, '刚提交完、无剩余变更时不拦')
  assert.equal(decide({ eligibleCount: 2 }), true, '又改了代码，下一次推进还要拦')
})

test('还在读 Git 状态时不拦', () => {
  // 此时 eligibleCount 还是上一帧的值（首帧是 0），据此打扰用户会误报。
  assert.equal(decide({ inspecting: true }), false)
  assert.equal(decide({ inspecting: true, eligibleCount: 5 }), false, '读数未完成时数字不可信')
})

test('设计稿还在生成时不拦', () => {
  // 此刻提交会捞到一个中间态快照，所以既不能提交、也不该弹一个主操作不可用的窗去挡发送。
  assert.equal(decide({ artifactsSettling: true }), false)
  assert.equal(decide({ artifactsSettling: true, eligibleCount: 9 }), false)
})

test('多个"不拦"条件同时成立时依然不拦', () => {
  assert.equal(
    decide({ artifactsSettling: true, inspecting: true, dismissed: true }),
    false
  )
})

test('跳阶段的卡片确认要过门禁', () => {
  // 开发阶段"确认并返回设计阶段 / 确认并进入计划阶段"：不经过输入框，点一下就走。
  assert.equal(isStageAdvanceDecision({ revision_impact_confirmation: 'approved' }), true)
  // 设计阶段确认完毕进入计划阶段。
  assert.equal(isStageAdvanceDecision({ planning_stage_entry: 'enter' }), true)
})

test('取消跳阶段不算推进，不拦', () => {
  // 同一张卡上的"取消"只是关掉卡片，人还在原地。
  assert.equal(isStageAdvanceDecision({ revision_impact_confirmation: 'rejected' }), false)
})

test('阶段内审批与验收确认不拦', () => {
  // 这些不换阶段；验收另有 AcceptanceCommitDock 承担提交，拦在这里是重复。
  for (const answers of [
    { unit_test_confirmation: 'run' },
    { test_phase_confirmation: { action: 'confirm' } },
    { review_phase_confirmation: { action: 'confirm' } },
    { acceptance_phase_confirmation: { action: 'confirm' } },
    { code_review_repair_confirmation: { action: 'repair_all' } },
    { api_design_gate: { action: 'confirm' } },
    { implementation_fix_confirmation: 'approved' }
  ]) {
    assert.equal(isStageAdvanceDecision(answers), false, `${JSON.stringify(answers)} 不换阶段`)
  }
})

test('正式草稿交互不拦：走到那张卡时阶段已经跳过了', () => {
  assert.equal(isStageAdvanceDecision({ revision_draft_interaction: { action: 'confirm' } }), false)
})

test('空答案（UI 设计稿轮询）不拦', () => {
  assert.equal(isStageAdvanceDecision({}), false)
})
