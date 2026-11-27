import { useCallback, useEffect, useRef, useState } from 'react'
import { branchConflictReason, mergeBranchNames, suggestNextBranchName } from '../service/applicationBranches'
import { checkRemoteBranch, listRemoteBranches, validateBranchName, validateRepoUrl } from '../service/repositoryBranch'

export type BranchNameValidation = {
  checking: boolean
  error?: string
  existingNames: string[]
  suggestion?: string
  retry: () => void
  validate: () => Promise<boolean>
}

export type BranchNameValidationInput = {
  enabled: boolean
  repoUrl: string
  branchName: string
  localNames: string[]
  suggestionBase: string
  /** 创建应用可继续使用未占用的 v1_0；迭代始终从基线尾号递增。 */
  includeBase?: boolean
}

/** 共用完整分支列表与复检，过期请求不能更新新仓库或新输入的校验结果。 */
export function useBranchNameValidation(input: BranchNameValidationInput): BranchNameValidation {
  const { enabled, repoUrl, branchName, suggestionBase, includeBase } = input
  const repo = repoUrl.trim()
  const localNames = mergeBranchNames(input.localNames, [])
  const [remote, setRemote] = useState<{ repo: string; names: string[]; error?: string }>()
  const [checking, setChecking] = useState(false)
  const [revision, setRevision] = useState(0)
  const sequence = useRef(0)
  const latest = useRef(input)
  latest.current = input

  useEffect(() => {
    const request = ++sequence.current
    setChecking(false)
    if (!enabled || !repo || validateRepoUrl(repo)) return
    setChecking(true)
    // 仓库输入稳定后再查询；名称变化复用完整列表，无需逐字符请求。
    const timer = window.setTimeout(() => {
      void listRemoteBranches({ repoUrl: repo }).then((result) => {
        if (request === sequence.current) setRemote({ repo, names: result.branches })
      }).catch((error: unknown) => {
        if (request === sequence.current) setRemote({ repo, names: [], error: error instanceof Error ? error.message : '无法读取码云分支，请重试。' })
      }).finally(() => {
        if (request === sequence.current) setChecking(false)
      })
    }, 400)
    return () => { window.clearTimeout(timer); sequence.current += 1 }
  }, [enabled, repo, revision])

  const remoteNames = remote?.repo === repo ? remote.names : []
  const existingNames = mergeBranchNames(localNames, remoteNames)
  const queryError = remote?.repo === repo ? remote.error : undefined
  const pending = enabled && Boolean(repo) && !validateRepoUrl(repo) && (checking || remote?.repo !== repo)
  const error = validateBranchName(branchName) || validateRepoUrl(repo) ||
    branchConflictReason(branchName, localNames, remoteNames) || queryError
  const suggestion = !pending && !queryError && !validateRepoUrl(repo)
    ? includeBase && !existingNames.includes(suggestionBase)
      ? suggestionBase
      : suggestNextBranchName(suggestionBase, existingNames)
    : undefined

  /** 点击确认时强制重新读取，失败或输入已变化时中止，不触发后续磁盘操作。 */
  const validate = useCallback(async (): Promise<boolean> => {
    const captured = latest.current
    const name = captured.branchName.trim()
    const targetRepo = captured.repoUrl.trim()
    if (validateBranchName(name) || validateRepoUrl(targetRepo)) return false
    const localNames = mergeBranchNames(captured.localNames, [])
    if (localNames.includes(name)) return false
    if (!targetRepo) return true
    const request = ++sequence.current
    setChecking(true)
    try {
      const result = await checkRemoteBranch({ repoUrl: targetRepo, branchName: name })
      if (request !== sequence.current) return false
      setRemote({ repo: targetRepo, names: result.branches })
      const current = latest.current
      return current.enabled && current.repoUrl.trim() === targetRepo && current.branchName.trim() === name &&
        !mergeBranchNames(current.localNames, []).includes(name) && !result.exists
    } catch (error: unknown) {
      if (request === sequence.current) setRemote({ repo: targetRepo, names: [], error: error instanceof Error ? error.message : '无法读取码云分支，请重试。' })
      return false
    } finally {
      if (request === sequence.current) setChecking(false)
    }
  }, [])

  return { checking: pending, error, existingNames, suggestion, retry: () => setRevision((value) => value + 1), validate }
}
