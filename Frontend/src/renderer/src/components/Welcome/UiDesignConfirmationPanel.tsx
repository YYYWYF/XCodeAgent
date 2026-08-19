import {
  ArrowUpOutlined,
  CheckOutlined,
  EyeOutlined,
  InboxOutlined,
  LayoutOutlined,
  LoadingOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Alert, Button, Input, Modal, Spin, Tag, Typography } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowClarification,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'
import { cx } from '../../utils'
import DesignRenderer from '../DesignRenderer/DesignRenderer'
import { getAvailableTemplates } from '../../service/templateService'
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
  code_path?: string
  /** 设计稿 .tsx 源码（方案 B：DesignRenderer 直接消费此字段编译渲染）。 */
  code?: string
  /** 仅用于隔离设计稿预览，不是 ProductPlan 正式路由。 */
  preview_path?: string
  /** 旧 UI Manifest 兼容字段。 */
  route_path?: string
  status?: string
  /** 用户为本页选中的页面模板 id（后端 ui_confirmation 节点回传），用于回显"已选模板"。 */
  template_id?: string
}

type Props = {
  disabled?: boolean
  onSubmit: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers
  ) => void
  workflow: WorkflowRunPayload
  /** 受控当前选中页 id（可选，用于与右侧设计稿预览面板联动）。 */
  activePageId?: string
  /** 选中页变化时通知外部（联动右侧预览）。 */
  onActivePageChange?: (pageId: string) => void
  /** 当前正在执行单页动作（选模板/换一换）的 pageId，用于右侧预览显示加载态。 */
  actionPageId?: string | null
  /** 单页动作页变化时通知外部（联动右侧加载态）。 */
  onActionPageIdChange?: (pageId: string | null) => void
  /** 是否在卡片内渲染设计稿预览（DesignRenderer）。
   *  工作台 MessageList 卡片设 false，预览由右侧"UI设计稿"tab 承接。 */
  showPreview?: boolean
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

// 在创建规划页面展示逐页设计稿，并收集用户的逐页/全部确认动作。
export default function UiDesignConfirmationPanel({
  disabled,
  onSubmit,
  workflow,
  activePageId: controlledActivePageId,
  onActivePageChange,
  actionPageId: controlledActionPageId,
  onActionPageIdChange,
  showPreview = true
}: Props): ReactElement | null {
  const clarification = planningClarification(workflow)
  const pages = useMemo(() => readPages(clarification), [clarification])
  // 最终确认被后端事实校验拒绝时，直接展示确定性错误而不是只停留在当前页面。
  const validationErrors = useMemo(() => {
    const value = clarification?.validation_errors
    return Array.isArray(value) ? value.map(String).filter(Boolean) : []
  }, [clarification])
  const [feedback, setFeedback] = useState('')
  const [internalActivePageId, setInternalActivePageId] = useState<string>('')
  // 受控模式（外部传入 activePageId）优先，否则用内部状态。
  const activePageId = controlledActivePageId ?? internalActivePageId
  const setActivePageId = useCallback(
    (next: string | ((prev: string) => string)) => {
      const resolved = typeof next === 'function' ? next(activePageId) : next
      if (onActivePageChange) onActivePageChange(resolved)
      else setInternalActivePageId(resolved)
    },
    [activePageId, onActivePageChange]
  )
  // 全屏查看的设计稿页面（null=关闭）。
  const [fullscreenPage, setFullscreenPage] = useState<PageDesign | null>(null)
  // 页面模板列表（与 DetailConfirmationPageSelector 共用同一份 templateService）。
  const templates = useMemo(() => getAvailableTemplates(), [])
  // 正在为哪个页面挑选模板（pageId，null=关闭模板选择弹窗）。
  const [templatePickerFor, setTemplatePickerFor] = useState<string | null>(null)
  // 正在执行单页动作（选模板/换一换）的 pageId：该页渲染区显示加载态，run 完成后清除。
  // 受控模式（外部传入）优先，否则用内部状态；变化时通知外部联动右侧加载态。
  const [internalActionPageId, setInternalActionPageId] = useState<string | null>(null)
  const actionPageId = controlledActionPageId ?? internalActionPageId
  const internalActionStartedRef = useRef(false)
  const setActionPageId = useCallback(
    (next: string | null | ((prev: string | null) => string | null)) => {
      const resolved = typeof next === 'function' ? next(actionPageId) : next
      if (onActionPageIdChange) onActionPageIdChange(resolved)
      else setInternalActionPageId(resolved)
    },
    [actionPageId, onActionPageIdChange]
  )
  // 斜杠提及：输入框输入 / 后弹出页面列表浮层。
  const [mentionOpen, setMentionOpen] = useState(false)
  const [mentionQuery, setMentionQuery] = useState('')
  const feedbackRef = useRef<HTMLTextAreaElement | null>(null)

  // 确认状态以后端 page.status 为权威源：选模板或换一换成功后后端置 confirmed。
  const isPageConfirmed = useCallback(
    (page: PageDesign): boolean =>
      Boolean(page.code) && page.status === 'confirmed',
    []
  )
  const confirmedCount = pages.filter((page) => isPageConfirmed(page)).length
  const allConfirmed = pages.length > 0 && confirmedCount === pages.length

  // 多页调整排队进度：从 workflow events 读 ui_confirmation.progress 的 detail。
  const adjustProgress = useMemo(() => {
    if (actionPageId !== 'adjust') return null
    const events = workflow.events || []
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i]
      if (ev.type === 'ui_confirmation.progress' && ev.data) {
        const detail = ev.data as Record<string, unknown>
        const total = typeof detail.adjust_total === 'number' ? detail.adjust_total : 0
        const ready = typeof detail.adjust_ready === 'number' ? detail.adjust_ready : 0
        if (total > 0) {
          return { ready, total, message: ev.message || '' }
        }
      }
    }
    return null
  }, [workflow.events, actionPageId])

  const confirmAll = (): void => {
    // 提交一句明确的全部确认信号，后端 _user_confirmed_all_designs 据此进入技术规划。
    const message = feedback.trim() || '确认全部设计稿'
    console.log('[ui-design-confirm] confirmAll clicked, submitting ui_design_confirmation=', message, 'workflow.runId=', workflow.runId, 'workflow.phase=', workflow.summary?.phase)
    onSubmit(workflow, { ui_design_confirmation: message })
  }

  // 用户明确选择跳过 UI 设计时，提交结构化动作并直接进入技术规划。
  const skipUiDesign = (): void => {
    if (disabled) return
    onSubmit(workflow, { ui_design_action: { action: 'skip' } })
  }

  // 逐页"选模板"或"重新生成"即时提交：后端 ui_confirmation 节点收到 ui_design_action
  // 后只更新该页设计稿并重放确认卡，不推进到技术规划。提交后该页需重新确认。
  // 同时联动右侧"UI设计稿"tab：切到该页，让渲染区显示生成加载态。
  // 选模板是同步完成（直接用模板代码，不调 LLM），不设 actionPageId 加载态；
  // 换一换走 LLM 生成，需要加载态。
  const submitPageAction = useCallback(
    (pageId: string, action: 'select_template' | 'regenerate', templateId?: string): void => {
      const payload: Record<string, unknown> = { pageId, action }
      if (templateId) payload.templateId = templateId
      if (action === 'regenerate') setActionPageId(pageId)
      onActivePageChange?.(pageId)
      onSubmit(workflow, { ui_design_action: payload })
      setTemplatePickerFor(null)
    },
    [onActivePageChange, onSubmit, workflow]
  )

  // 从输入框文本解析 @页面名 提及，映射回 pageId。
  const parseMentionedPageIds = useCallback(
    (text: string): { pageIds: string[]; instruction: string } => {
      const mentionRe = /@([^\s@]+)/g
      const mentionedNames: string[] = []
      let m: RegExpExecArray | null
      while ((m = mentionRe.exec(text)) !== null) {
        mentionedNames.push(m[1])
      }
      const pageIds: string[] = []
      for (const name of mentionedNames) {
        const found = pages.find(
          (p) => (p.name || '') === name || (p.pageId || '') === name
        )
        if (found?.pageId && !pageIds.includes(found.pageId)) {
          pageIds.push(found.pageId)
        }
      }
      const instruction = text.replace(mentionRe, '').replace(/\s+/g, ' ').trim()
      return { pageIds, instruction }
    },
    [pages]
  )

  // 多页调整提交：解析 @页面名（可选）+ 调整指令，提交 adjust_pages 动作。
  // 无 @页面名时 pageIds 为空，后端让大模型根据 instruction 自行判断调整哪些页面。
  const submitAdjustPages = useCallback((): void => {
    const { pageIds, instruction } = parseMentionedPageIds(feedback)
    if (!instruction) return
    setActionPageId('adjust')
    onSubmit(workflow, {
      ui_design_action: {
        action: 'adjust_pages',
        pageIds,
        instruction,
      },
    })
    setFeedback('')
  }, [feedback, onSubmit, parseMentionedPageIds, workflow])

  // 输入框变更：检测光标前最近的 / 触发提及浮层。
  const handleFeedbackChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>): void => {
      const val = event.target.value
      setFeedback(val)
      const el = event.target
      const caret = el.selectionStart ?? val.length
      // 光标前最近一段文本，找最后一个 / 且其后无空格（正在输入提及查询）。
      const before = val.slice(0, caret)
      const slashIdx = before.lastIndexOf('/')
      if (slashIdx < 0) {
        setMentionOpen(false)
        return
      }
      const afterSlash = before.slice(slashIdx + 1)
      // / 后到光标之间无空格才算提及输入中。
      if (/\s/.test(afterSlash)) {
        setMentionOpen(false)
        return
      }
      setMentionQuery(afterSlash)
      setMentionOpen(true)
    },
    []
  )

  // 选中某个页面：把当前 /查询 替换为 @页面名 。
  const insertMention = useCallback(
    (page: PageDesign): void => {
      const el = feedbackRef.current as unknown as
        | { focus: () => void; input?: HTMLTextAreaElement | null; resizableTextArea?: { textArea: HTMLTextAreaElement } | null }
        | null
      const val = feedback
      const caret = el?.input?.selectionStart ?? el?.resizableTextArea?.textArea.selectionStart ?? val.length
      const before = val.slice(0, caret)
      const slashIdx = before.lastIndexOf('/')
      if (slashIdx < 0) return
      const after = val.slice(caret)
      const insert = `@${page.name || page.pageId} `
      const next = before.slice(0, slashIdx) + insert + after
      const pos = slashIdx + insert.length
      setFeedback(next)
      setMentionOpen(false)
      // setFeedback 后 DOM 更新需要一帧，用 setTimeout 在更新后定位光标。
      setTimeout(() => {
        const textarea = el?.input ?? el?.resizableTextArea?.textArea
        if (textarea) {
          el?.focus()
          textarea.setSelectionRange(pos, pos)
        }
      }, 0)
    },
    [feedback]
  )

  // 提及浮层候选页面：按查询过滤，排除已在文本中 @ 过的（避免重复）。
  const mentionCandidates = useMemo(() => {
    if (!mentionOpen) return []
    const { pageIds: already } = parseMentionedPageIds(feedback)
    const query = mentionQuery.trim().toLowerCase()
    return pages.filter((p) => {
      const pid = p.pageId || ''
      if (already.includes(pid)) return false
      const name = (p.name || pid).toLowerCase()
      return !query || name.includes(query)
    })
  }, [mentionOpen, mentionQuery, feedback, pages, parseMentionedPageIds])

  // 调整按钮是否可用：输入框有非空内容 + 非 run 中。
  // 不强制要求 @页面名——用户可直接用自然语言描述，由大模型判断调整哪些页面。
  const { pageIds: mentionedPageIds } = useMemo(
    () => parseMentionedPageIds(feedback),
    [feedback, parseMentionedPageIds]
  )
  const canAdjust = !disabled && feedback.trim().length > 0

  // 在模板选择弹窗中确认选中某个模板。
  const confirmTemplatePick = useCallback(
    (templateId: string): void => {
      if (!templatePickerFor) return
      submitPageAction(templatePickerFor, 'select_template', templateId)
    },
    [templatePickerFor, submitPageAction]
  )

  // 默认选中第一个页面。
  useEffect(() => {
    if (!activePageId && pages.length > 0) {
      setActivePageId(pages[0].pageId || '')
    }
  }, [activePageId, pages])

  // 非受控模式只有在动作确实进入 running 后再回到可交互态时才清除 loading。
  // 点击后的首帧仍是旧 Workflow，不能因为 disabled 尚未更新就立即清空 actionPageId。
  useEffect(() => {
    if (controlledActionPageId !== undefined || actionPageId === null) return
    if (disabled) {
      internalActionStartedRef.current = true
      return
    }
    if (internalActionStartedRef.current) {
      internalActionStartedRef.current = false
      setInternalActionPageId(null)
    }
  }, [actionPageId, controlledActionPageId, disabled])

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
            如需视觉参考可选择模板或换一换生成设计稿，也可以跳过，直接进入技术规划。
          </p>
          {disabled && actionPageId ? (
            <span className={cx('ui-design-processing-hint')}>
              <LoadingOutlined spin />
              {actionPageId === 'adjust'
                ? '正在调整设计稿，请稍候…'
                : `正在处理「${pages.find((p) => (p.pageId || '') === actionPageId)?.name || actionPageId}」，请稍候…`}
            </span>
          ) : null}
        </div>
        {pages.length > 0 ? (
          <span className={cx('ui-design-progress-badge')}>
            {confirmedCount} / {pages.length}
          </span>
        ) : null}
      </header>

      {validationErrors.length > 0 ? (
        <Alert
          description={validationErrors.map((error) => (
            <div key={error}>{error}</div>
          ))}
          message="设计稿未通过产品事实一致性校验"
          showIcon
          type="error"
        />
      ) : null}

      {pages.length === 0 ? (
        <Paragraph type="secondary">暂无可展示的页面设计稿。</Paragraph>
      ) : showPreview ? (
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
                  const confirmed = isPageConfirmed(page)
                  const active = activePageId === pageId
                  return (
                    <button
                      className={cx(
                        'ui-design-anchor-item',
                        confirmed && 'is-confirmed',
                        active && 'is-active'
                      )}
                      key={pageId}
                      disabled={disabled}
                      onClick={() => setActivePageId(pageId)}
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

            <div className={cx('ui-design-content')}>
              {(() => {
                const activeIndex = pages.findIndex(
                  (p, i) => (p.pageId || `page-${i + 1}`) === activePageId
                )
                const page = activeIndex >= 0 ? pages[activeIndex] : pages[0]
                if (!page) return null
                const index = activeIndex >= 0 ? activeIndex : 0
                const pageId = page.pageId || `page-${index + 1}`
                const confirmed = isPageConfirmed(page)
                return (
                  <div
                    className={cx(
                      'ui-design-card',
                      confirmed && 'is-confirmed'
                    )}
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
                          {page.template_id ? (
                            <Text className={cx('ui-design-card-template-tag')} type="secondary">
                              <LayoutOutlined /> {templates.find((t) => t.manifest.id === page.template_id)?.manifest.name || page.template_id}
                            </Text>
                          ) : null}
                        </div>
                      </div>
                      <div className={cx('ui-design-card-actions')}>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled={!page.code || actionPageId === pageId || (disabled && actionPageId === 'adjust')}
                          icon={<EyeOutlined />}
                          onClick={() => setActivePageId(pageId)}
                          title={actionPageId === pageId ? '正在生成设计稿' : '在右侧查看设计稿'}
                        >
                          查看设计稿
                        </Button>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled={disabled || templates.length === 0}
                          icon={<LayoutOutlined />}
                          onClick={() => setTemplatePickerFor(pageId)}
                          title={templates.length === 0 ? '暂无可用页面模板' : '选择页面模板作为本页设计稿'}
                        >
                          选模板
                        </Button>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled={disabled}
                          icon={<ReloadOutlined />}
                          loading={actionPageId === pageId}
                          onClick={() => submitPageAction(pageId, 'regenerate')}
                          title={page.code ? '重新生成本页设计稿' : '生成本页设计稿'}
                        >
                          {actionPageId === pageId ? '生成中' : page.code ? '换一换' : '生成'}
                        </Button>
                      </div>
                    </div>
                    {page.description ? (
                      <Paragraph className={cx('ui-design-card-desc')} type="secondary">
                        {page.description}
                      </Paragraph>
                    ) : null}
                    {showPreview ? (
                    <div className={cx('ui-design-card-preview')}>
                      {actionPageId === pageId ? (
                        <div className={cx('ui-design-card-loading')}>
                          <Spin />
                          <span className={cx('ui-design-card-loading-text')}>
                            正在生成设计稿…
                          </span>
                        </div>
                      ) : page.code ? (
                        <>
                          <DesignRenderer
                            code={page.code}
                            title={`设计稿-${page.name || pageId}`}
                          />
                          {disabled && actionPageId === 'adjust' ? (
                            <div className={cx('ui-design-card-adjust-mask')}>
                              <Spin />
                              <span className={cx('ui-design-card-loading-text')}>
                                正在调整设计稿，请稍后…
                              </span>
                            </div>
                          ) : null}
                        </>
                      ) : (
                        <div className={cx('ui-design-card-empty')}>
                          <InboxOutlined className={cx('ui-design-card-empty-icon')} />
                          <span className={cx('ui-design-card-empty-title')}>
                            本页尚未生成设计稿
                          </span>
                          <span className={cx('ui-design-card-empty-hint')}>
                            选择一个页面模板，套用为该页设计稿。
                          </span>
                          <div className={cx('ui-design-card-empty-actions')}>
                            <Button
                              icon={<LayoutOutlined />}
                              disabled={disabled || templates.length === 0}
                              onClick={() => setTemplatePickerFor(pageId)}
                              type="primary"
                            >
                              选模板
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                    ) : null}
                  </div>
                )
              })()}
            </div>
          </div>
        </>
      ) : (
        // 工作台 MessageList 卡片模式：页面列表，每行一个页面 + 操作按钮。
        // 设计稿预览由右侧"UI设计稿"tab 承接，卡片内不渲染预览/放大。
        <div className={cx('ui-design-page-list')}>
          {pages.map((page, index) => {
            const pageId = page.pageId || `page-${index + 1}`
            const confirmed = isPageConfirmed(page)
            const acting = actionPageId === pageId
            return (
              <div
                className={cx(
                  'ui-design-page-row',
                  confirmed && 'is-confirmed',
                  acting && 'is-acting'
                )}
                key={pageId}
              >
                <div className={cx('ui-design-page-row-meta')}>
                  <span className={cx('ui-design-page-row-index')}>
                    {confirmed ? <CheckOutlined /> : index + 1}
                  </span>
                  <div className={cx('ui-design-page-row-title')}>
                    <Text className={cx('ui-design-page-row-name')} strong>{page.name || pageId}</Text>
                    {page.path ? (
                      <Text className={cx('ui-design-page-row-path')} code>
                        {page.path}
                      </Text>
                    ) : null}
                    {page.template_id ? (
                      <Text className={cx('ui-design-page-row-template')} type="secondary">
                        <LayoutOutlined /> {templates.find((t) => t.manifest.id === page.template_id)?.manifest.name || page.template_id}
                      </Text>
                    ) : null}
                  </div>
                  {acting ? (
                    <Tag
                      className={cx('ui-design-page-row-status', 'is-generating')}
                      icon={<LoadingOutlined spin />}
                    >
                      生成中
                    </Tag>
                  ) : confirmed ? (
                    <Tag className={cx('ui-design-page-row-status', 'is-confirmed')}>已确认</Tag>
                  ) : page.code ? (
                    <Tag className={cx('ui-design-page-row-status', 'is-pending')}>待确认</Tag>
                  ) : (
                    <Tag className={cx('ui-design-page-row-status', 'is-empty')}>未生成</Tag>
                  )}
                </div>
                <div className={cx('ui-design-page-row-actions')}>
                  <Button
                    className={cx('ui-design-action-btn')}
                    disabled={!page.code || acting || (disabled && actionPageId === 'adjust')}
                    icon={<EyeOutlined />}
                    onClick={() => {
                      setActivePageId(pageId)
                      onActivePageChange?.(pageId)
                    }}
                    title={acting ? '正在生成设计稿' : '在右侧查看设计稿'}
                  >
                    查看设计稿
                  </Button>
                  <Button
                    className={cx('ui-design-action-btn')}
                    disabled={disabled || templates.length === 0}
                    icon={<LayoutOutlined />}
                    onClick={() => setTemplatePickerFor(pageId)}
                    title={templates.length === 0 ? '暂无可用页面模板' : '选择页面模板作为本页设计稿'}
                  >
                    选模板
                  </Button>
                  <Button
                    className={cx('ui-design-action-btn')}
                    disabled={disabled}
                    icon={<ReloadOutlined />}
                    loading={acting}
                    onClick={() => submitPageAction(pageId, 'regenerate')}
                    title={page.code ? '重新生成本页设计稿' : '生成本页设计稿'}
                  >
                    {acting ? '生成中' : page.code ? '换一换' : '生成'}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div className={cx('ui-design-confirm-footer')}>
            <div className={cx('ui-design-feedback-wrap')}>
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={handleFeedbackChange}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && mentionOpen) {
                    setMentionOpen(false)
                    e.preventDefault()
                  }
                  // Enter 发送，Shift+Enter 换行。
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault()
                    if (canAdjust) submitAdjustPages()
                  }
                }}
                placeholder="描述要调整的设计稿，如「概览页改成卡片布局」。输入 / 可指定目标页面。Enter 发送，Shift+Enter 换行。"
                ref={feedbackRef}
                value={feedback}
              />
              <Button
                className={cx('ui-design-send-btn')}
                disabled={!canAdjust}
                icon={<ArrowUpOutlined />}
                onClick={submitAdjustPages}
                shape="circle"
                title={mentionedPageIds.length > 0 ? `调整选中页面（${mentionedPageIds.length}）` : '按描述调整'}
                type="primary"
              />
              {mentionOpen && mentionCandidates.length > 0 ? (
                <div className={cx('ui-design-mention-popover')}>
                  {mentionCandidates.map((p) => (
                    <button
                      className={cx('ui-design-mention-item')}
                      key={p.pageId}
                      onClick={() => insertMention(p)}
                      type="button"
                    >
                      {p.name || p.pageId}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <div className={cx('ui-design-confirm-footer-row')}>
              <Text className={cx('ui-design-confirm-hint')} type="secondary">
                {adjustProgress
                  ? `正在调整设计稿（第 ${adjustProgress.ready}/${adjustProgress.total} 页）…`
                  : allConfirmed
                    ? '所有页面设计稿已确认，可以进入技术规划。'
                    : `可为每个页面选模板或换一换生成设计稿，也可以直接跳过（已完成 ${confirmedCount}/${pages.length}）。`}
              </Text>
              <div className={cx('ui-design-confirm-actions')}>
                <Button
                  className={cx('ui-design-skip-btn')}
                  disabled={disabled}
                  onClick={skipUiDesign}
                  size="large"
                >
                  跳过 UI 设计
                </Button>
                <Button
                  className={cx('ui-design-confirm-all-btn')}
                  disabled={disabled || !allConfirmed}
                  onClick={confirmAll}
                  size="large"
                  type="primary"
                >
                  进入技术规划
                </Button>
              </div>
            </div>
          </div>

      <Modal
        bodyStyle={{ padding: 0 }}
        cancelText="关闭"
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

      {/* ---------- 页面模板选择弹窗（逐页"选模板"即时提交） ---------- */}
      <Modal
        cancelText="取消"
        footer={null}
        onCancel={() => setTemplatePickerFor(null)}
        open={Boolean(templatePickerFor)}
        title="选择页面模板"
        width={720}
        wrapClassName={cx('ui-design-template-picker-modal')}
      >
        <div className={cx('ui-design-template-cards')}>
          {(() => {
            // 当前选模板页面的已选模板 id（有 template_id 才回显，换一换/LLM 生成的无）。
            const pickerPage = pages.find((p) => (p.pageId || '') === templatePickerFor)
            const currentTemplateId = pickerPage?.template_id
            return templates.map((tpl) => {
            const desc = tpl.manifest.description || ''
            const previewImg = tpl.manifest.previewImage
            const isSelected = currentTemplateId === tpl.manifest.id
            return (
              <div
                className={cx('ui-design-template-card', isSelected && 'selected')}
                key={tpl.manifest.id}
                onClick={() => confirmTemplatePick(tpl.manifest.id)}
              >
                <div className={cx('ui-design-template-thumb')}>
                  {previewImg ? (
                    <img src={previewImg} alt={tpl.manifest.name} draggable={false} />
                  ) : (
                    <div className={cx('ui-design-template-thumb-empty')}>
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        暂无预览图
                      </Text>
                    </div>
                  )}
                </div>
                <div className={cx('ui-design-template-card-body')}>
                  <Text strong>{tpl.manifest.name}</Text>
                  <Text
                    className={cx('ui-design-template-desc')}
                    title={desc}
                    type="secondary"
                  >
                    {desc}
                  </Text>
                </div>
              </div>
            )
          })
          })()}
        </div>
        <Text className={cx('ui-design-template-picker-hint')} type="secondary">
          点击模板即套用为该页设计稿，弹窗自动关闭。
        </Text>
      </Modal>
    </section>
  )
}
