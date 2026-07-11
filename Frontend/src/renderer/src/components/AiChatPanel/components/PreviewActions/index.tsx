import { CloseOutlined, DesktopOutlined, DownOutlined, ExportOutlined } from '@ant-design/icons'
import { Button, Dropdown } from 'antd'
import type { MenuProps } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PreviewActions.less'

const previewMenuItems: MenuProps['items'] = [
  {
    key: 'external',
    icon: <ExportOutlined />,
    label: '打开网页预览'
  },
  {
    key: 'embedded',
    icon: <DesktopOutlined />,
    label: '打开内嵌页面预览'
  }
]

type PreviewActionsProps = {
  embeddedPreviewOpen: boolean
  onCloseEmbeddedPreview: () => void
  onPreviewAction: MenuProps['onClick']
  theme: 'light' | 'dark'
}

export default function PreviewActions({
  embeddedPreviewOpen,
  onCloseEmbeddedPreview,
  onPreviewAction,
  theme
}: PreviewActionsProps): ReactElement {
  return (
    <div className={cx('preview-actions')}>
      {embeddedPreviewOpen && (
        <Button
          aria-label="关闭内嵌预览"
          icon={<CloseOutlined />}
          onClick={onCloseEmbeddedPreview}
          type="text"
        />
      )}
      <Dropdown
        menu={{ items: previewMenuItems, onClick: onPreviewAction }}
        overlayClassName={cx('preview-actions-dropdown', theme)}
        trigger={['click']}
      >
        <Button icon={<DesktopOutlined />} type="primary">
          预览应用 <DownOutlined />
        </Button>
      </Dropdown>
    </div>
  )
}
