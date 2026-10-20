import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { readWorkspaceTree, type WorkspaceTreeNode } from '../../../../service/workspaceTools'
import { collectDirPaths, mergeSourceSubtree } from './sourceTreeModel'

const TREE_LIMIT = 2000

type TreeScope = {
  loaded: Set<string>
  pending: Set<string>
}

type SourceTreeState = {
  tree?: WorkspaceTreeNode
  treeLoading: boolean
  treeError: string
  expanded: Set<string>
  setExpanded: Dispatch<SetStateAction<Set<string>>>
  branchLoading: Set<string>
  branchErrors: Record<string, string>
  handleToggle: (path: string) => void
  loadDirectory: (path: string) => Promise<void>
}

/** 读取初始目录并按需展开子树，隔离工作区切换或卸载后的异步回执。 */
export function useSourceTree(workspaceRoot: string, revision?: string): SourceTreeState {
  const [tree, setTree] = useState<WorkspaceTreeNode>()
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeError, setTreeError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [branchLoading, setBranchLoading] = useState<Set<string>>(new Set())
  const [branchErrors, setBranchErrors] = useState<Record<string, string>>({})
  const scopeRef = useRef<TreeScope>()

  useEffect(() => {
    const scope: TreeScope = { loaded: new Set(), pending: new Set() }
    scopeRef.current = scope
    setTree(undefined)
    setTreeError('')
    setExpanded(new Set())
    setBranchLoading(new Set())
    setBranchErrors({})
    setTreeLoading(Boolean(workspaceRoot))
    if (workspaceRoot) {
      void readWorkspaceTree({
        workspace_root: workspaceRoot,
        path: '.',
        max_depth: 6,
        include_hidden: false,
        limit: TREE_LIMIT,
        revision
      })
        .then((result) => {
          if (scopeRef.current !== scope) return
          setTree(result.tree)
          setExpanded(new Set(collectDirPaths(result.tree)))
        })
        .catch((error: unknown) => {
          if (scopeRef.current !== scope) return
          setTreeError(error instanceof Error ? error.message : '读取工程目录失败')
        })
        .finally(() => {
          if (scopeRef.current === scope) setTreeLoading(false)
        })
    }
    return () => {
      scopeRef.current = undefined
    }
  }, [workspaceRoot, revision])

  /** 首次展开时读取直接子目录；失败不登记为已加载，允许用户重试。 */
  const loadDirectory = useCallback(
    async (path: string): Promise<void> => {
      const scope = scopeRef.current
      if (!workspaceRoot || !scope || scope.loaded.has(path) || scope.pending.has(path)) return
      scope.pending.add(path)
      setBranchLoading((current) => new Set([...current, path]))
      setBranchErrors((current) => ({ ...current, [path]: '' }))
      try {
        const result = await readWorkspaceTree({
          workspace_root: workspaceRoot,
          path,
          max_depth: 2,
          include_hidden: false,
          limit: TREE_LIMIT,
          revision
        })
        if (scopeRef.current !== scope) return
        setTree((current) => (current ? mergeSourceSubtree(current, result.tree) : current))
        scope.loaded.add(path)
      } catch (error: unknown) {
        if (scopeRef.current !== scope) return
        setBranchErrors((current) => ({
          ...current,
          [path]: error instanceof Error ? error.message : '读取目录失败'
        }))
      } finally {
        scope.pending.delete(path)
        if (scopeRef.current === scope) {
          setBranchLoading((current) => {
            const next = new Set(current)
            next.delete(path)
            return next
          })
        }
      }
    },
    [workspaceRoot, revision]
  )

  /** 展开目录时补读子树，折叠仅改变本地展示状态。 */
  const handleToggle = (path: string): void => {
    const isExpanded = expanded.has(path)
    setExpanded((current) => {
      const next = new Set(current)
      if (isExpanded) next.delete(path)
      else next.add(path)
      return next
    })
    if (!isExpanded) void loadDirectory(path)
  }

  return {
    tree,
    treeLoading,
    treeError,
    expanded,
    setExpanded,
    branchLoading,
    branchErrors,
    handleToggle,
    loadDirectory
  }
}
