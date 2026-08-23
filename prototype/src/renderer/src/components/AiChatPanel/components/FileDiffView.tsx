import hljs from 'highlight.js/lib/core'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import './FileDiffView.less'

hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)

/** 按文件扩展名推断高亮语言；未识别时按纯文本展示。 */
function languageForFile(path: string): string | undefined {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  if (['ts', 'tsx'].includes(ext)) return 'typescript'
  if (['js', 'jsx'].includes(ext)) return 'javascript'
  if (ext === 'json') return 'json'
  if (ext === 'java') return 'java'
  if (ext === 'md') return 'markdown'
  if (['html', 'xml', 'svg'].includes(ext)) return 'xml'
  return undefined
}

/** 单行高亮并输出 HTML；失败或无语言时退化为转义文本。 */
function highlightLine(line: string, language: string | undefined): string {
  const text = /^[+-]/.test(line) ? line.slice(1) : line
  try {
    if (language) return hljs.highlight(text, { language }).value
  } catch {
    // 高亮失败按纯文本兜底。
  }
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

type DiffLineKind = 'insert' | 'delete' | 'context'

/** 根据统一 Diff 行首标记识别新增、删除和上下文行。 */
function diffLineKind(line: string): DiffLineKind {
  if (line.startsWith('+')) return 'insert'
  if (line.startsWith('-')) return 'delete'
  return 'context'
}

export type PendingFileDiff = {
  path: string
  /** bare diff 内容：每行以 + 开头（新增行）。 */
  diff: string
  additions: number
}

type Props = {
  diff: PendingFileDiff
}

/** 单文件 Diff 视图：编辑状态中的一个行为，嵌入对应文件页签（文档/源码）。
 * 与多文件审阅面板不同，这里只呈现“当前文件正在被写入”的绿色新增行，
 * 用户在对话区“接受”后即恢复该文件的编辑/浏览状态。
 * 文件路径与变更统计不在此重复展示：路径由项目目录树定位，统计在对话区卡片。 */
export default function FileDiffView({ diff }: Props): ReactElement {
  const lines = diff.diff.split('\n')
  const language = languageForFile(diff.path)
  return (
    <div className={cx('file-diff-view')}>
      <div className={cx('file-diff-view-body')}>
        <table className="diff">
          <tbody>
            {lines.map((line, index) => {
              const kind = diffLineKind(line)
              const marker = kind === 'insert' ? '+' : kind === 'delete' ? '−' : ' '
              return (
                <tr className={'diff-line diff-line-' + kind} key={index}>
                  <td className={'diff-gutter diff-gutter-' + kind}>
                    <span className="diff-line-marker">{marker}</span>
                    {index + 1}
                  </td>
                  <td className={'diff-code diff-code-' + kind}>
                    <code
                      className="hljs"
                      dangerouslySetInnerHTML={{ __html: highlightLine(line, language) }}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
