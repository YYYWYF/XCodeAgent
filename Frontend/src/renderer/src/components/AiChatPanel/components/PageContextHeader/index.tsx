import {
  ApiOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  EllipsisOutlined,
  ExpandOutlined,
  ExportOutlined,
  FileTextOutlined,
  CloseOutlined
} from '@ant-design/icons'
import { Button, Dropdown, Popover, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PageContextHeader.less'

const { Text } = Typography

export type PageContextStatus = {
  details: string[]
  label: string
  tone: 'neutral' | 'active' | 'warning' | 'success' | 'error'
}

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
  previewLaunchError?: string
  previewLaunchLoading: boolean
  status: PageContextStatus
  targetType?: 'page' | 'api' | 'entity'
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

/** 将完整状态句拆成适合紧凑元数据布局的标签和值。 */
function splitStatusDetail(detail: string): { label: string; value: string } {
  const designMatch = detail.match(/^(页面设计|API 设计|实体设计)(已完成|尚未完成)$/)
  if (designMatch) return { label: designMatch[1], value: designMatch[2] }

  const taskMatch = detail.match(/^(开发任务)\s+(.+)$/)
  if (taskMatch) return { label: taskMatch[1], value: taskMatch[2] }

  const planMatch = detail.match(/^(开发计划)(暂未拆分)$/)
  if (planMatch) return { label: planMatch[1], value: planMatch[2] }

  return { label: '当前进度', value: detail }
}

/** 渲染当前页面或 API endpoint 的名称、路径、说明与操作。 */
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
  previewLaunchError = '',
  previewLaunchLoading,
  status,
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
      <header className={cx('page-context-detail-hero')}>
        <span className={cx('page-context-detail-icon')} aria-hidden="true">
          {targetType === 'api' ? (
            <ApiOutlined />
          ) : targetType === 'entity' ? (
            <DatabaseOutlined />
          ) : (
            <FileTextOutlined />
          )}
        </span>
        <div className={cx('page-context-detail-heading')}>
          <Text className={cx('page-context-detail-title')} strong title={pageTitle}>
            {pageTitle}
          </Text>
          <Text className={cx('page-context-detail-path')} title={normalizeDisplayPath(pagePath)}>
            {normalizeDisplayPath(pagePath)}
          </Text>
        </div>
        <span className={cx('page-context-detail-badge', `is-${status.tone}`)}>
          <i aria-hidden="true" />
          {status.label}
        </span>
      </header>

      <div className={cx('page-context-detail-body')}>
        <section className={cx('page-context-detail-summary')}>
          <Text>{description}</Text>
        </section>

        <dl className={cx('page-context-detail-metadata')}>
          {status.details.map((detail) => {
            const item = splitStatusDetail(detail)
            return (
              <div key={detail}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            )
          })}
        </dl>

        {keyFeatures.length > 0 ? (
          <section className={cx('page-context-detail-section')}>
            <Text className={cx('page-context-detail-label')}>主要功能</Text>
            <ul className={cx('page-context-detail-features')}>
              {keyFeatures.map((feature) => (
                <li key={feature}>
                  <CheckOutlined aria-hidden="true" />
                  <span>{feature}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>

      <footer>
        <ClockCircleOutlined aria-hidden="true" />
        <Text>{lastAnalyzedAt ? `${formatAnalysisAge(lastAnalyzedAt)}分析` : '尚未分析'}</Text>
      </footer>
    </div>
  )
  const moreMenuItems = [
    {
      disabled: !previewAvailable,
      icon: <ExpandOutlined />,
      key: 'fullscreen',
      label: '全屏打开页面',
      onClick: onOpenFullscreenPage
    }
  ]
  const pathText = normalizeDisplayPath(pagePath)
  const canPreviewPage = targetType === 'page' && previewAvailable
  const previewLaunchFailed = Boolean(previewLaunchError)

  return (
    <section
      className={cx('page-context-header')}
      aria-label={
        targetType === 'api'
          ? '当前 API 信息'
          : targetType === 'entity'
            ? '当前实体信息'
            : '当前页面信息'
      }
    >
      <Popover
        content={detailContent}
        overlayClassName={cx('page-context-popover', theme === 'dark' && 'dark')}
        placement="bottomLeft"
        trigger="click"
      >
        <button
          aria-label={`查看${
            targetType === 'api' ? ' API' : targetType === 'entity' ? '实体' : '页面'
          }详情：${pageTitle}`}
          className={cx('page-context-primary')}
          type="button"
        >
          <span className={cx('page-context-icon')} aria-hidden="true">
            {targetType === 'api' ? (
              <ApiOutlined />
            ) : targetType === 'entity' ? (
              <DatabaseOutlined />
            ) : (
              <FileTextOutlined />
            )}
          </span>
          <span className={cx('page-context-identity')}>
            <Text className={cx('page-context-title')} strong title={pageTitle}>
              {pageTitle}
            </Text>
            <Text className={cx('page-context-path')} title={pathText}>
              {pathText}
            </Text>
          </span>
          <span className={cx('page-context-status', `is-${status.tone}`)}>
            <i aria-hidden="true" />
            <Text>{status.label}</Text>
          </span>
        </button>
      </Popover>

      <div className={cx('page-context-actions')}>
        {targetType === 'page' ? (
          <>
            <Button
              className={cx('page-context-open-button')}
              disabled={!canPreviewPage || previewLaunchLoading}
              icon={isPageOpen ? <CloseOutlined /> : <ExportOutlined />}
              loading={previewLaunchLoading}
              onClick={handleTogglePage}
              type="primary"
            >
              {previewLaunchLoading ? '项目启动中' : isPageOpen ? '关闭预览' : '打开预览'}
            </Button>
            {!previewLaunchLoading && previewLaunchFailed ? (
              <Tooltip
                overlayClassName={cx(
                  'page-context-preview-error-tooltip',
                  theme === 'dark' && 'dark'
                )}
                placement="bottom"
                title={previewLaunchError}
              >
                <span
                  aria-label={`启动失败：${previewLaunchError}`}
                  className={cx('page-context-preview-status', 'is-error')}
                  tabIndex={0}
                >
                  <CloseCircleOutlined aria-hidden="true" />
                  <span>启动失败</span>
                </span>
              </Tooltip>
            ) : null}
            <Dropdown
              disabled={!canPreviewPage}
              menu={{ items: moreMenuItems }}
              overlayClassName={cx('page-context-dropdown', theme === 'dark' && 'dark')}
              placement="bottomRight"
              trigger={['click']}
            >
              <Button
                aria-label="更多页面操作"
                className={cx('page-context-more-button')}
                icon={<EllipsisOutlined />}
              />
            </Dropdown>
          </>
        ) : null}
      </div>
    </section>
  )
}
