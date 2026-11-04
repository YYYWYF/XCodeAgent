import type { ButtonHTMLAttributes } from 'react'
export * from './requirementEditorAntd'

/** 保留按钮事件和无障碍属性，以核对折叠状态。 */
export function Button({
  size: _size,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { size?: string }): JSX.Element {
  return <button type="button" {...props} />
}

/** 使用按钮承载页签事件，验证真实预览组件的切换行为。 */
export function Tabs(props: {
  activeKey: string
  onChange: (key: string) => void
  items: { key: string; label: string }[]
}): JSX.Element {
  return (
    <div>
      {props.items.map((item) => (
        <button
          key={item.key}
          aria-selected={props.activeKey === item.key}
          onClick={() => props.onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

/** 加载状态无需浏览器动画。 */
export function Spin(): JSX.Element {
  return <span>加载中</span>
}
