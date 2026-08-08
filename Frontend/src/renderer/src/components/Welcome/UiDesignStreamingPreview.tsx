import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { Button, Modal, Typography } from 'antd'
import { FullscreenOutlined } from '@ant-design/icons'
import type { WorkflowRunPayload } from '../../typings'
import { cx } from '../../utils'
import DesignRenderer from '../DesignRenderer/DesignRenderer'
import './UiDesignConfirmationPanel.less'

const { Paragraph, Text } = Typography

type PageDesign = {
  pageId?: string
  name?: string
  path?: string
  description?: string
  html_path?: string
  html?: string
  code_path?: string
  /** 设计稿 .tsx 源码（方案 B：DesignRenderer 直接消费此字段编译渲染）。 */
  code?: string
  route_path?: string
  status?: string
}

type Props = {
  workflow: WorkflowRunPayload
  /** 需求文档里登记的页面总数，用于渲染未就绪骨架。 */
  total?: number
}

// 从 workflow state/result 的 ui_designs.pages 读取已生成的页面快照。
function readStreamingPages(workflow: WorkflowRunPayload): PageDesign[] {
  for (const source of [workflow.state, workflow.result]) {
    const uiDesigns = source?.ui_designs
    if (!uiDesigns || typeof uiDesigns !== 'object') continue
    const pages = (uiDesigns as Record<string, unknown>).pages
    if (Array.isArray(pages)) {
      return pages.filter((item) => item && typeof item === 'object') as PageDesign[]
    }
  }
  return []
}

// 在 UI确认节点生成期间，流式展示已就绪的设计稿与未就绪骨架，
// 让用户边等边看到逐页完成的设计稿，而不是干等到最后一次性出现。
export default function UiDesignStreamingPreview({
  workflow,
  total
}: Props): ReactElement | null {
  const pages = useMemo(() => readStreamingPages(workflow), [workflow])
  const [fullscreenPage, setFullscreenPage] = useState<PageDesign | null>(null)
  const readyCount = pages.length
  const totalCount = Math.max(total ?? 0, readyCount)
  const pendingCount = Math.max(totalCount - readyCount, 0)

  if (readyCount === 0 && pendingCount === 0) return null

  return (
    <section className={cx('planning-question-panel', 'is-ui-confirmation', 'is-streaming')}>
      <header className={cx('planning-question-panel-header')}>
        <span className={cx('ui-design-progress-badge')}>
          {readyCount} / {totalCount || readyCount}
        </span>
        <div className={cx('ui-design-header-copy')}>
          <h4>UI设计稿生成中</h4>
          <p>
            正在逐页生成设计稿，已就绪的页面会先行展示，
            全部生成完成后即可逐页确认。
          </p>
        </div>
      </header>

      <div className={cx('ui-design-body')}>
        <div className={cx('ui-design-content')}>
          <div className={cx('ui-design-list')}>
            {pages.map((page, index) => {
              const pageId = page.pageId || `page-${index + 1}`
              return (
                <div className={cx('ui-design-card', 'is-ready')} data-page-anchor={pageId} key={pageId}>
                  <div className={cx('ui-design-card-header')}>
                    <div className={cx('ui-design-card-meta')}>
                      <span className={cx('ui-design-card-index')}>{index + 1}</span>
                      <div className={cx('ui-design-card-title')}>
                        <Text strong>{page.name || pageId}</Text>
                        {page.path ? (
                          <Text className={cx('ui-design-card-path')} code>
                            {page.path}
                          </Text>
                        ) : null}
                      </div>
                    </div>
                    <div className={cx('ui-design-card-actions')}>
                      <Button
                        className={cx('ui-design-action-btn')}
                        disabled={!page.code}
                        icon={<FullscreenOutlined />}
                        onClick={() => setFullscreenPage(page)}
                        title="全屏查看"
                      >
                        放大
                      </Button>
                    </div>
                  </div>
                  {page.description ? (
                    <Paragraph className={cx('ui-design-card-desc')} type="secondary">
                      {page.description}
                    </Paragraph>
                  ) : null}
                  <div className={cx('ui-design-card-preview')}>
                    {page.code ? (
                      <DesignRenderer
                        code={page.code}
                        title={`设计稿-${page.name || pageId}`}
                      />
                    ) : (
                      <Paragraph type="secondary">设计稿生成中或加载失败。</Paragraph>
                    )}
                  </div>
                </div>
              )
            })}

            {Array.from({ length: pendingCount }).map((_, offset) => {
              const index = readyCount + offset + 1
              return (
                <div className={cx('ui-design-card', 'is-skeleton')} key={`skeleton-${index}`}>
                  <div className={cx('ui-design-card-header')}>
                    <div className={cx('ui-design-card-meta')}>
                      <span className={cx('ui-design-card-index')}>{index}</span>
                      <div className={cx('ui-design-card-title')}>
                        <Text type="secondary">第 {index} 个页面生成中…</Text>
                      </div>
                    </div>
                  </div>
                  <div className={cx('ui-design-card-preview', 'is-skeleton-preview')}>
                    <div className={cx('ui-design-skeleton-bar')} />
                    <div className={cx('ui-design-skeleton-bar', 'is-short')} />
                    <div className={cx('ui-design-skeleton-bar')} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <Modal
        bodyStyle={{ padding: 0 }}
        footer={null}
        onCancel={() => setFullscreenPage(null)}
        open={Boolean(fullscreenPage)}
        title={fullscreenPage?.name || '设计稿预览'}
        width="90vw"
        wrapClassName={cx('ui-design-fullscreen-modal')}
      >
        {fullscreenPage && fullscreenPage.code ? (
          <DesignRenderer
            code={fullscreenPage.code}
            fullscreen
            title={`设计稿-${fullscreenPage.name || fullscreenPage.pageId}`}
          />
        ) : null}
      </Modal>
    </section>
  )
}
