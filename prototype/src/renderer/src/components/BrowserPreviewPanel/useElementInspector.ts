import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { message as antdMessage } from 'antd'
import {
  createElementInspectorCommand,
  elementInspectorPreviewOrigin,
  isExpectedElementInspectorOrigin,
  parseElementInspectorMessage
} from './elementInspectorProtocol'

type Options = {
  /** 应用验收预览不提供元素审查，关闭监听与跨窗口通信。 */
  enabled?: boolean
  frameKey: string
  onInspectingChange?: (active: boolean) => void
  previewUrl: string
}

type ElementInspectorController = {
  active: boolean
  iframeRef: RefObject<HTMLIFrameElement>
  ready: boolean
  toggle: () => void
}

const ELEMENT_INSPECTOR_MESSAGE_KEY = 'element-inspector-selection'

/** 管理跨域 iframe 审查协议、就绪状态以及 renderer 内的选中结果反馈。 */
export function useElementInspector({
  enabled = true,
  frameKey,
  onInspectingChange,
  previewUrl
}: Options): ElementInspectorController {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [active, setActive] = useState(false)
  const [ready, setReady] = useState(false)
  const activeRef = useRef(false)
  const previewOrigin = useMemo(() => elementInspectorPreviewOrigin(previewUrl), [previewUrl])
  const inspectingChangeRef = useRef(onInspectingChange)

  /** 向当前 iframe 文档发送明确 targetOrigin 的审查命令。 */
  const postActiveCommand = useCallback(
    (nextActive: boolean): void => {
      if (!previewOrigin) return
      iframeRef.current?.contentWindow?.postMessage(
        createElementInspectorCommand(nextActive),
        previewOrigin
      )
    },
    [previewOrigin]
  )
  const postActiveCommandRef = useRef(postActiveCommand)

  useEffect(() => {
    inspectingChangeRef.current = onInspectingChange
    postActiveCommandRef.current = postActiveCommand
  }, [onInspectingChange, postActiveCommand])

  /** 切换审查状态并同步父级遮罩和 iframe 运行时。 */
  const toggle = useCallback((): void => {
    if (!enabled || (!activeRef.current && !ready)) return
    const nextActive = !activeRef.current
    activeRef.current = nextActive
    setActive(nextActive)
    onInspectingChange?.(nextActive)
    postActiveCommand(nextActive)
  }, [enabled, onInspectingChange, postActiveCommand, ready])

  useEffect(() => {
    setReady(false)
    // 进入应用验收预览时，确保此前普通浏览器的审查遮罩与 iframe 命令彻底退出。
    if (!enabled && activeRef.current) {
      activeRef.current = false
      setActive(false)
      onInspectingChange?.(false)
      postActiveCommand(false)
    }
  }, [enabled, frameKey, onInspectingChange, postActiveCommand])

  useEffect(() => {
    if (!enabled) return undefined
    /** 只接收当前 iframe 和当前 origin 的消息，并在监听器内输出和提示选中位置。 */
    const handleMessage = (event: MessageEvent): void => {
      if (event.source !== iframeRef.current?.contentWindow) return
      if (!isExpectedElementInspectorOrigin(event.origin, previewUrl)) return
      const message = parseElementInspectorMessage(event.data)
      if (!message) return
      if (message.type === 'ready') {
        setReady(true)
        if (activeRef.current) postActiveCommand(true)
        return
      }
      let selectionContent: string
      if (message.sourceLocation) {
        const { sourcePath, line, column } = message.sourceLocation
        selectionContent = `<${message.tagName}> ${sourcePath}:${line}:${column}`
        console.log(`[ElementInspector] <${message.tagName}> ${sourcePath}:${line}:${column}`)
      } else {
        selectionContent = `<${message.tagName}> source unavailable`
        console.log(`[ElementInspector] <${message.tagName}> source unavailable`)
      }
      antdMessage.info({
        key: ELEMENT_INSPECTOR_MESSAGE_KEY,
        content: selectionContent,
        duration: 3
      })
    }

    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [enabled, postActiveCommand, previewUrl])

  useEffect(() => {
    return () => {
      if (activeRef.current) postActiveCommandRef.current(false)
      activeRef.current = false
      inspectingChangeRef.current?.(false)
    }
  }, [])

  return { active, iframeRef, ready, toggle }
}
