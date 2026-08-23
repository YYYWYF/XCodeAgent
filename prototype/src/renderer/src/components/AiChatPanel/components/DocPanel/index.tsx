import { FileTextOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import RichLoading from '../DesignProgress/RichLoading'
import FileDiffView, { type PendingFileDiff } from '../FileDiffView'
import './index.less'

const { Text } = Typography

type Props = {
  content?: string
  generating?: boolean
  docName?: string
  /** 生成中的单文件 Diff（编辑态中的一个行为）：存在时优先于编辑器/预览渲染。 */
  diff?: PendingFileDiff | null
  readOnly?: boolean
  /** 保存编辑草稿（对应 IDE Ctrl+S）。 */
  onSaveEdit?: (draft: string) => void
}

/** 「应用文件」中的文档内容区：IDE 式源码视图。生成就绪后始终展示 Markdown 源码，
 * 只读文档仅禁止编辑、不切换预览态；可编辑文档统一使用 Ctrl/Cmd+S 保存。 */
export default function DocPanel({
  content,
  generating,
  docName,
  diff,
  onSaveEdit,
  readOnly = false
}: Props): ReactElement {
  const [internalDraft, setInternalDraft] = useState(content || '')
  const [editorScrollTop, setEditorScrollTop] = useState(0)
  const lineNumberRef = useRef<HTMLDivElement>(null)
  // 内容变化（新文档生成/切换）→ 同步草稿（保持编辑态，不跳视图）。
  useEffect(() => {
    setInternalDraft(content || '')
  }, [content])
  /** 使用编辑器快捷键保存当前文档草稿，避免 Diff 与编辑态出现两套操作入口。 */
  const handleShortcutSave = (): void => {
    onSaveEdit?.(internalDraft)
  }
  // 文档已就绪(content 有值)才进编辑器态;澄清阶段未生成 → 空引导,不算 dirty。
  const ready = Boolean(content)
  const lineCount = useMemo(
    () => Math.max(1, internalDraft.split('\n').length),
    [internalDraft]
  )

  return (
    <div className={cx('doc-panel')}>
      <div className={cx('doc-panel-stage', (ready || diff) && 'editor')}>
        {diff ? (
          // Diff 过程融合进文档页签：绿色新增行逐段写入，接受后回到编辑器。
          <FileDiffView diff={diff} />
        ) : generating ? (
          <div className={cx('doc-panel-generating')}>
            <RichLoading bare title={`正在生成${docName || '文档'}…`} />
          </div>
        ) : ready ? (
          <div className={cx('doc-panel-code-editor')}>
            <div
              aria-hidden="true"
              className={cx('doc-panel-line-numbers')}
              ref={lineNumberRef}
              style={{ transform: `translateY(-${editorScrollTop}px)` }}
            >
              {Array.from({ length: lineCount }, (_, index) => (
                <div className={cx('doc-panel-line-number')} key={index}>
                  <span className={cx('doc-panel-line-marker')} aria-hidden="true">
                    {' '}
                  </span>
                  {index + 1}
                </div>
              ))}
            </div>
            <textarea
              aria-label={readOnly ? '查看文档' : '编辑文档'}
              className={cx('doc-panel-editor')}
              onChange={(event) => setInternalDraft(event.target.value)}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
                  event.preventDefault()
                  if (!readOnly) handleShortcutSave()
                }
              }}
              onScroll={(event) => setEditorScrollTop(event.currentTarget.scrollTop)}
              readOnly={readOnly}
              spellCheck={false}
              value={internalDraft}
              wrap="off"
            />
          </div>
        ) : (
          <div className={cx('doc-panel-empty')}>
            <span className={cx('doc-panel-orb')}>
              <FileTextOutlined />
            </span>
            <Text strong>{docName ? `${docName}待生成` : '文档将在此生成'}</Text>
            <Text type="secondary">完成当前阶段确认后，文档会生成在这里</Text>
          </div>
        )}
      </div>
    </div>
  )
}
