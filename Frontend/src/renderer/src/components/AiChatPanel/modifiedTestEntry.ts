import type { WorkflowRunPayload } from '../../typings'
import { workflowCodeChanges } from './utils'

/** 成功完成的实际代码修改提供本轮测试入口，普通问答和失败修改不冒充完成。 */
export function canEnterModifiedTesting(workflow?: WorkflowRunPayload): boolean {
  return workflow?.summary.phase === 'conversation' &&
    workflow.summary.intent === 'implementation_fix' &&
    workflow.summary.status === 'completed' &&
    Boolean(workflowCodeChanges(workflow)?.files.length)
}
