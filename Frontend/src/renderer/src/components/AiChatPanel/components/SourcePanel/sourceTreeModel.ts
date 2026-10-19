import type { WorkspaceTreeNode } from '../../../../service/workspaceTools'

/** 收集浅层目录，保持应用文件面板的默认展开范围。 */
export function collectDirPaths(node: WorkspaceTreeNode | undefined, maxDepth = 2): string[] {
  if (!node || node.kind !== 'directory' || maxDepth <= 0) return []
  return [
    node.path,
    ...(node.children || []).flatMap((child) => collectDirPaths(child, maxDepth - 1))
  ]
}

/** 查找文件的全部祖先目录，供初始选中文件时展开目录。 */
export function ancestorDirPaths(
  tree: WorkspaceTreeNode | undefined,
  targetPath: string
): string[] {
  if (!tree || tree.kind !== 'directory') return []
  for (const child of tree.children || []) {
    if (child.path === targetPath) return [tree.path]
    const ancestors = ancestorDirPaths(child, targetPath)
    if (ancestors.length) return [tree.path, ...ancestors]
  }
  return []
}

/** 合并已读取的子树，保留新快照未下探到的后代，避免折叠其他已展开目录。 */
export function mergeSourceSubtree(
  tree: WorkspaceTreeNode,
  subtree: WorkspaceTreeNode
): WorkspaceTreeNode {
  if (tree.path === subtree.path) {
    const previous = new Map((tree.children || []).map((child) => [child.path, child]))
    return {
      ...subtree,
      children: subtree.children?.map((child) => {
        const existing = previous.get(child.path)
        // 深度边界没有下探的目录继续保留缓存；当前被请求目录的空列表仍是权威空态。
        return existing?.kind === 'directory' &&
          child.kind === 'directory' &&
          !child.children?.length
          ? { ...child, children: existing.children }
          : existing
            ? mergeSourceSubtree(existing, child)
            : child
      })
    }
  }
  if (!tree.children) return tree
  return { ...tree, children: tree.children.map((child) => mergeSourceSubtree(child, subtree)) }
}
