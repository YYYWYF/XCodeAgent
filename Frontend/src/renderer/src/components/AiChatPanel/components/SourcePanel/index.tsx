import { LeftOutlined, RightOutlined } from '@ant-design/icons'
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
import { readWorkspaceFile, type WorkspaceTreeNode } from '../../../../service/workspaceTools'
import { cx } from '../../../../utils'
import SourceTree from './SourceTree'
import { ancestorDirPaths } from './sourceTreeModel'
import { useSourceTree } from './useSourceTree'
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
  /** 回看历史版本时传入该版本的 Git tag：目录树与文件内容都按该版本读取。 */
  revision?: string
}

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

/** 转义源码中的 HTML 字符，避免纯文本被解释为页面元素。 */
function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 应用文件面板：左侧可整栏收起的工程目录树+ 右侧内容查看器。 */
export default function SourcePanel({
  workspaceRoot,
  initialFilePath,
  revision
}: Props): ReactElement {
  const {
    tree,
    treeLoading,
    treeError,
    expanded,
    setExpanded,
    branchLoading,
    branchErrors,
    handleToggle,
    loadDirectory
  } = useSourceTree(workspaceRoot, revision)
  const [selectedPath, setSelectedPath] = useState('')
  const [fileReadSequence, setFileReadSequence] = useState(0)
  const [fileContent, setFileContent] = useState('')
  const [fileLoading, setFileLoading] = useState(false)
  const [fileError, setFileError] = useState('')
  const [fileTruncated, setFileTruncated] = useState(false)
  const [initialTried, setInitialTried] = useState(false)
  const [treeCollapsed, setTreeCollapsed] = useState(false)
  // 有指定文件时展开对应祖先目录；没有指定文件时等待用户选择工程文件。
  useEffect(() => {
    if (initialTried || !tree) return
    setInitialTried(true)
    if (initialFilePath) {
      const ancestors = ancestorDirPaths(tree, initialFilePath)
      if (ancestors.length > 0) {
        // 展开到目标文件的祖先目录再加载。
        setExpanded((current) => new Set([...current, ...ancestors]))
        void loadFile(initialFilePath)
        return
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, initialFilePath, initialTried])

  // 仅在选择工程文件后读取正文，沿用工作区及 Git 修订身份。
  useEffect(() => {
    if (!workspaceRoot || !selectedPath) return
    let cancelled = false
    setFileLoading(true)
    setFileError('')
    setFileContent('')
    setFileTruncated(false)
    void readWorkspaceFile({
      workspace_root: workspaceRoot,
      path: selectedPath,
      max_chars: MAX_CHARS,
      revision
    })
      .then((result) => {
        if (cancelled) return
        setFileContent(result.content || '')
        setFileTruncated(result.truncated)
      })
      .catch((error) => {
        if (cancelled) return
        setFileError(error instanceof Error ? error.message : '读取文件失败')
      })
      .finally(() => {
        if (!cancelled) setFileLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [workspaceRoot, selectedPath, revision, fileReadSequence])

  /** 选择文件并清空上一个文件的展示状态，避免切换时闪现旧内容。 */
  const loadFile = (filePath: string): void => {
    if (!workspaceRoot || !filePath) return
    setSelectedPath(filePath)
    setFileReadSequence((current) => current + 1)
    setFileLoading(false)
    setFileError('')
    setFileContent('')
    setFileTruncated(false)
  }

  /** 将目录树选择传给工程文件读取器。 */
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
    <div className={cx('source-panel', treeCollapsed && 'is-tree-collapsed')}>
      {/* 目录树固定在左：可整栏收起为窄条，收起后点窄条展开。 */}
      {treeCollapsed ? (
        <button
          aria-label="展开目录树"
          className={cx('source-panel-tree-rail')}
          onClick={() => setTreeCollapsed(false)}
          title="展开目录树"
          type="button"
        >
          <RightOutlined />
          <span className={cx('source-panel-tree-rail-label')}>目录树</span>
        </button>
      ) : (
        <div className={cx('source-panel-tree')}>
          <header className={cx('source-panel-tree-head')}>
            <Text strong>目录树</Text>
            <button
              aria-label="收起目录树"
              className={cx('source-panel-tree-collapse')}
              onClick={() => setTreeCollapsed(true)}
              title="收起目录树"
              type="button"
            >
              <LeftOutlined />
            </button>
          </header>

          <div className={cx('source-panel-tree-body')}>
            {treeLoading ? (
              <div className={cx('source-panel-tree-loading')}>
                <Spin size="small" />
                <Text type="secondary">正在读取工程目录…</Text>
              </div>
            ) : treeError ? (
              <div className={cx('source-panel-tree-empty')}>
                <Empty
                  description={treeError || '读取工程目录失败'}
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              </div>
            ) : tree ? (
              <ul className={cx('source-tree')}>
                <SourceTree
                  node={tree}
                  expanded={expanded}
                  selectedPath={selectedPath}
                  branchLoading={branchLoading}
                  branchErrors={branchErrors}
                  onToggle={handleToggle}
                  onRetry={(path) => void loadDirectory(path)}
                  onSelectFile={handleSelectFile}
                />
              </ul>
            ) : (
              <div className={cx('source-panel-tree-empty')}>
                <Empty description="未找到前端工程目录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              </div>
            )}
          </div>
        </div>
      )}

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
            <Empty
              description="选择左侧目录树中的文件查看内容"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          </div>
        )}
      </div>
    </div>
  )
}
