import { DesktopOutlined, ExpandOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PreviewActions.less'

type PreviewActionsProps = {
  embeddedPreviewOpen: boolean
  onOpenFullscreenPreview: () => void
  onToggleEmbeddedPreview: () => void
}

export default function PreviewActions({
  embeddedPreviewOpen,
  onOpenFullscreenPreview,
  onToggleEmbeddedPreview
}: PreviewActionsProps): ReactElement {
  return (
    <div className={cx('preview-actions')}>
      <Button
        aria-label={embeddedPreviewOpen ? '关闭嵌入预览' : '打开嵌入预览'}
        aria-pressed={embeddedPreviewOpen}
        className={cx(embeddedPreviewOpen && 'active')}
        icon={<DesktopOutlined />}
        onClick={onToggleEmbeddedPreview}
        title={embeddedPreviewOpen ? '关闭预览' : '预览应用'}
      >
        {embeddedPreviewOpen ? '关闭预览' : '预览应用'}
      </Button>
      <Button
        aria-label="全屏预览"
        icon={<ExpandOutlined />}
        onClick={onOpenFullscreenPreview}
        title="全屏预览"
      >
        全屏预览
      </Button>
    </div>
  )
}
