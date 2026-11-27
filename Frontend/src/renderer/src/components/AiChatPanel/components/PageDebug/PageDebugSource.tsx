import { Button, Spin } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import { readWorkspaceFile } from '../../../../service/workspaceTools'
import type { ElementInspectorSelectionMessage } from '../../../BrowserPreviewPanel/elementInspectorProtocol'
import { debugPageSourcePath } from './pageDebugModel'

/** 展示被选中的元素，并按需读取当前位置附近的有限源码。 */
export default function PageDebugSource({
  selection,
  workspaceRoot
}: {
  selection: ElementInspectorSelectionMessage
  workspaceRoot: string
}): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const [source, setSource] = useState<{ content?: string; failed?: boolean }>()
  const location = selection.sourceLocation
  const path = debugPageSourcePath(location?.sourcePath)
  const line = location?.line ?? 1

  useEffect(() => {
    if (!expanded || !path) return
    let disposed = false
    setSource(undefined)
    void readWorkspaceFile({
      workspace_root: workspaceRoot,
      path,
      start_line: Math.max(1, line - 5),
      max_lines: 11,
      max_chars: 8000
    }).then(
      (result) => {
        if (!disposed) setSource({ content: result.content })
      },
      () => {
        if (!disposed) setSource({ failed: true })
      }
    )
    return () => {
      disposed = true
    }
  }, [expanded, line, path, workspaceRoot])

  return (
    <section className="page-debug__source" aria-label="选中元素">
      <div className="page-debug__source-heading">
        <strong>{`<${selection.tagName}>`}</strong>
        {path && location ? (
          <>
            <code title={path}>
              {path}:{location.line}:{location.column}
            </code>
            <Button size="small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
              {expanded ? '收起源码' : '查看源码'}
            </Button>
          </>
        ) : (
          <span>该元素暂无可定位的源码</span>
        )}
      </div>
      {expanded &&
        (!source ? (
          <Spin size="small" aria-label="正在读取源码" />
        ) : source.failed ? (
          <span role="status">无法读取对应源码</span>
        ) : (
          <pre aria-label="元素对应源码">
            <code>{source.content || '该位置暂无源码'}</code>
          </pre>
        ))}
    </section>
  )
}
