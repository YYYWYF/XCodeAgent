import { FileTextOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import RichLoading from '../DesignProgress/RichLoading'
import './index.less'

const { Text } = Typography

type Props = {
  content?: string
  title?: string
  generating?: boolean
  docName?: string
  /** 保存编辑草稿（对应 IDE Ctrl+S）。 */
  onSaveEdit?: (draft: string) => void
}

/** 右侧「文档」面板：IDE 式人工编辑口子。生成就绪后默认编辑态（全面板编辑区，
 * 内部留白让文字协调），右上角仅一个保存按钮（Ctrl+S 语义）。接受/放弃在对话区授权条。 */
export default function DocPanel({
  content,
  title,
  generating,
  docName,
  onSaveEdit
}: Props): ReactElement {
  const [internalDraft, setInternalDraft] = useState(content || '')
  // 内容变化（新文档生成/切换）→ 同步草稿（保持编辑态，不跳视图）。
  useEffect(() => {
    setInternalDraft(content || '')
  }, [content])
  const savedRef = useRef(false)
  const handleSave = (): void => {
    onSaveEdit?.(internalDraft)
    savedRef.current = true
  }
  useEffect(() => {
    if (!savedRef.current) return
    savedRef.current = false
  }, [content])
  // 文档已就绪(content 有值)才进编辑器态;澄清阶段未生成 → 空引导,不算 dirty。
  const ready = Boolean(content)
  // 草稿是否偏离生成版（有未保存修改）→ 展示轻提示;仅文档就绪时判断。
  const dirty = ready && internalDraft !== content

  return (
    <div className={cx('doc-panel')}>
      <header className={cx('doc-panel-toolbar')}>
        <div className={cx('doc-panel-path')}>{title || '文档'}</div>
        {ready && !generating && (
          <div className={cx('doc-panel-edit-actions')}>
            <Text className={cx('doc-panel-dirty-hint')} type="secondary">
              {dirty ? '有未保存的修改' : '已保存'}
            </Text>
            <Button
              onClick={handleSave}
              size="small"
              type="primary"
            >
              保存
            </Button>
          </div>
        )}
      </header>
      <div className={cx('doc-panel-stage', ready && 'editor')}>
        {generating ? (
          <div className={cx('doc-panel-generating')}>
            <RichLoading bare title={`正在生成${docName || '文档'}…`} />
          </div>
        ) : ready ? (
          <textarea
            aria-label="编辑文档"
            className={cx('doc-panel-editor')}
            onChange={(event) => setInternalDraft(event.target.value)}
            spellCheck={false}
            value={internalDraft}
          />
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
