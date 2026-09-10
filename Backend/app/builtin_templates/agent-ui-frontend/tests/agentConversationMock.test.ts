import { MockAgentConversationAdapter } from '@/apis/agentConversationMock'
import type { SendMessageInput } from '@/typings/agentConversation'

/** 创建低延迟且文案稳定的测试适配器。 */
function createAdapter(latencyMs = 0): MockAgentConversationAdapter {
  return new MockAgentConversationAdapter({
    agentName: '测试助手',
    assistantMessage: '已收到。',
    toolTitle: '示例工具',
    toolDetail: '示例工具已完成。',
    approvalTitle: '确认操作',
    approvalDetail: '请确认是否继续。',
    successMessage: '执行成功。',
    errorMessage: '模拟失败。',
    latencyMs
  })
}

/** 创建绑定指定会话和运行的完整发送输入。 */
function createInput(
  threadId: string,
  runId: string,
  content: string
): SendMessageInput {
  return {
    runId,
    threadId,
    agentId: 'assistant',
    actionId: 'assistant.ask',
    content,
    contextItems: [{ id: 'orderId', label: '订单号', value: 'A-001' }]
  }
}

describe('MockAgentConversationAdapter', () => {
  /** 工具场景必须返回可渲染的完成摘要。 */
  test('returns a completed tool card', async () => {
    const adapter = createAdapter()
    const thread = await adapter.createThread()

    const result = await adapter.sendMessage(
      createInput(thread.id, 'run-tool', '模拟工具：查询订单')
    )

    expect(result.status).toBe('completed')
    expect(result.messages.at(-1)?.tool?.status).toBe('completed')
  })

  /** 失败运行必须可通过相同 Adapter 接口重试成功。 */
  test('retries a failed run as a successful run', async () => {
    const adapter = createAdapter()
    const thread = await adapter.createThread()
    const failed = await adapter.sendMessage(
      createInput(thread.id, 'run-error', '模拟失败：验证重试')
    )

    const retried = await adapter.retry(failed.runId, 'run-error-retry-1')

    expect(failed.status).toBe('error')
    expect(retried.status).toBe('completed')
    expect(retried.runId).toBe('run-error-retry-1')
  })

  /** 审批场景必须等待明确决定后再进入终态。 */
  test('waits for and resolves an approval', async () => {
    const adapter = createAdapter()
    const thread = await adapter.createThread()
    const pending = await adapter.sendMessage(
      createInput(thread.id, 'run-approval', '模拟审批：提交操作')
    )
    const approvalId = pending.messages.at(-1)?.approval?.id

    const resolved = await adapter.resolveApproval({
      runId: pending.runId,
      approvalId: approvalId ?? '',
      approved: true
    })

    expect(pending.status).toBe('waiting_approval')
    expect(resolved.status).toBe('completed')
    expect(resolved.messages.some((message) => message.approval?.status === 'approved')).toBe(true)
  })

  /** 停止动作必须在延迟结束前终止运行并保留停止消息。 */
  test('stops an active run before completion', async () => {
    const adapter = createAdapter(20)
    const thread = await adapter.createThread()
    const resultPromise = adapter.sendMessage(
      createInput(thread.id, 'run-stop', '普通问题')
    )

    await adapter.stop('run-stop')
    const result = await resultPromise

    expect(result.status).toBe('stopped')
    expect(result.messages.at(-1)?.status).toBe('stopped')
  })

  /** 重试期间也必须暴露新的运行标识供停止动作使用。 */
  test('stops an active retry by its new run id', async () => {
    const adapter = createAdapter(20)
    const thread = await adapter.createThread()
    const failed = await adapter.sendMessage(
      createInput(thread.id, 'run-before-retry', '模拟失败：先失败')
    )
    const retryPromise = adapter.retry(failed.runId, 'run-active-retry')

    await adapter.stop('run-active-retry')
    const retried = await retryPromise

    expect(retried.status).toBe('stopped')
    expect(retried.runId).toBe('run-active-retry')
  })
})
