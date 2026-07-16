import type { ChatMessageSkill } from '../../typings'

export type OptimisticSkillSelection = {
  messageSkills?: ChatMessageSkill[]
  nextDraftSkills: ChatMessageSkill[]
}

/** 规范化技能标签快照，按首次出现顺序去空白并去重。 */
export function normalizeChatSkills(skills: ChatMessageSkill[]): ChatMessageSkill[] {
  const names = new Set<string>()
  return skills
    .map((skill) => ({
      name: skill.name.trim(),
      description: skill.description.trim()
    }))
    .filter((skill) => {
      if (!skill.name || names.has(skill.name)) return false
      names.add(skill.name)
      return true
    })
}

/** 生成发送时的消息快照，并清空下一份会话草稿技能。 */
export function beginOptimisticSkillSend(
  skills: ChatMessageSkill[]
): OptimisticSkillSelection {
  const normalized = normalizeChatSkills(skills)
  return {
    messageSkills: normalized.length > 0 ? normalized : undefined,
    nextDraftSkills: []
  }
}

/** 认证失败后生成一份独立副本，供草稿标签安全回滚。 */
export function rollbackSkillSelection(skills?: ChatMessageSkill[]): ChatMessageSkill[] {
  return normalizeChatSkills(skills || [])
}

/** 从技能展示快照生成传给 Python Workflow 的稳定名称数组。 */
export function selectedSkillNames(skills?: ChatMessageSkill[]): string[] | undefined {
  const names = normalizeChatSkills(skills || []).map((skill) => skill.name)
  return names.length > 0 ? names : undefined
}
