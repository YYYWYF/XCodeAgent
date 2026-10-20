import { Input } from 'antd'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import type {
  ChangeEvent,
  KeyboardEvent,
  KeyboardEventHandler,
  MouseEventHandler,
  ReactElement
} from 'react'
import { useId, useMemo, useRef, useState } from 'react'
import { cx } from '../../../../utils'
import type { WorkflowDebugStage } from '../../../../typings'
import {
  workflowStageMentions,
  type ProductDesignStageMention
} from './productConversation'

const { TextArea } = Input

type MentionQuery = { start: number; query: string }

type Props = {
  stage: WorkflowDebugStage
  ariaLabel: string
  onChange: (value: string) => void
  onKeyDown?: KeyboardEventHandler<HTMLTextAreaElement>
  onPressEnter: (event: KeyboardEvent<HTMLTextAreaElement>) => void
  placeholder: string
  value: string
}

/** 在规划输入框中定位光标前尚未完成的当前阶段 @ 查询。 */
function findStageMentionQuery(text: string, caret: number, stage: WorkflowDebugStage): MentionQuery | undefined {
  const beforeCaret = text.slice(0, caret)
  const start = beforeCaret.lastIndexOf('@')
  if (start < 0 || /[A-Za-z0-9_@]/.test(beforeCaret[start - 1] || '')) {
    return undefined
  }
  const query = beforeCaret.slice(start + 1)
  if (/[\r\n]/.test(query)) return undefined
  const completedMention = workflowStageMentions(stage).some((mention) =>
    query.toLocaleLowerCase().startsWith(`${mention.label.toLocaleLowerCase()} `)
  )
  return completedMention ? undefined : { start, query }
}

/** 处理设计与计划阶段的 @ 选择，并将节点标记保留在可编辑草稿中。 */
export default function WorkflowStageMentionInput({
  stage,
  ariaLabel,
  onChange,
  onKeyDown,
  onPressEnter,
  placeholder,
  value
}: Props): ReactElement {
  const textareaRef = useRef<TextAreaRef | null>(null)
  const listId = useId()
  const [mentionOpen, setMentionOpen] = useState(false)
  const [mentionStart, setMentionStart] = useState<number | null>(null)
  const [mentionQuery, setMentionQuery] = useState('')
  const [activeCandidateIndex, setActiveCandidateIndex] = useState(0)

  // 只过滤当前阶段允许的节点，并匹配用户已输入的中文标签。
  const candidates = useMemo(() => {
    const query = mentionQuery.trim().toLocaleLowerCase()
    return workflowStageMentions(stage).filter((mention) =>
      `${mention.label}\n${mention.description}\n${mention.node}`
        .toLocaleLowerCase()
        .includes(query)
    )
  }, [mentionQuery, stage])
  const visibleCandidates = mentionOpen ? candidates : []
  const selectedCandidateIndex = Math.min(
    activeCandidateIndex,
    Math.max(visibleCandidates.length - 1, 0)
  )

  // 将用户编辑内容同步给主会话草稿，并更新当前 @ 查询。
  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const nextValue = event.target.value
    onChange(nextValue)
    const caret = event.target.selectionStart ?? nextValue.length
    const activeMention = findStageMentionQuery(nextValue, caret, stage)
    setMentionStart(activeMention?.start ?? null)
    setMentionQuery(activeMention?.query ?? '')
    setActiveCandidateIndex(0)
    setMentionOpen(Boolean(activeMention))
  }

  // 用阶段标签替换 @ 查询，并在草稿更新后把光标恢复到插入文本末尾。
  const insertMention = (mention: ProductDesignStageMention): void => {
    if (mentionStart === null) return
    const textarea = textareaRef.current?.resizableTextArea?.textArea
    const caret = textarea?.selectionStart ?? value.length
    const insertedText = `@${mention.label} `
    const nextValue = value.slice(0, mentionStart) + insertedText + value.slice(caret)
    const nextCaret = mentionStart + insertedText.length
    onChange(nextValue)
    setMentionOpen(false)
    setMentionStart(null)
    setMentionQuery('')
    setTimeout(() => {
      const currentTextarea = textareaRef.current?.resizableTextArea?.textArea
      textareaRef.current?.focus()
      currentTextarea?.setSelectionRange(nextCaret, nextCaret)
    }, 0)
  }

  // 处理候选列表键盘导航，其余按键沿用主输入框的技能标签与发送逻辑。
  const handleKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = (event) => {
    if (mentionOpen && event.key === 'Escape') {
      setMentionOpen(false)
      event.preventDefault()
      return
    }
    if (visibleCandidates.length > 0 && !event.nativeEvent.isComposing) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const direction = event.key === 'ArrowDown' ? 1 : -1
        setActiveCandidateIndex((current) =>
          (current + direction + visibleCandidates.length) % visibleCandidates.length
        )
        return
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault()
        insertMention(visibleCandidates[selectedCandidateIndex])
        return
      }
    }
    onKeyDown?.(event)
  }

  // 鼠标选择时保持文本框焦点，让插入位置仍使用原光标。
  const handleCandidateMouseDown: MouseEventHandler<HTMLButtonElement> = (event) => {
    event.preventDefault()
  }

  // 根据稳定的节点 ID 找回选项，避免依赖展示文案路由。
  const handleCandidateClick: MouseEventHandler<HTMLButtonElement> = (event) => {
    const node = event.currentTarget.dataset.node || ''
    const mention = visibleCandidates.find((candidate) => candidate.node === node)
    if (mention) insertMention(mention)
  }

  return (
    <div className={cx('composer-workflow-stage-mention-input')}>
      <TextArea
        aria-label={ariaLabel}
        aria-controls={listId}
        aria-expanded={visibleCandidates.length > 0}
        aria-haspopup="listbox"
        autoSize={{ minRows: 1, maxRows: 6 }}
        bordered={false}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPressEnter={(event) => {
          if (!event.defaultPrevented) onPressEnter(event)
        }}
        placeholder={placeholder}
        ref={textareaRef}
        value={value}
      />
      {visibleCandidates.length > 0 ? (
        <div className={cx('composer-workflow-stage-mention-list')} id={listId} role="listbox">
          {visibleCandidates.map((mention, index) => (
            <button
              aria-selected={index === selectedCandidateIndex}
              className={cx(
                'composer-workflow-stage-mention-option',
                index === selectedCandidateIndex && 'active'
              )}
              data-node={mention.node}
              key={mention.node}
              onClick={handleCandidateClick}
              onMouseDown={handleCandidateMouseDown}
              role="option"
              type="button"
            >
              <span>{mention.label}</span>
              <span className={cx('composer-workflow-stage-mention-description')}>
                {mention.description}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
