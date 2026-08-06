export type UserSkill = {
  name: string
  description: string
  directoryName: string
  relativePath: string
  updatedAt: string
  version?: string
  enabled: boolean
}

export type BuiltinSkill = {
  name: string
  description: string
  directoryName: string
  relativePath: string
  version?: string
}

export type ChatMessageSkill = {
  name: string
  description: string
}

export type UserSkillIssue = {
  relativePath: string
  code: 'invalid_frontmatter' | 'read_error' | 'symlink_ignored'
  message: string
}

export type UserSkillCatalog = {
  root: string
  skills: UserSkill[]
  builtinRoot: string
  builtinSkills: BuiltinSkill[]
  skippedCount: number
  issues: UserSkillIssue[]
}

export type UserSkillDocument = {
  name: string
  relativePath: string
  content: string
  revision: string
}
