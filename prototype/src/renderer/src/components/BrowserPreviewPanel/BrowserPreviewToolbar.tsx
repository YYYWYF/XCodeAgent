import {
  AimOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
  DesktopOutlined,
  MobileOutlined,
  ReloadOutlined,
  TabletOutlined
} from '@ant-design/icons'
import { Button, Input, Segmented, Select, Tooltip } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'

export type PreviewViewport = 'desktop' | 'tablet' | 'mobile'

type PreviewPageOption = {
  label: string
  value: string
}

type Props = {
  draftUrl: string
  elementInspectorActive: boolean
  elementInspectorReady: boolean
  navigationIndex: number
  navigationLength: number
  onDraftUrlChange: (value: string) => void
  onNavigate: (url: string) => void
  onNavigateHistory: (direction: 'back' | 'forward') => void
  onOpenInBrowser: () => void
  onPageChange: (pagePath: string) => void
  onRefresh: () => void
  onToggleInspector: () => void
  onViewportChange: (viewport: PreviewViewport) => void
  pageOptions: PreviewPageOption[]
  selectedPage: string
  viewport: PreviewViewport
}

/** 渲染普通浏览器预览的导航、审查和设备工具栏；应用验收预览不挂载该工具栏。 */
export default function BrowserPreviewToolbar({
  draftUrl,
  elementInspectorActive,
  elementInspectorReady,
  navigationIndex,
  navigationLength,
  onDraftUrlChange,
  onNavigate,
  onNavigateHistory,
  onOpenInBrowser,
  onPageChange,
  onRefresh,
  onToggleInspector,
  onViewportChange,
  pageOptions,
  selectedPage,
  viewport
}: Props): ReactElement {
  return (
    <header className={cx('browser-preview-toolbar')}>
      <div className={cx('browser-navigation')}>
        <Tooltip title="后退">
          <Button
            aria-label="后退"
            disabled={navigationIndex === 0}
            icon={<ArrowLeftOutlined />}
            onClick={() => onNavigateHistory('back')}
            type="text"
          />
        </Tooltip>
        <Tooltip title="前进">
          <Button
            aria-label="前进"
            disabled={navigationIndex >= navigationLength - 1}
            icon={<ArrowRightOutlined />}
            onClick={() => onNavigateHistory('forward')}
            type="text"
          />
        </Tooltip>
        <Tooltip title="刷新">
          <Button aria-label="刷新" icon={<ReloadOutlined />} onClick={onRefresh} type="text" />
        </Tooltip>
      </div>
      <Input.Search
        aria-label="预览地址"
        className={cx('browser-address-input')}
        enterButton="访问"
        onChange={(event) => onDraftUrlChange(event.target.value)}
        onSearch={onNavigate}
        value={draftUrl}
      />
      <Tooltip
        title={
          elementInspectorActive
            ? '退出元素审查'
            : elementInspectorReady
              ? '审查预览页面中的元素'
              : '当前预览页面尚未准备好元素审查'
        }
      >
        <span className={cx('browser-inspector-button-shell')}>
          <Button
            aria-label={elementInspectorActive ? '退出审查' : '审查元素'}
            aria-pressed={elementInspectorActive}
            className={cx('browser-inspector-button')}
            disabled={!elementInspectorReady && !elementInspectorActive}
            icon={<AimOutlined />}
            onClick={onToggleInspector}
            type="primary"
          >
            {elementInspectorActive ? '退出审查' : '审查元素'}
          </Button>
        </span>
      </Tooltip>
      <Select
        aria-label="页面"
        className={cx('browser-page-select')}
        options={pageOptions}
        value={selectedPage}
        onChange={onPageChange}
      />
      <Segmented
        aria-label="视口"
        className={cx('browser-viewport-switcher')}
        options={[
          { label: <DesktopOutlined />, value: 'desktop' },
          { label: <TabletOutlined />, value: 'tablet' },
          { label: <MobileOutlined />, value: 'mobile' }
        ]}
        value={viewport}
        onChange={(value) => onViewportChange(value as PreviewViewport)}
      />
      <Tooltip title="在系统浏览器打开">
        <Button
          aria-label="在系统浏览器打开"
          icon={<DesktopOutlined />}
          onClick={onOpenInBrowser}
          type="primary"
        />
      </Tooltip>
    </header>
  )
}
