import { forwardRef, type PropsWithChildren, type ReactElement } from 'react'

/** 只替代浏览器布局边界，真实面板、磁盘订阅和交互逻辑保持原样。 */
export default function Container({ children }: PropsWithChildren): ReactElement {
  return <div>{children}</div>
}

/** 保留按钮的禁用状态和点击回调，便于验证确认门。 */
export function Button({
  children,
  disabled,
  onClick
}: PropsWithChildren<{
  disabled?: boolean
  onClick?: () => void
}>): ReactElement {
  return (
    <button disabled={disabled} onClick={onClick}>
      {children}
    </button>
  )
}

/** 关闭的 portal 不应渲染到无 DOM 测试中。 */
export function Modal({
  open,
  children
}: PropsWithChildren<{ open: boolean }>): ReactElement | null {
  return open ? <div>{children}</div> : null
}

/** 文本输入在该回归中只保留受控值，不模拟浏览器排版。 */
const TextArea = forwardRef<HTMLTextAreaElement, { value?: string }>(function TextArea(
  { value },
  ref
): ReactElement {
  return <textarea ref={ref} value={value} readOnly />
})

/** 对外保留 Antd 的 TextArea 静态入口。 */
export function Input({ value }: { value?: string }): ReactElement {
  return <TextArea value={value} />
}
Input.TextArea = TextArea

/** 对外保留 Antd 文本组件的静态入口。 */
export function Typography({ children }: PropsWithChildren): ReactElement {
  return <div>{children}</div>
}
Typography.Paragraph = Container
Typography.Text = Container
export const Alert = Container
export const Spin = Container
export const Tag = Container
