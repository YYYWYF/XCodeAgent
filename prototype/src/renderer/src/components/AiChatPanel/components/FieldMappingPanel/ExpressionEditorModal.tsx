import { Button, Modal } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import Editor from 'react-simple-code-editor'
import Prism from 'prismjs'
import { cx } from '../../../../utils'
import './expressionEditor.less'

/** 表达式可用的函数目录：签名、说明与点击插入的示例片段。 */
const EXPRESSION_FUNCTIONS: Array<{
  name: string
  signature: string
  desc: string
  example: string
}> = [
  {
    name: 'LOOKUP',
    signature: 'LOOKUP(表, 匹配字段, 返回字段)',
    desc: '以当前值在指定数据表查译：按匹配字段定位行，返回目标字段的值。',
    example: 'LOOKUP(user, id, name)'
  },
  {
    name: 'DEFAULT',
    signature: 'DEFAULT(表达式, 默认值)',
    desc: '表达式取不到值时使用默认值，否则原值透传。',
    example: "DEFAULT(:status, '全部')"
  },
  {
    name: 'CONCAT',
    signature: 'CONCAT(值1, 值2, …)',
    desc: '把多个值按顺序拼接成一个字符串。',
    example: "CONCAT(:projectName, '-', :currentUser)"
  },
  {
    name: 'IF',
    signature: 'IF(条件, 真值, 假值)',
    desc: '条件成立取真值，否则取假值，支持 ==、!=、>、< 比较。',
    example: "IF(:status == '待处理', 1, 0)"
  },
  {
    name: 'UPPER',
    signature: 'UPPER(表达式)',
    desc: '把文本转为大写，常用于编码归一。',
    example: 'UPPER(:code)'
  }
]

/** 表达式语言的 Prism 语法：函数调用、:变量、字符串、数字与括号。 */
Prism.languages.fxexpr = {
  comment: {
    pattern: /\/\/.*/,
    greedy: true
  },
  string: {
    pattern: /'[^']*'|"[^"]*"/,
    greedy: true
  },
  function: /\b(LOOKUP|DEFAULT|CONCAT|IF|UPPER|LOWER|TRIM|ROUND|LENGTH)(?=\s*\()/,
  variable: /:[a-zA-Z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*/,
  number: /\b\d+(?:\.\d+)?\b/,
  punctuation: /[(),]/,
  operator: /[+\-*/><=!]/
}

/** 语法高亮：交给 react-simple-code-editor 渲染。 */
function highlight(code: string): string {
  return Prism.highlight(code, Prism.languages.fxexpr, 'fxexpr')
}

const KNOWN_FUNCTIONS = new Set(EXPRESSION_FUNCTIONS.map((item) => item.name))

/** 轻校验：括号配平与未知函数提醒；通过则返回 null。 */
function validate(code: string): string | null {
  let depth = 0
  for (const char of code) {
    if (char === '(') depth += 1
    if (char === ')') depth -= 1
    if (depth < 0) return '括号不匹配：多余的右括号。'
  }
  if (depth !== 0) return '括号不匹配：还有未闭合的左括号。'
  const calls = code.matchAll(/([A-Za-z_][\w]*)\s*\(/g)
  for (const match of calls) {
    if (!KNOWN_FUNCTIONS.has(match[1])) return `未知函数 ${match[1]}，请从左侧函数目录选择。`
  }
  return null
}

/** 可插入的变量候选：label 展示用途，insert 为写进表达式的文本。 */
export type ExpressionVariable = { label: string; insert: string }

type Props = {
  open: boolean
  /** 正在配置的字段（契约出入参），用于标题与说明。 */
  paramLabel: string
  value: string
  variables: ExpressionVariable[]
  onCancel: () => void
  onOk: (value: string) => void
}

/**
 * 函数表达式编辑弹框：API 适配数据加工的核心维护界面。
 * 左侧是函数目录与可用变量（点击插入到光标处），右侧是基于 Prism 语法的
 * 高亮代码编辑器（react-simple-code-editor），底部实时校验括号配平与未知函数；
 * 清空表达式即回到直接透传。确定后写回映射草稿。
 */
export default function ExpressionEditorModal({
  open,
  paramLabel,
  value,
  variables,
  onCancel,
  onOk
}: Props): ReactElement {
  const [code, setCode] = useState('')
  const cursorRef = useRef(0)
  useEffect(() => {
    if (open) {
      setCode(value || '')
      cursorRef.current = (value || '').length
    }
  }, [open, value])

  /** 在最近的光标位置插入片段；光标跟随到片段末尾。 */
  const insert = (snippet: string): void => {
    setCode((current) => {
      const position = Math.min(cursorRef.current, current.length)
      const next = current.slice(0, position) + snippet + current.slice(position)
      cursorRef.current = position + snippet.length
      return next
    })
  }
  /** 记录光标：点击、按键与聚焦时同步，保证插入位置符合直觉。 */
  const trackCursor = (event: React.SyntheticEvent): void => {
    const target = event.target as HTMLTextAreaElement
    if (target instanceof HTMLTextAreaElement) {
      cursorRef.current = target.selectionStart ?? 0
    }
  }

  const error = validate(code)

  return (
    <Modal
      cancelText="取消"
      okButtonProps={{ disabled: Boolean(error) }}
      okText="确定"
      onCancel={onCancel}
      onOk={() => onOk(code.trim())}
      visible={open}
      title={`配置函数表达式 · ${paramLabel}`}
      width={720}
      footer={[
        <Button key="clear" onClick={() => setCode('')}>
          清空（透传）
        </Button>,
        <Button key="cancel" onClick={onCancel}>
          取消
        </Button>,
        <Button
          disabled={Boolean(error)}
          key="ok"
          onClick={() => onOk(code.trim())}
          type="primary"
        >
          确定
        </Button>
      ]}
    >
      <div className={cx('fx-editor-layout')}>
        <aside aria-label="函数与变量目录" className={cx('fx-editor-catalog')}>
          <div className={cx('fx-editor-catalog-title')}>函数</div>
          {EXPRESSION_FUNCTIONS.map((item) => (
            <button
              key={item.name}
              className={cx('fx-editor-catalog-item')}
              onClick={() => insert(item.example)}
              title={`${item.signature} — ${item.desc}`}
              type="button"
            >
              <code>{item.name}</code>
              <small>{item.desc}</small>
            </button>
          ))}
          <div className={cx('fx-editor-catalog-title')}>变量</div>
          {variables.map((item) => (
            <button
              key={item.insert}
              className={cx('fx-editor-catalog-item')}
              onClick={() => insert(item.insert)}
              title={`插入 ${item.insert}`}
              type="button"
            >
              <code>{item.insert}</code>
              <small>{item.label}</small>
            </button>
          ))}
        </aside>
        <div className={cx('fx-editor-main')}>
          <div className={cx('fx-editor-surface')}>
            <Editor
              highlight={highlight}
              onValueChange={(next) => setCode(next)}
              placeholder="留空表示直接透传；输入表达式做加工，如 LOOKUP(user, id, name)"
              textareaClassName={cx('fx-editor-textarea')}
              value={code}
              onClick={trackCursor}
              onKeyUp={trackCursor}
              onMouseUp={trackCursor}
              onFocus={trackCursor}
            />
          </div>
          <div className={cx('fx-editor-meta')}>
            {error ? (
              <span className={cx('fx-editor-error')}>{error}</span>
            ) : (
              <span className={cx('fx-editor-hint')}>
                适配当前行的取值：出参适配时表达式处理的是外部出参取回的原始值；留空即透传。
              </span>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
