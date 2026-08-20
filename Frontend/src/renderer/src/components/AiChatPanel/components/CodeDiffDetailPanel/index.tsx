import { CloseOutlined, CodeOutlined } from '@ant-design/icons'
import { Button, Empty, Typography } from 'antd'
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
import { useEffect, useMemo, useRef, type ReactElement, type ReactNode } from 'react'
import { Diff, parseDiff, type FileData, type GutterOptions } from 'react-diff-view'
import 'react-diff-view/style/index.css'
import type { WorkspaceCodeChangeFile, WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import {
  groupWorkspaceCodeChanges,
  splitWorkspaceCodeChanges,
  splitWorkspacePath,
  summarizeWorkspaceCodeChanges
} from '../../utils'
import './CodeDiffDetailPanel.less'

const { Text, Title } = Typography

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)

type Props = {
  codeChanges: WorkspaceCodeChangeSet
  selectedPath?: string
  onClose: () => void
}

/** 连续展示一次变更集中的全部用户代码文件，并定位被点击的文件。 */
export default function CodeDiffDetailPanel({
  codeChanges,
  selectedPath,
  onClose
}: Props): ReactElement {
  const bodyRef = useRef<HTMLDivElement>(null)
  const fileRefs = useRef<Map<string, HTMLElement>>(new Map())
  const groupedFiles = useMemo(
    () => groupWorkspaceCodeChanges(codeChanges.files),
    [codeChanges.files]
  )
  const summary = useMemo(() => summarizeWorkspaceCodeChanges(groupedFiles), [groupedFiles])
  const parsedFiles = useMemo(
    () =>
      groupedFiles.map((file) => ({
        file,
        pathParts: splitWorkspacePath(file.path),
        parsedChanges: file.changes.map((change) => parseChangeDiff(change))
      })),
    [groupedFiles]
  )
  const parsedSections = useMemo(
    () => splitWorkspaceCodeChanges(parsedFiles, (entry) => entry.file.path),
    [parsedFiles]
  )
  const activePath = groupedFiles.some((file) => file.path === selectedPath)
    ? selectedPath
    : groupedFiles[0]?.path

  useEffect(() => {
    if (!activePath) return
    const body = bodyRef.current
    const target = fileRefs.current.get(activePath)
    if (!body || !target) return
    body.scrollTo({ top: Math.max(target.offsetTop - 1, 0), behavior: 'smooth' })
  }, [activePath, parsedFiles])

  return (
    <section className={cx('code-diff-detail-panel')}>
      <header className={cx('code-diff-detail-header')}>
        <div className={cx('code-diff-detail-title')}>
          <Title level={4}>代码变更审阅</Title>
          <div className={cx('code-diff-summary')}>
            <span className={cx('code-diff-file-count')}>{summary.files} 个文件</span>
            <span className={cx('addition')}>+{summary.additions}</span>
            <span className={cx('deletion')}>-{summary.deletions}</span>
          </div>
        </div>
        <Button
          aria-label="关闭代码变更详情"
          className={cx('code-diff-close-button')}
          icon={<CloseOutlined />}
          onClick={onClose}
          type="text"
        />
      </header>

      <div className={cx('code-diff-body')} ref={bodyRef}>
        {parsedFiles.length === 0 ? (
          <Empty description="暂无可展示的用户代码变更" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className={cx('code-diff-file-stack')}>
            {parsedSections.map((section) => (
              <div className={cx('code-diff-file-section')} key={section.title}>
                <div className={cx('code-diff-file-section-title')}>
                  <span>{section.title}</span>
                  <span className={cx('code-diff-file-section-count')}>
                    {section.files.length}
                  </span>
                </div>
                {section.files.map(({ file, parsedChanges, pathParts }) => (
                  <article
                    aria-label={`${file.path} 文件变更`}
                    className={cx('code-diff-file', file.path === activePath && 'selected')}
                    key={file.path}
                    ref={(node) => {
                      if (node) fileRefs.current.set(file.path, node)
                      else fileRefs.current.delete(file.path)
                    }}
                  >
                    <div className={cx('code-diff-active-file')}>
                      <div className={cx('code-diff-file-path')} title={file.path}>
                        {pathParts.directory && (
                          <span className={cx('code-diff-file-directory')}>
                            <span>{pathParts.directory}/</span>
                          </span>
                        )}
                        <Text className={cx('code-diff-file-name')} strong>
                          {pathParts.fileName}
                        </Text>
                      </div>
                      <Text className={cx('code-diff-file-stats')} type="secondary">
                        <span className={cx('addition')}>+{file.additions}</span>
                        <span className={cx('deletion')}>-{file.deletions}</span>
                      </Text>
                    </div>

                    {file.changes.map((change, changeIndex) => (
                      <div className={cx('code-diff-block')} key={`${change.id}-${changeIndex}`}>
                        {file.changes.length > 1 && (
                          <div className={cx('code-diff-block-title')}>
                            <CodeOutlined />
                            <Text type="secondary">PATCH {changeIndex + 1}</Text>
                          </div>
                        )}
                        {change.binary ? (
                          <div className={cx('code-diff-empty')}>
                            <Text type="secondary">二进制文件不展示文本 Diff。</Text>
                          </div>
                        ) : parsedChanges[changeIndex].length > 0 ? (
                          <HighlightedDiff files={parsedChanges[changeIndex]} path={file.path} />
                        ) : change.diff ? (
                          <pre className={cx('code-diff-raw')}>{change.diff}</pre>
                        ) : (
                          <div className={cx('code-diff-empty')}>
                            <Text type="secondary">此文件没有文本行级变更。</Text>
                          </div>
                        )}
                        {change.truncated && (
                          <Text className={cx('code-diff-truncated')} type="secondary">
                            Diff 内容过长，已截断。
                          </Text>
                        )}
                      </div>
                    ))}
                  </article>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

type HighlightedDiffProps = {
  files: FileData[]
  path: string
}

function HighlightedDiff({ files, path }: HighlightedDiffProps): ReactElement {
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const language = languageFromPath(path)
    rootRef.current?.querySelectorAll<HTMLElement>('.diff-code').forEach((element) => {
      const code = element.textContent ?? ''
      element.innerHTML = language
        ? hljs.highlight(code, { language, ignoreIllegals: true }).value
        : hljs.highlightAuto(code).value
      element.classList.add('hljs')
    })
  }, [files, path])

  return (
    <div className={cx('code-diff-view')} ref={rootRef}>
      {files.map((file, fileIndex) => (
        <Diff
          diffType={file.type}
          hunks={file.hunks}
          key={`${file.oldPath}-${file.newPath}-${fileIndex}`}
          renderGutter={renderSingleGutter}
          viewType="unified"
        />
      ))}
    </div>
  )
}

function renderSingleGutter({ change, side }: GutterOptions): ReactNode {
  if (side === 'old') return null
  if (change.type === 'normal') return change.newLineNumber
  return change.lineNumber
}

function parseChangeDiff(change: WorkspaceCodeChangeFile): FileData[] {
  if (!change.diff) return []
  try {
    return parseDiff(normalizeUnifiedDiff(change))
  } catch {
    return []
  }
}

function normalizeUnifiedDiff(change: WorkspaceCodeChangeFile): string {
  if (/^(?:diff --git|--- |@@ )/m.test(change.diff)) return change.diff

  const lines = change.diff.split('\n')
  if (lines.at(-1) === '') lines.pop()
  const oldLines = lines.filter((line) => !line.startsWith('+')).length
  const newLines = lines.filter((line) => !line.startsWith('-')).length
  const oldPath = change.changeType === 'added' ? '/dev/null' : `a/${change.path}`
  const newPath = change.changeType === 'deleted' ? '/dev/null' : `b/${change.path}`
  const oldStart = oldLines === 0 ? 0 : 1
  const newStart = newLines === 0 ? 0 : 1

  return [
    `--- ${oldPath}`,
    `+++ ${newPath}`,
    `@@ -${oldStart},${oldLines} +${newStart},${newLines} @@`,
    ...lines,
    ''
  ].join('\n')
}

function languageFromPath(path: string): string | undefined {
  const extension = path.split('.').pop()?.toLowerCase()
  const languages: Record<string, string> = {
    bash: 'bash',
    css: 'css',
    htm: 'xml',
    html: 'xml',
    java: 'java',
    js: 'javascript',
    json: 'json',
    jsx: 'javascript',
    less: 'css',
    py: 'python',
    sh: 'bash',
    sql: 'sql',
    ts: 'typescript',
    tsx: 'typescript',
    xml: 'xml'
  }
  return extension ? languages[extension] : undefined
}
