import type { PropsWithChildren, ReactElement } from 'react'

/** 定向测试只替代 Antd 的 DOM/动画边界，实际状态和 AG-UI 客户端保持不变。 */
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

/** 用可观察输入触发真实组件的明确审阅事件。 */
export function Checkbox({
  children,
  ...props
}: PropsWithChildren<{
  checked: boolean
  disabled?: boolean
  onChange: (event: { target: { checked: boolean } }) => void
}>): ReactElement {
  return (
    <label>
      <input type="checkbox" {...props} />
      {children}
    </label>
  )
}

/** 容器不模拟浏览器布局或主题验收。 */
export function Space({ children }: PropsWithChildren): ReactElement {
  return <div>{children}</div>
}

/** 保留产品弹窗的打开边界，移除依赖浏览器的 portal。 */
export function Modal({
  open,
  children
}: PropsWithChildren<{ open: boolean }>): ReactElement | null {
  return open ? <div role="dialog">{children}</div> : null
}

/** 保留真实提示文字便于验证成功与失败投影。 */
export function Alert({
  message,
  description
}: {
  message: string
  description?: ReactElement
}): ReactElement {
  return (
    <div>
      {message}
      {description}
    </div>
  )
}

export const Typography = Object.assign(Space, { Paragraph: Space, Text: Space })
export const Collapse = Object.assign(Space, { Panel: Space })
