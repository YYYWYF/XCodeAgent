import { useState } from 'react'
import type { DebugSelectionRequest } from '../types'

/** 应用每次快捷跳转，保留跳转之后的手动选择和面板隐藏时的选中项。 */
export function useDebugSelection(request?: DebugSelectionRequest) {
  const [selection, setSelection] = useState({ request, optionKey: request?.optionKey })
  // 在渲染子组件前同步目标，避免先加载旧页面或读取上一个接口的定义。
  if (request && request.requestKey !== selection.request?.requestKey) {
    setSelection({ request, optionKey: request.optionKey })
  }

  /** 手动选页或选接口后，保留已处理的跳转请求。 */
  const selectOption = (optionKey: string): void => {
    setSelection((current) => ({ ...current, optionKey }))
  }
  return [selection.optionKey, selectOption, selection.request?.requestKey] as const
}
