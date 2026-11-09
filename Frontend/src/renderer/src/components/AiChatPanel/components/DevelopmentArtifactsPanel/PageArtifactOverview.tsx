import { Alert, Button, Spin, Tag } from 'antd'
import {
  FileTextOutlined,
  ReloadOutlined,
  ExportOutlined,
  PictureOutlined,
  LinkOutlined,
  CodeOutlined,
  ArrowUpOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { developmentStatusText } from '../../../../developmentArtifacts'
import type { DevelopmentArtifactProgress } from '../../../../typings'
import type { PageArtifactEndpoint } from '../../../../service/pageArtifacts'
import { usePageArtifactOverview } from '../../hooks/usePageArtifactOverview'
import PageArtifactTasks from './PageArtifactTasks'
import './PageArtifactOverview.less'

type Props = {
  workspaceRoot: string
  pageId: string
  refreshKey?: string
  catalogLoading?: boolean
  onPreviewPage?: (path: string) => void
  onShowDesign: (pageId: string) => void
  onShowFiles: () => void
  onEndpointSelect: (endpoint: PageArtifactEndpoint) => void
}

/** 页面状态只读生命周期事实，任务计数不能替代页面完成或验收结论。 */
function progressLabel(progress?: DevelopmentArtifactProgress | null): string {
  return progress ? developmentStatusText(progress) : '开发状态未登记'
}

/** 展示页面开发任务、关联文件及接口，全部事实沿现有只读 AG-UI 读取。 */
export default function PageArtifactOverview({
  workspaceRoot,
  pageId,
  refreshKey,
  catalogLoading,
  onPreviewPage,
  onShowDesign,
  onShowFiles,
  onEndpointSelect
}: Props): ReactElement {
  const { overview, error, loading, reload } = usePageArtifactOverview(
    workspaceRoot,
    pageId,
    refreshKey,
    catalogLoading
  )
  if (loading)
    return (
      <div className={cx('development-artifacts-placeholder')} role="status">
        <Spin />
        <p>正在读取页面开发概览…</p>
      </div>
    )
  if (error || !overview)
    return (
      <div className={cx('page-artifact-overview')}>
        <Alert
          showIcon
          type="error"
          message="页面概览读取失败"
          description={error}
          action={<Button onClick={reload}>重新读取</Button>}
        />
      </div>
    )
  const { page, endpoints, files, issues } = overview
  const previewAvailable = Boolean(onPreviewPage && page.path)
  return (
    <article className={cx('page-artifact-overview')} aria-label={page.name + '开发概览'}>
      <p className={cx('page-artifact-breadcrumb')}>
        页面 <span>/</span> {page.name}
      </p>
      <header className={cx('page-artifact-heading')}>
        <span className={cx('page-artifact-page-icon')}>
          <FileTextOutlined />
        </span>
        <div className={cx('page-artifact-identity')}>
          <div className={cx('page-artifact-title')}>
            <h2>{page.name}</h2>
            <Tag className={cx(issues.progress && 'page-artifact-unverified')}>
              {issues.progress ? '开发状态待核验' : progressLabel(overview.progress)}
            </Tag>
            {page.confirmationStatus !== 'confirmed' && (
              <Tag>
                {page.confirmationStatus === 'pending_user_confirmation'
                  ? '页面定义待确认'
                  : page.confirmationStatus
                    ? '页面定义：' + page.confirmationStatus
                    : '页面定义确认状态未记录'}
              </Tag>
            )}
          </div>
          <p className={cx('page-artifact-path')}>{page.path || '页面路径未记录'}</p>
        </div>
        <div className={cx('page-artifact-actions')}>
          <Button icon={<PictureOutlined />} onClick={() => onShowDesign(page.id)}>
            查看设计稿
          </Button>
          <Button
            icon={<ExportOutlined />}
            disabled={!previewAvailable}
            title={
              !onPreviewPage
                ? '前端服务尚未就绪，可在预览中启动服务。'
                : !page.path
                  ? '页面路径尚未记录。'
                  : undefined
            }
            onClick={() => onPreviewPage?.(page.path)}
          >
            打开预览
          </Button>
          <Button icon={<ReloadOutlined />} aria-label="刷新页面开发概览" onClick={reload} />
        </div>
      </header>
      <p className={cx('page-artifact-description')}>
        {page.description || '产品规划尚未记录页面说明。'}
      </p>
      <div className={cx('page-artifact-main')}>
        {issues.progress ? (
          <Alert
            className={cx('page-artifact-status-notice')}
            type="warning"
            showIcon
            message={issues.progress}
          />
        ) : overview.progress?.changeReason ? (
          <p className={cx('page-artifact-change-reason')}>{overview.progress.changeReason}</p>
        ) : null}
        <PageArtifactTasks overview={overview} onReload={reload} />
        <section aria-label="任务关联文件">
          <div className={cx('page-artifact-section-heading')}>
            <h3>任务关联文件</h3>
            <button className={cx('page-artifact-text-link')} type="button" onClick={onShowFiles}>
              浏览代码 <ArrowUpOutlined />
            </button>
          </div>
          {issues.tasks ? (
            <p className={cx('page-artifact-empty')}>任务来源读取失败，无法确认关联文件。</p>
          ) : files.length ? (
            <ul className={cx('page-artifact-files')}>
              {files.map((file) => (
                <li key={file.path}>
                  <span className={cx('page-artifact-file-icon')}>
                    <CodeOutlined />
                  </span>
                  <span>
                    <strong>{file.path.split('/').pop()}</strong>
                    <small>{file.path}</small>
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={cx('page-artifact-empty')}>暂无已登记且存在的任务关联文件。</p>
          )}
          {!issues.tasks && files.length > 0 && (
            <p className={cx('page-artifact-caption')}>
              仅展示任务已声明且工作区内实际存在的文件。
            </p>
          )}
        </section>
        <section aria-label="关联接口">
          <div className={cx('page-artifact-section-heading')}>
            <h3>关联接口{!issues.endpoints && endpoints.length ? ' · ' + endpoints.length : ''}</h3>
          </div>
          {issues.endpoints ? (
            <Alert
              type="warning"
              showIcon
              message="关联接口暂不可读取"
              description={issues.endpoints}
              action={<Button onClick={reload}>重试</Button>}
            />
          ) : endpoints.length ? (
            endpoints.map((endpoint) => (
              <button
                className={cx('page-artifact-endpoint')}
                type="button"
                key={endpoint.apiContractId + ':' + endpoint.endpointId}
                onClick={() => onEndpointSelect(endpoint)}
              >
                <LinkOutlined />
                <span>
                  <strong>{endpoint.name}</strong>
                  <small>
                    {endpoint.method} {endpoint.path}
                  </small>
                </span>
                <Tag>{issues.progress ? '开发状态待核验' : progressLabel(endpoint.progress)}</Tag>
              </button>
            ))
          ) : (
            <p className={cx('page-artifact-empty')}>
              <LinkOutlined />
              技术规划未声明此页面的关联接口。
            </p>
          )}
        </section>
      </div>
    </article>
  )
}
