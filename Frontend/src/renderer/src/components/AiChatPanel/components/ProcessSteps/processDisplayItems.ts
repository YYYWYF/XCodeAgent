import type { ProcessStepRecord } from '../../../../service/agUiAgent'

export type ProcessDisplayItem =
  | { kind: 'step'; step: ProcessStepRecord; toolSteps?: ProcessStepRecord[] }
  | { kind: 'tool-chain'; steps: ProcessStepRecord[] }

/** 判断需要归入节点调用链的工具和命令步骤。 */
export function isToolActivityStep(step: ProcessStepRecord): boolean {
  return step.kind === 'tool' || step.kind === 'command'
}

/** 将工具活动归入当前节点轮次，避免多轮修复互相复制调用记录。 */
export function buildProcessDisplayItems(
  steps: ProcessStepRecord[],
  conversation: boolean
): ProcessDisplayItem[] {
  const nodes = steps.filter((step) => !isToolActivityStep(step))
  const toolsByStep = new Map<string, ProcessStepRecord[]>()
  const orphanTools: ProcessStepRecord[] = []
  const groupedTools = new Set<string>()

  for (const tool of steps.filter(isToolActivityStep)) {
    const repair = tool.nodeName === 'small_task_repair' || tool.nodeName === 'unit_test_repair'
    if (!conversation && !repair) continue
    groupedTools.add(tool.id)
    // 优先使用后端签发的轮次；普通对话工具按最近的节点开始位置归属。
    const owner = [...nodes]
      .reverse()
      .find(
        (node) =>
          node.nodeName === tool.nodeName &&
          Boolean(tool.nodeName) &&
          (tool.attempt !== undefined
            ? (node.attempt ?? 1) === tool.attempt
            : node.sequence <= tool.sequence)
      )
    if (owner) {
      toolsByStep.set(owner.id, [...(toolsByStep.get(owner.id) || []), tool])
    } else {
      orphanTools.push(tool)
    }
  }

  const items: ProcessDisplayItem[] = []
  let orphanAdded = false
  for (const step of steps) {
    if (groupedTools.has(step.id)) {
      if (orphanTools.includes(step) && !orphanAdded) {
        items.push({ kind: 'tool-chain', steps: orphanTools })
        orphanAdded = true
      }
      continue
    }
    const toolSteps = toolsByStep.get(step.id)
    items.push({ kind: 'step', step, ...(toolSteps?.length ? { toolSteps } : {}) })
  }
  return items
}
