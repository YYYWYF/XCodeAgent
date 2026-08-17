import { AutoComplete, Input } from 'antd'
import type { FormInstance, InputProps } from 'antd'
import { useTabToFillPlaceholder } from '../hooks/useTabToFillPlaceholder'
import { cx } from '../../../utils'

/**
 * 构建带 Tab 提示的 placeholder
 */
function buildTabHintPlaceholder(placeholder?: string): string | undefined {
  if (!placeholder) return undefined
  return `${placeholder} (按 Tab 采用)`
}

type TabHintProps = Omit<InputProps, 'form'> & {
  /** antd Form 实例 */
  form: FormInstance
  /** 表单字段名（支持嵌套，如 ['menus', 'rootPath']） */
  fieldName: string | string[]
}

/**
 * 带 Tab 键填充提示的 Input 组件
 *
 * 在 placeholder 后添加 "按 Tab 采用" 提示，
 * 用户按 Tab 键可快速填充 placeholder 内容到输入框。
 */
export function TabHintInput({ placeholder, className, form, fieldName, ...props }: TabHintProps) {
  const handleKeyDown = useTabToFillPlaceholder(form, fieldName)

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
 * 带 Tab 键填充提示的 AutoComplete 组件
 *
 * 在 placeholder 后添加 "按 Tab 采用" 提示，
 * 用户按 Tab 键可快速填充 placeholder 内容到输入框。
 */
export function TabHintAutoComplete({
  placeholder,
  className,
  form,
  fieldName,
  ...props
}: any & { form: FormInstance; fieldName: string | string[] }) {
  const handleKeyDown = useTabToFillPlaceholder(form, fieldName)

  return (
    <AutoComplete
      {...props}
      className={cx('tab-hint-input', className)}
      placeholder={buildTabHintPlaceholder(placeholder)}
      onKeyDown={handleKeyDown}
    />
  )
}
