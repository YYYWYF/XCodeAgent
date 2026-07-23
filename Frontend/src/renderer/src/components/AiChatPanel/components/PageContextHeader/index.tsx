import {
  ApiOutlined,
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
  detailButtonLabel?: string
  detailTitle?: string
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
  targetType?: 'page' | 'api'
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

/** 统一页面路由或 API 路径的展示格式，确保以斜杠开头。 */
function normalizeDisplayPath(value: string): string {
  const normalizedValue = value.trim()
  if (!normalizedValue) return '/'
  return normalizedValue.startsWith('/') ? normalizedValue : `/${normalizedValue}`
}

/** 渲染当前页面或 API endpoint 的名称、路径、说明与操作。 */
export default function PageContextHeader({
  detailButtonLabel,
  detailTitle,
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
  targetType = 'page',
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
  const pathText = normalizeDisplayPath(pagePath)
  const canPreviewPage = targetType === 'page' && previewAvailable

  return (
    <section className={cx('page-context-header')} aria-label={targetType === 'api' ? '当前 API 信息' : '当前页面信息'}>
      <div className={cx('page-context-primary')}>
        <span className={cx('page-context-icon')} aria-hidden="true">
          {targetType === 'api' ? <ApiOutlined /> : <FileTextOutlined />}
        </span>
        <div className={cx('page-context-copy')}>
          <div className={cx('page-context-title-row')}>
            <Text className={cx('page-context-title')} strong title={pageTitle}>{pageTitle}</Text>
            <Text className={cx('page-context-path')} code title={pathText}>
              {pathText}
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
          title={detailTitle || (targetType === 'api' ? 'API 详情' : '页面详情')}
          trigger="click"
        >
          <Button>{detailButtonLabel || (targetType === 'api' ? 'API 详情' : '页面详情')}</Button>
        </Popover>
        {targetType === 'page' ? (
          <>
            <Button
              className={cx('page-context-open-button')}
              disabled={!canPreviewPage}
              icon={isPageOpen ? <CloseOutlined /> : <ExportOutlined />}
              onClick={handleTogglePage}
              type="primary"
            >
              {isPageOpen ? '关闭页面' : '打开页面'}
            </Button>
            <Dropdown
              disabled={!canPreviewPage}
              overlay={moreMenu}
              overlayClassName={cx('page-context-dropdown', theme === 'dark' && 'dark')}
              placement="bottomRight"
              trigger={['click']}
            >
              <Button aria-label="更多页面操作" className={cx('page-context-more-button')} icon={<EllipsisOutlined />} />
            </Dropdown>
          </>
        ) : null}
      </div>
    </section>
  )
}
