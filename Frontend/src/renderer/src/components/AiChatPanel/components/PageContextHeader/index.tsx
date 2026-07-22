import {
  EllipsisOutlined,
  ExpandOutlined,
  ExportOutlined,
  FileTextOutlined,
  CloseOutlined
} from '@ant-design/icons'
import { Button, Dropdown, Menu, Popover, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PageContextHeader.less'

const { Text } = Typography

type PageContextHeaderProps = {
  description: string
  isPageOpen?: boolean
  keyFeatures: string[]
  lastAnalyzedAt?: number
  onClosePage?: () => void
  onOpenFullscreenPage: () => void
  onOpenPage: () => void
  pagePath: string
  pageTitle: string
  previewAvailable: boolean
  theme: 'light' | 'dark'
}

/** 将最近分析时间转换为适合信息卡展示的相对时间。 */
function formatAnalysisAge(value?: number): string {
  if (!value) return '尚未分析'
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - value) / 60_000))
  if (elapsedMinutes < 1) return '刚刚'
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前`
  const elapsedHours = Math.floor(elapsedMinutes / 60)
  if (elapsedHours < 24) return `${elapsedHours} 小时前`
  return `${Math.floor(elapsedHours / 24)} 天前`
}

/** 统一页面路由的展示格式，确保以斜杠开头。 */
function normalizePagePath(value: string): string {
  const normalizedValue = value.trim()
  if (!normalizedValue) return '/'
  return normalizedValue.startsWith('/') ? normalizedValue : `/${normalizedValue}`
}

/** 渲染当前页面的名称、路由、用途与预览操作。 */
export default function PageContextHeader({
  description,
  isPageOpen = false,
  keyFeatures,
  lastAnalyzedAt,
  onClosePage,
  onOpenFullscreenPage,
  onOpenPage,
  pagePath,
  pageTitle,
  previewAvailable,
  theme
}: PageContextHeaderProps): ReactElement {
  /** 处理打开/关闭页面按钮点击。 */
  const handleTogglePage = (): void => {
    if (isPageOpen && onClosePage) {
      onClosePage()
    } else {
      onOpenPage()
    }
  }
  const detailContent = (
    <div className={cx('page-context-detail')}>
      <Text>{description}</Text>
      {keyFeatures.length > 0 ? (
        <ul>
          {keyFeatures.map((feature) => <li key={feature}>{feature}</li>)}
        </ul>
      ) : null}
    </div>
  )
  const moreMenu = (
    <Menu className={cx('page-context-more-menu')}>
      <Menu.Item
        disabled={!previewAvailable}
        icon={<ExpandOutlined />}
        key="fullscreen"
        onClick={onOpenFullscreenPage}
      >
        全屏打开页面
      </Menu.Item>
    </Menu>
  )

  return (
    <section className={cx('page-context-header')} aria-label="当前页面信息">
      <div className={cx('page-context-primary')}>
        <span className={cx('page-context-icon')} aria-hidden="true">
          <FileTextOutlined />
        </span>
        <div className={cx('page-context-copy')}>
          <div className={cx('page-context-title-row')}>
            <Text className={cx('page-context-title')} strong title={pageTitle}>{pageTitle}</Text>
            <Text className={cx('page-context-path')} code title={normalizePagePath(pagePath)}>
              {normalizePagePath(pagePath)}
            </Text>
          </div>
          <Text className={cx('page-context-description')} title={description}>{description}</Text>
        </div>
      </div>

      <div className={cx('page-context-actions')}>
        <span className={cx('page-context-analysis')}>
          <i aria-hidden="true" />
          <Text>最近分析：{formatAnalysisAge(lastAnalyzedAt)}</Text>
        </span>
        <Popover
          content={detailContent}
          overlayClassName={cx('page-context-popover', theme === 'dark' && 'dark')}
          placement="bottomRight"
          title="页面详情"
          trigger="click"
        >
          <Button>页面详情</Button>
        </Popover>
        <Button
          className={cx('page-context-open-button')}
          disabled={!previewAvailable}
          icon={isPageOpen ? <CloseOutlined /> : <ExportOutlined />}
          onClick={handleTogglePage}
          type="primary"
        >
          {isPageOpen ? '关闭页面' : '打开页面'}
        </Button>
        <Dropdown
          disabled={!previewAvailable}
          overlay={moreMenu}
          overlayClassName={cx('page-context-dropdown', theme === 'dark' && 'dark')}
          placement="bottomRight"
          trigger={['click']}
        >
          <Button aria-label="更多页面操作" className={cx('page-context-more-button')} icon={<EllipsisOutlined />} />
        </Dropdown>
      </div>
    </section>
  )
}
