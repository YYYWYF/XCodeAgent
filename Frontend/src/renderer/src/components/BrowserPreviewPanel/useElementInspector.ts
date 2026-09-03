import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { message as antdMessage } from 'antd'
import type { InspectedElementContext } from '../../typings'
import {
  createElementInspectorCommand,
  elementInspectorPreviewOrigin,
  inspectedElementContextFromMessage,
  inspectedElementLocationMessage,
  isExpectedElementInspectorOrigin,
  parseElementInspectorMessage
} from './elementInspectorProtocol'

type Options = {
  frameKey: string
  onElementContextChange?: (context: InspectedElementContext | undefined) => void
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
  frameKey,
  onElementContextChange,
  onInspectingChange,
  previewUrl
}: Options): ElementInspectorController {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [active, setActive] = useState(false)
  const [ready, setReady] = useState(false)
  const activeRef = useRef(false)
  const previewOrigin = useMemo(() => elementInspectorPreviewOrigin(previewUrl), [previewUrl])
  const inspectingChangeRef = useRef(onInspectingChange)
  const elementContextChangeRef = useRef(onElementContextChange)

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
    elementContextChangeRef.current = onElementContextChange
    postActiveCommandRef.current = postActiveCommand
  }, [onElementContextChange, onInspectingChange, postActiveCommand])

  /** 切换审查状态并同步父级布局状态和 iframe 运行时。 */
  const toggle = useCallback((): void => {
    if (!activeRef.current && !ready) return
    const nextActive = !activeRef.current
    activeRef.current = nextActive
    setActive(nextActive)
    onInspectingChange?.(nextActive)
    postActiveCommand(nextActive)
  }, [onInspectingChange, postActiveCommand, ready])

  useEffect(() => {
    setReady(false)
    elementContextChangeRef.current?.(undefined)
  }, [frameKey])

  useEffect(() => {
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
      const context = inspectedElementContextFromMessage(message)
      const selectionContent = context
        ? inspectedElementLocationMessage(context)
        : `<${message.tagName}> 无法定位源码`
      if (context) {
        console.log(
          `[ElementInspector] <${context.tagName}> ${context.sourcePath}:${context.line}:${context.column}`
        )
        elementContextChangeRef.current?.(context)
      } else {
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
  }, [postActiveCommand, previewUrl])

  useEffect(() => {
    return () => {
      if (activeRef.current) postActiveCommandRef.current(false)
      activeRef.current = false
      inspectingChangeRef.current?.(false)
    }
  }, [])

  return { active, iframeRef, ready, toggle }
}
