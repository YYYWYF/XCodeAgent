import {
  ApiOutlined,
  DownOutlined,
  FileMarkdownOutlined,
  FileTextOutlined,
  LockOutlined,
  UnlockOutlined
} from '@ant-design/icons'
import { Popover, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PageContextHeader.less'

const { Text } = Typography

export type ConversationArtifact = {
  accessMessage?: string
  accessMode?: 'unavailable' | 'read' | 'write'
  id: string
  name: string
  path: string
  status: '未开始' | '进行中' | '已完成'
  type: 'page' | 'endpoint' | 'document'
}

type PageContextHeaderProps = {
  artifacts: ConversationArtifact[]
  conversationTitle: string
  historical?: boolean
}

/** 将产物状态映射为稳定的样式名称。 */
function artifactStatusClass(status: ConversationArtifact['status']): string {
  if (status === '已完成') return 'completed'
  if (status === '进行中') return 'in-progress'
  return 'not-started'
}

/** 对话顶部仅常驻对话名称，点击后通过浮层查看关联产物详情。 */
export default function PageContextHeader({
  artifacts,
  conversationTitle,
  historical = false
}: PageContextHeaderProps): ReactElement {
  const artifactDetails = (
    <div className={cx('conversation-artifact-popover')}>
      <header>
        <Text strong>关联产物</Text>
        <span>{artifacts.length}</span>
      </header>
      <div className={cx('artifact-context-list')}>
        {artifacts.length > 0 ? (
          artifacts.map((artifact) => (
            <div className={cx('artifact-context-row')} key={artifact.id}>
              <span aria-hidden="true" className={cx('artifact-context-icon')}>
                {artifact.type === 'endpoint' ? (
                  <ApiOutlined />
                ) : artifact.type === 'document' ? (
                  <FileMarkdownOutlined />
                ) : (
                  <FileTextOutlined />
                )}
              </span>
              <Text className={cx('artifact-context-name')} strong title={artifact.name}>
                {artifact.name}
              </Text>
              <Text className={cx('artifact-context-path')} code title={artifact.path}>
                {artifact.path}
              </Text>
              <span className={cx('artifact-context-status', artifactStatusClass(artifact.status))}>
                <i aria-hidden="true" />
                {artifact.status}
              </span>
              <span
                className={cx('artifact-context-access', artifact.accessMode || 'read')}
                title={artifact.accessMessage}
              >
                {artifact.accessMode === 'write' ? <UnlockOutlined /> : <LockOutlined />}
                {artifact.accessMode === 'write' ? '可编辑' : '只读'}
              </span>
            </div>
          ))
        ) : (
          <div className={cx('artifact-context-empty')}>
            引用或修改文件后，产物会自动关联到当前对话。
          </div>
        )}
      </div>
    </div>
  )

  return (
    <section
      aria-label="当前对话"
      className={cx('page-context-header', 'conversation-context-header')}
    >
      <Popover
        content={artifactDetails}
        getPopupContainer={(trigger) => trigger.parentElement || trigger}
        overlayClassName={cx('conversation-artifact-popover-overlay')}
        placement="bottomLeft"
        trigger="click"
      >
        <button className={cx('conversation-context-trigger')} type="button">
          <MessageTitle title={conversationTitle} />
          <span className={cx('conversation-artifact-count')}>{artifacts.length} 个产物</span>
          <DownOutlined />
        </button>
      </Popover>
      {historical ? <span className={cx('conversation-history-label')}>历史任务</span> : null}
    </section>
  )
}

/** 让较长的对话标题保持单行截断。 */
function MessageTitle({ title }: { title: string }): ReactElement {
  return (
    <Text strong title={title}>
      {title || '新对话'}
    </Text>
  )
}
