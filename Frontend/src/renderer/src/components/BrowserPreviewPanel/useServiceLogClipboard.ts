import { useEffect, useState } from 'react'

/** 统一日志按钮和右键菜单的原生复制，并提供可见的成功或错误反馈。 */
export function useServiceLogClipboard(): {
  copied: boolean
  copyError: string
  copyLogs: (text: string) => Promise<void>
} {
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState('')

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  /** 仅在系统剪贴板写入成功后反馈成功，失败时保留手动复制提示。 */
  const copyLogs = async (text: string): Promise<void> => {
    try {
      const clipboard = window.devAgentStudio?.clipboard
      if (!clipboard) throw new Error('Native clipboard is unavailable')
      await clipboard.writeText(text)
      setCopyError('')
      setCopied(true)
    } catch {
      setCopied(false)
      setCopyError('复制失败，请重试或选择日志文本复制。')
    }
  }

  return { copied, copyError, copyLogs }
}
