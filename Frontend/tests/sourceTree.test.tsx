import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ComponentProps } from 'react'
import type { WorkspaceTreeNode } from '../src/renderer/src/service/workspaceTools'
import SourceTree from '../src/renderer/src/components/AiChatPanel/components/SourcePanel/SourceTree'
import {
  ancestorDirPaths,
  collectDirPaths,
  mergeSourceSubtree
} from '../src/renderer/src/components/AiChatPanel/components/SourcePanel/sourceTreeModel'

const packagePath = 'backend/src/main/java/com'
const applicationPath = `${packagePath}/cmbchina/backend/Application.java`

/** 构造与 file/tree 工具一致的目录节点，未下探的目录不提供 children。 */
function directory(path: string, children?: WorkspaceTreeNode[]): WorkspaceTreeNode {
  return { path, name: path.split('/').pop() || path, kind: 'directory', children }
}

/** 复现初次读取六层目录后停在 com 的真实数据形态。 */
function initialTree(): WorkspaceTreeNode {
  return directory('.', [
    directory('backend', [
      directory('backend/src', [
        directory('backend/src/main', [
          directory('backend/src/main/java', [directory(packagePath)])
        ])
      ])
    ]),
    directory('frontend', [directory('frontend/src')])
  ])
}

/** 构造目录渲染的默认事件与状态，测试可以覆盖正在加载、失败或选中状态。 */
function treeProps(node: WorkspaceTreeNode): ComponentProps<typeof SourceTree> {
  return {
    node,
    expanded: new Set(ancestorDirPaths(node, applicationPath)),
    selectedPath: applicationPath,
    branchLoading: new Set<string>(),
    branchErrors: {} as Record<string, string>,
    onToggle: (): void => undefined,
    onRetry: (): void => undefined,
    onSelectFile: (): void => undefined
  }
}

test('按需补读 com 后可继续展开任意深度的包，并选中 Java 文件', () => {
  const original = initialTree()
  let tree = mergeSourceSubtree(
    original,
    directory(packagePath, [directory(`${packagePath}/cmbchina`)])
  )
  tree = mergeSourceSubtree(
    tree,
    directory(`${packagePath}/cmbchina`, [directory(`${packagePath}/cmbchina/backend`)])
  )
  tree = mergeSourceSubtree(
    tree,
    directory(`${packagePath}/cmbchina/backend`, [
      { path: applicationPath, name: 'Application.java', kind: 'file' }
    ])
  )
  assert.deepEqual(ancestorDirPaths(tree, applicationPath), [
    '.',
    'backend',
    'backend/src',
    'backend/src/main',
    'backend/src/main/java',
    packagePath,
    `${packagePath}/cmbchina`,
    `${packagePath}/cmbchina/backend`
  ])
  const html = renderToStaticMarkup(<SourceTree {...treeProps(tree)} />)
  assert.ok(html.includes('Application.java'))
  assert.ok(html.includes('is-selected'))
  assert.ok(html.includes('aria-expanded="true"'))
  assert.deepEqual(ancestorDirPaths(original, applicationPath), [], '合并不能修改原始树')
})

test('补读父目录时保留已加载的深层后代与其他源码分支', () => {
  const javaTree = directory('backend', [
    directory('backend/src', [{ path: applicationPath, name: 'Application.java', kind: 'file' }])
  ])
  const tree = directory('.', [javaTree, directory('frontend', [directory('frontend/src')])])
  const merged = mergeSourceSubtree(tree, directory('backend', [directory('backend/src')]))
  assert.deepEqual(merged, tree)
  assert.ok(ancestorDirPaths(merged, applicationPath).length)
})

test('直接读取到的空目录清除旧子节点，不将空态误认为未加载', () => {
  const tree = directory('.', [directory('backend', [directory('backend/removed')])])
  const merged = mergeSourceSubtree(tree, directory('backend', []))
  assert.deepEqual(merged.children?.[0].children, [])
})

test('不同目录的回执依次合并，均保留在最新树中', () => {
  let tree = initialTree()
  tree = mergeSourceSubtree(
    tree,
    directory('frontend/src', [{ path: 'frontend/src/App.tsx', name: 'App.tsx', kind: 'file' }])
  )
  tree = mergeSourceSubtree(tree, directory(packagePath, [directory(`${packagePath}/cmbchina`)]))
  assert.ok(ancestorDirPaths(tree, 'frontend/src/App.tsx').length)
  assert.ok(ancestorDirPaths(tree, `${packagePath}/cmbchina`).length)
  assert.deepEqual(collectDirPaths(tree), ['.', 'backend', 'frontend'])
})

test('目录读取中显示忙状态，失败时在当前目录下提供重试入口', () => {
  const node = directory(packagePath)
  const loading = renderToStaticMarkup(
    <SourceTree
      {...treeProps(node)}
      expanded={new Set([packagePath])}
      branchLoading={new Set([packagePath])}
    />
  )
  assert.ok(loading.includes('aria-busy="true"'))
  const failed = renderToStaticMarkup(
    <SourceTree
      {...treeProps(node)}
      expanded={new Set([packagePath])}
      branchErrors={{ [packagePath]: '连接失败' }}
    />
  )
  assert.ok(failed.includes('读取失败，点击重试'))
  assert.ok(failed.includes('连接失败'))
})
