import { useCallback, useRef } from 'react'

/**
 * Tab 键填充 Placeholder 的 Hook
 *
 * 当用户按下 Tab 键时，将 placeholder 内容填充到当前输入框。
 * 需要在 Input/TextArea 组件上绑定 onKeyDown 事件。
 *
 * @returns handleKeyDown - 绑定到 onKeyDown 的事件处理器
 *
 * @example
 * ```tsx
 * const handleKeyDown = useTabToFillPlaceholder()
 * <Input placeholder="请输入内容" onKeyDown={handleKeyDown} />
 * ```
 */
export function useTabToFillPlaceholder() {
  const filledRef = useRef(false)

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      // 只处理 Tab 键
      if (e.key !== 'Tab') {
        filledRef.current = false
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

      // 将 placeholder 内容填充到输入框
      // 使用原生 setter 以触发 React 的受控组件更新
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value'
      )?.set
      const nativeTextAreaValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        'value'
      )?.set

      if (target.tagName === 'TEXTAREA' && nativeTextAreaValueSetter) {
        nativeTextAreaValueSetter.call(target, placeholder)
      } else if (nativeInputValueSetter) {
        nativeInputValueSetter.call(target, placeholder)
      }

      // 触发 input 事件以更新 React 状态
      target.dispatchEvent(new Event('input', { bubbles: true }))

      // 标记已填充，防止重复触发
      filledRef.current = true
    },
    []
  )

  return handleKeyDown
}
