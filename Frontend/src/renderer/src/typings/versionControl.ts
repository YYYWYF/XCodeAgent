export type VersionControlFile = {
  path: string
  status: string
  indexStatus: string
  worktreeStatus: string
  staged: boolean
  untracked: boolean
}

export type VersionControlSnapshot = {
  workspaceRoot: string
  repositoryRoot: string
  branch: string
  head: string
  fingerprint: string
  dirty: boolean
  hasStagedChanges: boolean
  files: VersionControlFile[]
  requestedPaths: string[]
  eligiblePaths: string[]
  unavailablePaths: string[]
}

export type VersionControlCommitResult = {
  action: 'commit'
  workspaceRoot: string
  repositoryRoot: string
  commitSha: string
  message: string
  committedPaths: string[]
  remainingDirty: boolean
  snapshot: VersionControlSnapshot
}
