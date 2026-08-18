import { FileTextOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
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
}

/** 右侧「文档」面板：只读展示生成的 Markdown 文档。编辑通过对话区卡片的弹窗进行。 */
export default function DocPanel({
  content,
  title,
  generating,
  docName
}: Props): ReactElement {
  const ready = Boolean(content)

  return (
    <div className={cx('doc-panel')}>
      <header className={cx('doc-panel-toolbar')}>
        <div className={cx('doc-panel-path')}>{title || '文档'}</div>
      </header>
      <div className={cx('doc-panel-stage', ready && 'editor')}>
        {generating ? (
          <div className={cx('doc-panel-generating')}>
            <RichLoading bare title={`正在生成${docName || '文档'}…`} />
          </div>
        ) : ready ? (
          <pre className={cx('doc-panel-viewer')}>{content}</pre>
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
