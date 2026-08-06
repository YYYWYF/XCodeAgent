import type { BuiltinSkill, ChatMessageSkill, UserSkill, UserSkillCatalog } from '../../typings'

export type SkillCategory = 'user' | 'builtin'
export type CatalogSkill = UserSkill | BuiltinSkill

export const DEFAULT_SKILL_CATEGORY: SkillCategory = 'user'

/** 返回当前分类中经过名称、描述和目录搜索后的技能。 */
export function filterCatalogSkills(
  catalog: UserSkillCatalog | undefined,
  category: SkillCategory,
  query: string
): CatalogSkill[] {
  const skills = category === 'user' ? catalog?.skills || [] : catalog?.builtinSkills || []
  const normalizedQuery = query.trim().toLocaleLowerCase()
  if (!normalizedQuery) return skills
  return skills.filter((skill) =>
    `${skill.name}\n${skill.description}\n${skill.directoryName}`
      .toLocaleLowerCase()
      .includes(normalizedQuery)
  )
}

/** 返回聊天技能菜单允许选择的已开启用户技能。 */
export function enabledUserSkills(skills: UserSkill[]): UserSkill[] {
  return skills.filter((skill) => skill.enabled)
}

/** 按当前已开启目录清理聊天草稿中的失效技能标签。 */
export function reconcileEnabledChatSkills(
  selectedSkills: ChatMessageSkill[],
  skills: UserSkill[]
): ChatMessageSkill[] {
  const available = new Map(
    enabledUserSkills(skills).map((skill) => [skill.name, skill.description] as const)
  )
  return selectedSkills
    .filter((skill) => available.has(skill.name))
    .map((skill) => ({
      name: skill.name,
      description: available.get(skill.name) || skill.description
    }))
}
