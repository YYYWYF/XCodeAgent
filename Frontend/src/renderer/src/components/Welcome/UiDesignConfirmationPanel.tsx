import {
  CheckOutlined,
  CheckSquareOutlined,
  MessageOutlined,
  MinusSquareOutlined,
  SwapOutlined
} from '@ant-design/icons'
import { Button, Input, Typography } from 'antd'
import { useCallback, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowClarification,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'
import { cx } from '../../utils'
import './UiDesignConfirmationPanel.less'

const { Paragraph, Text } = Typography
const { TextArea } = Input

type PageDesign = {
  pageId?: string
  name?: string
  path?: string
  description?: string
  html_path?: string
  html?: string
  status?: string
}

type Props = {
  disabled?: boolean
  onSubmit: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers
  ) => void
  workflow: WorkflowRunPayload
}

// 从公开 Workflow 载荷中读取当前规划阶段的待确认内容。
function planningClarification(
  workflow: WorkflowRunPayload
): WorkflowClarification | undefined {
  const candidates = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  return candidates.find(
    (value): value is WorkflowClarification =>
      Boolean(value && typeof value === 'object')
  )
}

// 安全读取 clarification.pages 数组。
function readPages(clarification?: WorkflowClarification): PageDesign[] {
  if (!clarification) return []
  const pages = (clarification as unknown as Record<string, unknown>).pages
  return Array.isArray(pages)
    ? (pages.filter((item) => item && typeof item === 'object') as PageDesign[])
    : []
}

// 在创建规划页面展示逐页线框图设计稿，并收集用户的逐页/全部确认动作。
export default function UiDesignConfirmationPanel({
  disabled,
  onSubmit,
  workflow
}: Props): ReactElement | null {
  const clarification = planningClarification(workflow)
  const pages = useMemo(() => readPages(clarification), [clarification])
  // 本地逐页确认状态：已确认的 pageId 集合。后端权威状态以提交时的一句确认为准。
  const [confirmedPageIds, setConfirmedPageIds] = useState<Set<string>>(new Set())
  const [feedback, setFeedback] = useState('')
  const [activePageId, setActivePageId] = useState<string>('')
  // 滚动容器引用，用于锚点点击时滚动到对应卡片。
  const scrollRef = useRef<HTMLDivElement>(null)

  const confirmedCount = pages.filter((page) =>
    confirmedPageIds.has(page.pageId || '')
  ).length
  const allConfirmed = pages.length > 0 && confirmedCount === pages.length

  const togglePageConfirm = useCallback((pageId: string): void => {
    setConfirmedPageIds((current) => {
      const next = new Set(current)
      if (next.has(pageId)) {
        next.delete(pageId)
      } else {
        next.add(pageId)
      }
      return next
    })
  }, [])

  // 一键全部确认/取消：已全部确认时再点清空，否则标记全部已确认。
  const confirmAllPages = useCallback((): void => {
    setConfirmedPageIds((current) => {
      const allIds = pages.map((page) => page.pageId || '')
      const allConfirmed = allIds.length > 0 && allIds.every((id) => current.has(id))
      return allConfirmed ? new Set<string>() : new Set(allIds)
    })
  }, [pages])

  const confirmAll = (): void => {
    // 提交一句明确的全部确认信号，后端 _user_confirmed_all_designs 据此放行项目规划。
    const message = feedback.trim() || '确认全部设计稿'
    onSubmit(workflow, { ui_design_confirmation: message })
  }

  // 点击左侧锚点，滚动右侧内容区到对应卡片。
  const scrollToPage = useCallback((pageId: string): void => {
    setActivePageId(pageId)
    const container = scrollRef.current
    if (!container) return
    const target = container.querySelector<HTMLElement>(
      `[data-page-anchor="${pageId}"]`
    )
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [])

  if (!clarification) return null

  return (
    <section className={cx('planning-question-panel', 'is-ui-confirmation')}>
      <header className={cx('planning-question-panel-header')}>
        <span className={cx('planning-question-panel-icon')}>
          <CheckOutlined />
        </span>
        <div className={cx('ui-design-header-copy')}>
          <h4>确认UI设计稿</h4>
          <p>
            逐页审核已生成的线框图设计稿，确认全部页面后进入项目规划。
            「换一换」与「对话调整」即将开放，当前可先逐页确认或填写整体意见。
          </p>
        </div>
        {pages.length > 0 ? (
          <span className={cx('ui-design-progress-badge')}>
            {confirmedCount} / {pages.length}
          </span>
        ) : null}
      </header>

      {pages.length === 0 ? (
        <Paragraph type="secondary">暂无可展示的页面设计稿。</Paragraph>
      ) : (
        <>
        <div className={cx('ui-design-body')}>
          <aside className={cx('ui-design-anchor')}>
            <div className={cx('ui-design-anchor-actions')}>
              <Text className={cx('ui-design-anchor-summary')} type="secondary">
                {allConfirmed
                  ? '全部已确认'
                  : `待确认 ${pages.length - confirmedCount} 个页面`}
              </Text>
            </div>
            <nav className={cx('ui-design-anchor-list')}>
              {pages.map((page, index) => {
                const pageId = page.pageId || `page-${index + 1}`
                const confirmed = confirmedPageIds.has(pageId)
                const active = activePageId === pageId
                return (
                  <button
                    className={cx(
                      'ui-design-anchor-item',
                      confirmed && 'is-confirmed',
                      active && 'is-active'
                    )}
                    key={pageId}
                    onClick={() => scrollToPage(pageId)}
                    type="button"
                  >
                    <span className={cx('ui-design-anchor-index')}>
                      {confirmed ? <CheckOutlined /> : index + 1}
                    </span>
                    <span className={cx('ui-design-anchor-label')}>
                      {page.name || pageId}
                    </span>
                  </button>
                )
              })}
            </nav>
          </aside>

          <div className={cx('ui-design-content')} ref={scrollRef}>
            <div className={cx('ui-design-list')}>
              {pages.map((page, index) => {
                const pageId = page.pageId || `page-${index + 1}`
                const confirmed = confirmedPageIds.has(pageId)
                return (
                  <div
                    className={cx(
                      'ui-design-card',
                      confirmed && 'is-confirmed'
                    )}
                    data-page-anchor={pageId}
                    key={pageId}
                  >
                    <div className={cx('ui-design-card-header')}>
                      <div className={cx('ui-design-card-meta')}>
                        <span className={cx('ui-design-card-index')}>
                          {index + 1}
                        </span>
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
                          disabled
                          icon={<SwapOutlined />}
                          title="即将开放"
                        >
                          换一换
                        </Button>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled
                          icon={<MessageOutlined />}
                          title="即将开放"
                        >
                          对话调整
                        </Button>
                        <Button
                          className={cx('ui-design-confirm-btn')}
                          onClick={() => togglePageConfirm(pageId)}
                          type={confirmed ? 'primary' : 'default'}
                        >
                          {confirmed ? (
                            <>
                              <CheckOutlined /> 已确认
                            </>
                          ) : (
                            '确认本页'
                          )}
                        </Button>
                      </div>
                    </div>
                    {page.description ? (
                      <Paragraph className={cx('ui-design-card-desc')} type="secondary">
                        {page.description}
                      </Paragraph>
                    ) : null}
                    <div className={cx('ui-design-card-preview')}>
                      {page.html ? (
                        <iframe
                          className={cx('ui-design-iframe')}
                          sandbox="allow-same-origin"
                          srcDoc={page.html}
                          title={`设计稿-${page.name || pageId}`}
                        />
                      ) : (
                        <Paragraph type="secondary">设计稿生成中或加载失败。</Paragraph>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>

        <div className={cx('ui-design-confirm-footer')}>
          <TextArea
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="整体意见（可选）：例如确认全部设计稿 / 第2页需要改成表格布局。"
            rows={2}
            value={feedback}
          />
          <div className={cx('ui-design-confirm-footer-row')}>
            <Text className={cx('ui-design-confirm-hint')} type="secondary">
              {allConfirmed
                ? '所有页面设计稿已确认，可以进入项目规划。'
                : `请先逐页确认所有 ${pages.length} 个页面设计稿（已确认 ${confirmedCount} 个）。`}
            </Text>
            <div className={cx('ui-design-confirm-actions')}>
              <Button
                className={cx('ui-design-anchor-confirm-all')}
                disabled={disabled}
                icon={allConfirmed ? <MinusSquareOutlined /> : <CheckSquareOutlined />}
                onClick={confirmAllPages}
              >
                {allConfirmed ? '取消全部确认' : '一键全部确认'}
              </Button>
              <Button
                className={cx('ui-design-confirm-all-btn')}
                disabled={disabled || !allConfirmed}
                onClick={confirmAll}
                size="large"
                type="primary"
              >
                进入项目规划
              </Button>
            </div>
          </div>
        </div>
        </>
      )}
    </section>
  )
}
