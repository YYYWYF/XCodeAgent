import { Fragment, type ReactElement, type ReactNode } from 'react'
import { cx } from '../../../../utils'

type Props = {
  content: string
}

/** 将常用 Markdown 行内标记转换成安全的 React 节点，不注入原始 HTML。 */
function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g)
  return tokens.filter(Boolean).map((token, index) => {
    const key = `${keyPrefix}-${index}`
    if (token.startsWith('`') && token.endsWith('`')) {
      return <code key={key}>{token.slice(1, -1)}</code>
    }
    if (token.startsWith('**') && token.endsWith('**')) {
      return <strong key={key}>{token.slice(2, -2)}</strong>
    }
    const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) {
      return (
        <a href={link[2]} key={key} rel="noreferrer" target="_blank">
          {link[1]}
        </a>
      )
    }
    return <Fragment key={key}>{token}</Fragment>
  })
}

/** 将 Markdown 表格的一行拆为单元格，并忽略首尾分隔竖线。 */
function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

/** 判断当前行是否会开启新的块级 Markdown 结构。 */
function startsMarkdownBlock(lines: string[], index: number): boolean {
  const line = lines[index] || ''
  const next = lines[index + 1] || ''
  return (
    !line.trim() ||
    /^```/.test(line) ||
    /^#{1,6}\s+/.test(line) ||
    /^>\s?/.test(line) ||
    /^[-*+]\s+/.test(line) ||
    /^\d+\.\s+/.test(line) ||
    /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line) ||
    (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(next))
  )
}

/** 渲染只读 Markdown 文档，覆盖工作台正式文档使用的标题、列表、表格与代码块。 */
export default function MarkdownPreview({ content }: Props): ReactElement {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }

    if (/^```/.test(line)) {
      const language = line.slice(3).trim()
      const codeLines: string[] = []
      index += 1
      while (index < lines.length && !/^```/.test(lines[index])) {
        codeLines.push(lines[index])
        index += 1
      }
      blocks.push(
        <pre className={cx('markdown-code-block')} key={`code-${index}`}>
          <code data-language={language || undefined}>{codeLines.join('\n')}</code>
        </pre>
      )
      index += 1
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const headingContent = renderInlineMarkdown(heading[2], `heading-${index}`)
      if (level === 1) blocks.push(<h1 key={`heading-${index}`}>{headingContent}</h1>)
      else if (level === 2) blocks.push(<h2 key={`heading-${index}`}>{headingContent}</h2>)
      else blocks.push(<h3 key={`heading-${index}`}>{headingContent}</h3>)
      index += 1
      continue
    }

    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || '')) {
      const headers = tableCells(line)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      blocks.push(
        <div className={cx('markdown-table-scroll')} key={`table-${index}`}>
          <table>
            <thead>
              <tr>
                {headers.map((cell, cellIndex) => (
                  <th key={`head-${cellIndex}`}>
                    {renderInlineMarkdown(cell, `head-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {headers.map((_, cellIndex) => (
                    <td key={`cell-${rowIndex}-${cellIndex}`}>
                      {renderInlineMarkdown(row[cellIndex] || '', `cell-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )
      continue
    }

    const unordered = /^[-*+]\s+/.test(line)
    const ordered = /^\d+\.\s+/.test(line)
    if (unordered || ordered) {
      const items: string[] = []
      const pattern = ordered ? /^\d+\.\s+(.+)$/ : /^[-*+]\s+(.+)$/
      while (index < lines.length) {
        const match = lines[index].match(pattern)
        if (!match) break
        items.push(match[1])
        index += 1
      }
      const children = items.map((item, itemIndex) => (
        <li key={`item-${index}-${itemIndex}`}>
          {renderInlineMarkdown(item, `item-${index}-${itemIndex}`)}
        </li>
      ))
      blocks.push(
        ordered ? <ol key={`list-${index}`}>{children}</ol> : <ul key={`list-${index}`}>{children}</ul>
      )
      continue
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push(
        <blockquote key={`quote-${index}`}>
          {renderInlineMarkdown(quote.join(' '), `quote-${index}`)}
        </blockquote>
      )
      continue
    }

    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push(<hr key={`rule-${index}`} />)
      index += 1
      continue
    }

    const paragraph: string[] = [line.trim()]
    index += 1
    while (index < lines.length && !startsMarkdownBlock(lines, index)) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push(
      <p key={`paragraph-${index}`}>
        {renderInlineMarkdown(paragraph.join(' '), `paragraph-${index}`)}
      </p>
    )
  }

  return <article className={cx('doc-panel-viewer')}>{blocks}</article>
}
