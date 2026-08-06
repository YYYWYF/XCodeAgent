import { useCallback } from 'react'
import type { FormInstance } from 'antd'

/**
 * Tab 键填充 Placeholder 的 Hook
 *
 * 当用户按下 Tab 键时，将 placeholder 内容填充到当前输入框。
 * 使用 antd Form API 更新值，确保受控组件正确更新。
 *
 * @param form - antd Form 实例
 * @param fieldName - 表单字段名（支持嵌套，如 ['menus', 'rootPath']）
 * @returns handleKeyDown - 绑定到 onKeyDown 的事件处理器
 *
 * @example
 * ```tsx
 * const form = Form.useFormInstance()
 * const handleKeyDown = useTabToFillPlaceholder(form, ['menus', 'rootPath'])
 * <Input placeholder="请输入内容" onKeyDown={handleKeyDown} />
 * ```
 */
export function useTabToFillPlaceholder(
  form: FormInstance,
  fieldName: string | string[]
) {
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      // 只处理 Tab 键
      if (e.key !== 'Tab') {
        return
      }

      const target = e.target as HTMLInputElement | HTMLTextAreaElement
      const placeholder = target.getAttribute('placeholder')

      // 如果没有 placeholder 或输入框已有内容，则不处理
      if (!placeholder || target.value) {
        return
      }

      // 阻止默认的 Tab 行为（焦点切换）
      e.preventDefault()

      // 清理 placeholder 中的提示文本 (按 Tab 采用)
      const cleanValue = placeholder.replace(/\s*\(按 Tab 采用\)\s*$/, '')

      // 使用 antd Form API 更新值
      form.setFields([{ name: fieldName, value: cleanValue }])
    },
    [form, fieldName]
  )

  return handleKeyDown
}
