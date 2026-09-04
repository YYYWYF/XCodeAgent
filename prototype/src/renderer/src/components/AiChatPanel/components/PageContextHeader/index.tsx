import { EditOutlined } from '@ant-design/icons'
import { Input, Typography } from 'antd'
import type { InputRef } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import { cx } from '../../../../utils'
import './PageContextHeader.less'

const { Text } = Typography

type PageContextHeaderProps = {
  conversationTitle: string
  historical?: boolean
  /** 标题重命名回调；提供后常显编辑图标，点击图标进入行内编辑，回车或失焦保存。 */
  onRename?: (title: string) => void
}

/** 任务顶部呈现会话身份；编辑图标常显，点击即重命名，历史任务只读。 */
export default function PageContextHeader({
  conversationTitle,
  historical = false,
  onRename
}: PageContextHeaderProps): ReactElement {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conversationTitle)
  const inputRef = useRef<InputRef>(null)
  const editable = Boolean(onRename) && !historical

  // 进入编辑时全选现有标题；标题被外部（如自动命名）更新且不在编辑态时同步草稿。
  useEffect(() => {
    if (editing) inputRef.current?.select()
    else setDraft(conversationTitle)
  }, [editing, conversationTitle])

  const startEditing = (): void => {
    if (!editable) return
    setDraft(conversationTitle)
    setEditing(true)
  }

  /** 提交重命名：空标题或与原名相同视为取消。 */
  const commit = (): void => {
    const trimmed = draft.trim()
    setEditing(false)
    if (trimmed && trimmed !== conversationTitle) onRename?.(trimmed)
  }

  return (
    <section
      aria-label="当前对话"
      className={cx('page-context-header', 'conversation-context-header')}
    >
      <div className={cx('conversation-context-title')}>
        {editing ? (
          <Input
            autoFocus
            onBlur={commit}
            onChange={(event) => setDraft(event.target.value)}
            onPressEnter={commit}
            ref={inputRef}
            size="small"
            value={draft}
          />
        ) : (
          <>
            <MessageTitle title={conversationTitle} />
            {editable ? (
              <button
                aria-label="重命名任务"
                className={cx('conversation-title-edit')}
                onClick={startEditing}
                title="重命名任务"
                type="button"
              >
                <EditOutlined aria-hidden="true" />
              </button>
            ) : null}
          </>
        )}
      </div>
      {historical ? <span className={cx('conversation-history-label')}>历史任务</span> : null}
    </section>
  )
}

/** 让较长的对话标题保持单行截断，并以原生 title 提示完整标题。 */
function MessageTitle({ title }: { title: string }): ReactElement {
  return (
    <Text strong title={title}>
      {title || '新对话'}
    </Text>
  )
}
