import {
  CaretDownOutlined,
  CaretRightOutlined,
  CodeOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined
} from '@ant-design/icons'
import { Empty, Spin, Typography } from 'antd'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import { useEffect, useMemo, useState, type ReactElement } from 'react'
import {
  readWorkspaceFile,
  readWorkspaceTree,
  type WorkspaceTreeNode
} from '../../../../service/workspaceTools'
import { cx } from '../../../../utils'
import './SourcePanel.less'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)

const { Text } = Typography

type Props = {
  workspaceRoot: string
  initialFilePath?: string
}

const MAX_DEPTH = 6
const TREE_LIMIT = 2000
const MAX_CHARS = 200000

/** 按文件扩展名推断 highlight.js 语言名；无匹配返回 undefined（纯文本）。 */
function languageForFile(name: string): string | undefined {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  switch (ext) {
    case 'ts':
    case 'tsx':
      return 'typescript'
    case 'js':
    case 'jsx':
    case 'mjs':
    case 'cjs':
      return 'javascript'
    case 'json':
      return 'json'
    case 'css':
    case 'less':
    case 'scss':
      return 'css'
    case 'html':
    case 'svg':
    case 'xml':
      return 'xml'
    case 'sh':
    case 'bash':
      return 'bash'
    case 'java':
      return 'java'
    case 'py':
      return 'python'
    case 'sql':
      return 'sql'
    default:
      return undefined
  }
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 收集树中所有目录节点的 path，用于默认全部展开（浅层）。 */
function collectDirPaths(node: WorkspaceTreeNode | undefined, maxDepth = 2): string[] {
  if (!node || node.kind !== 'directory' || maxDepth <= 0) return []
  const paths = [node.path]
  for (const child of node.children || []) {
    if (child.kind === 'directory') {
      paths.push(...collectDirPaths(child, maxDepth - 1))
    }
  }
  return paths
}

/** 在树中查找指定 path 的文件节点的所有祖先目录 path，用于展开到该文件。 */
function ancestorDirPaths(tree: WorkspaceTreeNode | undefined, targetPath: string): string[] {
  if (!tree) return []
  const result: string[] = []
  const walk = (node: WorkspaceTreeNode, ancestors: string[]): boolean => {
    if (node.path === targetPath) return true
    if (node.kind === 'directory') {
      for (const child of node.children || []) {
        if (walk(child, [...ancestors, node.path])) {
          result.push(...ancestors, node.path)
          return true
        }
      }
    }
    return false
  }
  walk(tree, [])
  return Array.from(new Set(result))
}

/** 递归渲染目录树节点。 */
function renderTreeNodes(
  node: WorkspaceTreeNode,
  depth: number,
  expanded: Set<string>,
  selectedPath: string,
  onToggle: (path: string) => void,
  onSelectFile: (node: WorkspaceTreeNode) => void
): ReactElement {
  const isDir = node.kind === 'directory'
  const isExpanded = expanded.has(node.path)
  const isSelected = node.path === selectedPath
  const paddingLeft = 8 + depth * 14

  return (
    <li className={cx('source-tree-node')} key={node.path}>
      <button
        className={cx(
          'source-tree-row',
          isDir && 'is-dir',
          isSelected && 'is-selected'
        )}
        onClick={() => (isDir ? onToggle(node.path) : onSelectFile(node))}
        style={{ paddingLeft: `${paddingLeft}px` }}
        title={node.name}
        type="button"
      >
        {isDir ? (
          <>
            {isExpanded ? <CaretDownOutlined /> : <CaretRightOutlined />}
            {isExpanded ? <FolderOpenOutlined /> : <FolderOutlined />}
          </>
        ) : (
          <>
            <span className={cx('source-tree-leaf-spacer')} aria-hidden="true" />
            {/\.tsx?$|\.jsx?$|\.json$|\.css$|\.less$|\.scss$|\.html?$|\.md$/.test(node.name) ? (
              <CodeOutlined />
            ) : (
              <FileOutlined />
            )}
          </>
        )}
        <span className={cx('source-tree-name')}>{node.name}</span>
      </button>
      {isDir && isExpanded && node.children && node.children.length > 0 ? (
        <ul className={cx('source-tree-children')}>
          {node.children
            .slice()
            .sort((a, b) => {
              // 目录在前，文件在后；同类按名称排序
              const aDir = a.kind === 'directory' ? 0 : 1
              const bDir = b.kind === 'directory' ? 0 : 1
              if (aDir !== bDir) return aDir - bDir
              return a.name.localeCompare(b.name)
            })
            .map((child) =>
              renderTreeNodes(child, depth + 1, expanded, selectedPath, onToggle, onSelectFile)
            )}
        </ul>
      ) : null}
    </li>
  )
}

/** 右侧「源码」面板：模板工程文件浏览器（左目录树 + 右代码查看器）。 */
export default function SourcePanel({ workspaceRoot, initialFilePath }: Props): ReactElement {
  const [tree, setTree] = useState<WorkspaceTreeNode | undefined>()
  const [treeLoading, setTreeLoading] = useState(false)
  const [treeError, setTreeError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [selectedPath, setSelectedPath] = useState('')
  const [fileContent, setFileContent] = useState('')
  const [fileLoading, setFileLoading] = useState(false)
  const [fileError, setFileError] = useState('')
  const [fileTruncated, setFileTruncated] = useState(false)
  const [initialTried, setInitialTried] = useState(false)

  // 加载工作区工程目录树（frontend + backend 等），后端自动过滤 .xcodeagent/node_modules
  useEffect(() => {
    if (!workspaceRoot) {
      setTree(undefined)
      setTreeError('')
      return
    }
    let cancelled = false
    setTreeLoading(true)
    setTreeError('')
    void readWorkspaceTree({
      workspace_root: workspaceRoot,
      path: '.',
      max_depth: MAX_DEPTH,
      include_hidden: false,
      limit: TREE_LIMIT
    })
      .then((result) => {
        if (cancelled) return
        setTree(result.tree)
        // 默认展开前两层目录（根 + frontend/backend/src 等）
        setExpanded(new Set(collectDirPaths(result.tree, 2)))
      })
      .catch((error) => {
        if (cancelled) return
        setTree(undefined)
        setTreeError(error instanceof Error ? error.message : '读取工程目录失败')
      })
      .finally(() => {
        if (!cancelled) setTreeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceRoot])

  // 树加载后尝试选中 initialFilePath
  useEffect(() => {
    if (initialTried || !tree || !initialFilePath) return
    setInitialTried(true)
    // 展开到目标文件的祖先目录
    const ancestors = ancestorDirPaths(tree, initialFilePath)
    if (ancestors.length > 0) {
      setExpanded((current) => new Set([...current, ...ancestors]))
      void loadFile(initialFilePath)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, initialFilePath, initialTried])

  const loadFile = (filePath: string): void => {
    if (!workspaceRoot || !filePath) return
    setSelectedPath(filePath)
    setFileLoading(true)
    setFileError('')
    setFileContent('')
    setFileTruncated(false)
    void readWorkspaceFile({
      workspace_root: workspaceRoot,
      path: filePath,
      max_chars: MAX_CHARS
    })
      .then((result) => {
        setFileContent(result.content || '')
        setFileTruncated(result.truncated)
      })
      .catch((error) => {
        setFileError(error instanceof Error ? error.message : '读取文件失败')
      })
      .finally(() => setFileLoading(false))
  }

  const handleToggle = (path: string): void => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const handleSelectFile = (node: WorkspaceTreeNode): void => {
    loadFile(node.path)
  }

  const highlighted = useMemo(() => {
    if (!fileContent) return ''
    const lang = selectedPath ? languageForFile(selectedPath) : undefined
    try {
      if (lang) return hljs.highlight(fileContent, { language: lang }).value
    } catch {
      // fallback 到纯文本
    }
    return escapeHtml(fileContent)
  }, [fileContent, selectedPath])

  return (
    <div className={cx('source-panel')}>
      <div className={cx('source-panel-tree')}>
        {treeLoading ? (
          <div className={cx('source-panel-tree-loading')}>
            <Spin size="small" />
            <Text type="secondary">正在读取工程目录…</Text>
          </div>
        ) : treeError ? (
          <div className={cx('source-panel-tree-empty')}>
            <Empty description={treeError || '读取工程目录失败'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : tree ? (
          <ul className={cx('source-tree')}>{renderTreeNodes(tree, 0, expanded, selectedPath, handleToggle, handleSelectFile)}</ul>
        ) : (
          <div className={cx('source-panel-tree-empty')}>
            <Empty description="未找到前端工程目录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        )}
      </div>

      <div className={cx('source-panel-viewer')}>
        <header className={cx('source-panel-header')}>
          <Text code>{selectedPath || '未选择文件'}</Text>
          {fileTruncated ? (
            <Text className={cx('source-panel-truncated')} type="warning">
              文件过大，已截断
            </Text>
          ) : null}
        </header>
        {fileLoading ? (
          <div className={cx('source-panel-viewer-loading')}>
            <Spin size="small" />
            <Text type="secondary">正在读取文件…</Text>
          </div>
        ) : fileError ? (
          <div className={cx('source-panel-viewer-empty')}>
            <Empty description={fileError} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : fileContent ? (
          <pre className={cx('source-panel-pre')}>
            <code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted }} />
          </pre>
        ) : (
          <div className={cx('source-panel-viewer-empty')}>
            <Empty description="选择左侧文件查看内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        )}
      </div>
    </div>
  )
}
