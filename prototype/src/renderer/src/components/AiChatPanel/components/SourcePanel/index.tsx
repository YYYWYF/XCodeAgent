import { Typography } from 'antd'
import { useMemo, type ReactElement } from 'react'
import hljs from 'highlight.js/lib/core'
import typescript from 'highlight.js/lib/languages/typescript'
import { cx } from '../../../../utils'
import './SourcePanel.less'

hljs.registerLanguage('typescript', typescript)

const { Text } = Typography

type Props = {
  filePath: string
  content: string
}

/** 右侧「源码」面板：当前页面生成的代码，语法高亮展示。 */
export default function SourcePanel({ filePath, content }: Props): ReactElement {
  const highlighted = useMemo(() => {
    if (!content) return ''
    try {
      return hljs.highlight(content, { language: 'typescript' }).value
    } catch {
      return content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    }
  }, [content])

  return (
    <div className={cx('source-panel')}>
      <header className={cx('source-panel-header')}>
        <Text code>{filePath}</Text>
      </header>
      <pre className={cx('source-panel-pre')}>
        <code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted }} />
      </pre>
    </div>
  )
}
