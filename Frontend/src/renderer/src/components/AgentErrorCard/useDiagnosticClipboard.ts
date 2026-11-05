import { useState } from 'react'

/** 管理诊断复制的成功与失败反馈，成功身份绑定精确文本。 */
export function useDiagnosticClipboard(text: string): {
  copied: boolean
  copying: boolean
  copyFailed: boolean
  copyDiagnostic: () => Promise<void>
} {
  const [copiedText, setCopiedText] = useState<string>()
  const [failedText, setFailedText] = useState<string>()
  const [copying, setCopying] = useState(false)

  /** 写入成功才提示已复制，失败保留详情供用户手动复制。 */
  const copyDiagnostic = async (): Promise<void> => {
    setCopying(true)
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('原生剪贴板不可用')
      await clipboard.writeText(text)
      setCopiedText(text)
      setFailedText(undefined)
    } catch {
      setCopiedText(undefined)
      setFailedText(text)
    } finally {
      setCopying(false)
    }
  }
  return { copied: copiedText === text, copyFailed: failedText === text, copying, copyDiagnostic }
}
