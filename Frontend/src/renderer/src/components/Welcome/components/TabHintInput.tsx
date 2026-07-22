import { AutoComplete, Input } from 'antd'
import type { InputProps } from 'antd'
import { useTabToFillPlaceholder } from '../hooks/useTabToFillPlaceholder'
import { cx } from '../../../utils'

/**
 * 构建带 Tab 提示的 placeholder
 */
function buildTabHintPlaceholder(placeholder?: string): string | undefined {
  if (!placeholder) return undefined
  return `${placeholder} (按 Tab 采用)`
}

/**
 * 带 Tab 键填充提示的 Input 组件
 *
 * 在 placeholder 后添加 "按 Tab 采用" 提示，
 * 用户按 Tab 键可快速填充 placeholder 内容到输入框。
 */
export function TabHintInput({ placeholder, className, ...props }: InputProps) {
  const handleKeyDown = useTabToFillPlaceholder()

  return (
    <Input
      {...props}
      className={cx('tab-hint-input', className)}
      placeholder={buildTabHintPlaceholder(placeholder)}
      onKeyDown={handleKeyDown}
    />
  )
}

/**
 * 带 Tab 键填充提示的 TextArea 组件
 *
 * 在 placeholder 后添加 "按 Tab 采用" 提示，
 * 用户按 Tab 键可快速填充 placeholder 内容到输入框。
 */
export function TabHintTextArea({
  placeholder,
  className,
  ...props
}: React.ComponentProps<typeof Input.TextArea>) {
  const handleKeyDown = useTabToFillPlaceholder()

  return (
    <Input.TextArea
      {...props}
      className={cx('tab-hint-input', className)}
      placeholder={buildTabHintPlaceholder(placeholder)}
      onKeyDown={handleKeyDown}
    />
  )
}

/**
 * 带 Tab 键填充提示的 AutoComplete 组件
 *
 * 在 placeholder 后添加 "按 Tab 采用" 提示，
 * 用户按 Tab 键可快速填充 placeholder 内容到输入框。
 */
export function TabHintAutoComplete({
  placeholder,
  className,
  ...props
}: any) {
  const handleKeyDown = useTabToFillPlaceholder()

  return (
    <AutoComplete
      {...props}
      className={cx('tab-hint-input', className)}
      placeholder={buildTabHintPlaceholder(placeholder)}
      onKeyDown={handleKeyDown}
    />
  )
}
