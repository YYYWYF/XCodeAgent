import type { RuntimeAgent } from '../../../../service/runtimeWorkbench'

export const developmentStatusLabels: Record<string, string> = {
  pending: '待开发', running: '开发中', completed: '已完成', already_satisfied: '已完成',
  failed: '失败', blocked: '受阻', stopped: '已停止', awaiting_user: '等待确认', stopping: '停止中'
}

const descriptions: Record<string, { title: string; description: string }> = {
  prompt: { title: '角色与回答规则', description: '明确智能体的角色、表达方式与业务边界。' },
  model: { title: '模型与运行配置', description: '接入项目模型，应用当前生成参数。' },
  memory: { title: '会话记忆', description: '实现当前智能体的会话记忆配置。' },
  tools: { title: '业务工具接入', description: '按接口契约接入智能体需要的业务工具。' },
  skills: { title: '扩展技能', description: '装配已启用的技能资源。' },
  knowledge: { title: '知识资源', description: '装配已启用的知识资源。' },
  context: { title: '业务上下文', description: '应用当前智能体的上下文策略。' }
}

/** 将正式模块状态转为开发事项；未启用模块不冒充已交付能力，文件记录仍保留在详情中。 */
export function agentDevelopmentPresentation(agent: RuntimeAgent) {
  const development = agent.development
  const modules = development?.modules || []
  const progress = development?.progress
  const execution = development?.execution
  const completed = progress?.initialDevelopmentStatus === 'completed'
  const changed = completed && progress.changeStatus === 'changed'
  const unavailable = completed && progress.changeStatus === 'unavailable'
  const items = modules.filter((item) => !['tools', 'skills', 'knowledge'].includes(item.module)
    || agent.agentSettings[item.module as keyof RuntimeAgent['agentSettings']].enabled === true)
    .map((item) => ({ ...item, ...descriptions[item.module] }))
  const readyCount = items.filter((item) => ['completed', 'already_satisfied'].includes(item.status)).length
  const failed = execution?.status === 'failed' || items.some((item) => ['failed', 'blocked'].includes(item.status))
  const running = execution?.status === 'running' || progress?.initialDevelopmentStatus === 'in_progress'
    || items.some((item) => item.status === 'running')
  // 当前任务异常优先展示；已登记的产物完成事实单独表达，不覆盖失败或待确认状态。
  const state = failed ? 'failed' : execution?.status === 'awaiting_user' ? 'awaiting_user'
    : execution?.status === 'stopping' ? 'stopping' : execution?.status === 'stopped' ? 'stopped'
      : changed ? 'changed' : unavailable ? 'unavailable' : running ? 'running' : completed ? 'completed' : 'pending'
  const titles: Record<string, string> = {
    failed: '智能体开发需要处理', awaiting_user: '开发任务等待确认', stopping: '正在停止开发任务',
    stopped: '开发任务已停止', changed: '智能体实现需要更新', unavailable: '智能体实现待检查',
    running: '正在开发智能体', completed: '智能体开发已完成', pending: '准备开发智能体'
  }
  const summaries: Record<string, string> = {
    failed: '查看任务原因，保留已完成的开发结果，从原任务继续处理。',
    awaiting_user: '请在当前任务中确认下一步，确认后继续开发。',
    stopping: '正在结束当前任务，请等待任务状态更新。',
    stopped: '查看原任务记录，决定下一步开发安排。',
    changed: '正式配置已变化，请更新实现后再进行调试。',
    unavailable: '暂时无法确认实现与正式配置是否一致，请查看任务记录。',
    running: '正在实现当前智能体的配置与能力，可查看下方开发事项。',
    completed: '开发产物已完成登记，可进入调试，验证实际业务效果。',
    pending: '按已确认的设计生成开发任务，确认后开始实现。'
  }
  return { modules, items, readyCount, completed, changed, unavailable, state,
    title: titles[state], summary: summaries[state], canDebug: state === 'completed' && !execution }
}
