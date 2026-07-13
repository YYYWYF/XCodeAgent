export type UserSkill = {
  name: string
  description: string
  directoryName: string
  relativePath: string
  updatedAt: string
  version?: string
}

export type UserSkillIssue = {
  relativePath: string
  code: 'invalid_frontmatter' | 'read_error' | 'symlink_ignored'
  message: string
}

export type UserSkillCatalog = {
  root: string
  skills: UserSkill[]
  skippedCount: number
  issues: UserSkillIssue[]
}

export type UserSkillDocument = {
  name: string
  relativePath: string
  content: string
  revision: string
}
