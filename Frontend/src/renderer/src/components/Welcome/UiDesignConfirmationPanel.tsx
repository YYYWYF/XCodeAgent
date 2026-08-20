import {
  ArrowUpOutlined,
  CheckOutlined,
  EyeOutlined,
  InboxOutlined,
  LayoutOutlined,
  ReloadOutlined,
  ThunderboltOutlined
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

// 轮询看门狗：一轮 no-op 轮询正常 2-3 秒内经 running→requires_user_input 完成并复位
// runInFlightRef。SSE 流被网关掐断时该序列永远走不完，runInFlightRef 卡死、轮询停摆。
// 超过此时长未完成即判定流已断，强制复位放行下一轮（只读 resume，重发安全）。
const RUN_WATCHDOG_MS = 30000
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
  /** 当前正在执行动作（选模板/换一换）的 pageId 集合，用于逐页禁用 + 右侧预览加载态。 */
  actingPageIds?: string[]
  /** 动作页集合变化时通知外部（联动右侧加载态）。 */
  onActingPageIdsChange?: (ids: string[]) => void
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
  actingPageIds: controlledActingPageIds,
  onActingPageIdsChange,
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
  // 正在执行动作（选模板/换一换）的 pageId 集合：这些页渲染区显示加载态，run 完成后清除。
  // 受控模式（外部传入）优先，否则用内部状态；变化时通知外部联动右侧加载态。
  const [internalActingPageIds, setInternalActingPageIds] = useState<string[]>([])
  const actingPageIds = controlledActingPageIds ?? internalActingPageIds
  const actingSet = useMemo(() => new Set(actingPageIds), [actingPageIds])
  const setActingPageIds = useCallback(
    (next: string[] | ((prev: string[]) => string[])) => {
      const resolved = typeof next === 'function' ? next(actingPageIds) : next
      if (onActingPageIdsChange) onActingPageIdsChange(resolved)
      else setInternalActingPageIds(resolved)
    },
    [actingPageIds, onActingPageIdsChange]
  )
  // 待提交动作队列 + 进行中 run 标记。
  // 点击即入队并加入 acting 集合（立即禁用该页按钮 + 显示"生成中"）。
  // 生成已解耦到进程级 worker pool：单页 run 只入队 + 重读清单，无需攒批。
  // run 进行中（runInFlightRef）只入队不 flush，等 run 完成后 flush 下一批。
  // 保证同一时刻最多一个 run（同 thread 不能并发 Graph run，见 checkpoint 约束）。
  // 不依赖 workflow.status 判断 run 中态（流式快照可能不可靠），只用显式 ref。
  const pendingActionsRef = useRef<
    Array<{ pageId: string; action: 'select_template' | 'regenerate'; templateId?: string }>
  >([])
  const runInFlightRef = useRef(false)
  // 本轮 run 发起时间戳（看门狗用）：SSE 流被网关中途掐断时，runInFlightRef 会永远
  // 卡在 true（等不到 requires_user_input），导致轮询停摆、卡片冻结。记录发起时间，
  // 超过 RUN_WATCHDOG_MS 未完成就强制复位放行下一轮（no-op 轮询是只读 resume，重发安全）。
  const runStartedAtRef = useRef(0)
  // 是否已观察到本轮 run 真正进入 running（后端流式推送 status=running）。
  // flush 瞬间 workflowStatus 可能还是上一轮残留的 requires_user_input（run 尚未开始
  // 流式），若据此判定 run 完成会提前重置 runInFlightRef，导致同 thread 并发新 run
  // （AsyncSqliteSaver checkpoint 链冲突，workflow 回退到 requirements 节点）。
  // 只有先观察到 running、再回到 requires_user_input 才算本轮 run 真正完成。
  const observedRunningRef = useRef(false)
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
  // 后台生成池处理中：入队（queued）或已领取（generating），尚未产出设计稿。
  // 后端把生成解耦到进程级 worker pool 后，动作 run 会立即返回这些中间状态，
  // 前端据此显示加载态并周期性轮询，直到页面进入 confirmed / generation_failed 终态。
  const isPageGenerating = useCallback(
    (page: PageDesign): boolean =>
      page.status === 'queued' || page.status === 'generating',
    []
  )
  const confirmedCount = pages.filter((page) => isPageConfirmed(page)).length
  const allConfirmed = pages.length > 0 && confirmedCount === pages.length
  // 当前处于后台生成池处理中的 pageId 列表（queued / generating），用于轮询与加载态。
  const generatingPageIds = useMemo(
    () =>
      pages
        .filter((page) => isPageGenerating(page))
        .map((page) => page.pageId || '')
        .filter(Boolean),
    [pages, isPageGenerating]
  )

  // 多页调整排队进度：从 workflow events 读 ui_confirmation.progress 的 detail。
  const adjustProgress = useMemo(() => {
    if (!actingSet.has('adjust')) return null
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
  }, [workflow.events, actingPageIds])

  const confirmAll = (): void => {
    // 提交一句明确的全部确认信号，后端 _user_confirmed_all_designs 据此进入技术规划。
    const message = feedback.trim() || '确认全部设计稿'
    onSubmit(workflow, { ui_design_confirmation: message })
  }

  // 一键并发生成所有未确认页面的设计稿：把所有 pending 页一次性提交为 multi
  // action，后端入队到进程级 worker pool 并发调度。已确认页、生成中页跳过。
  const generateAll = (): void => {
    if (disabled || runInFlightRef.current) return
    const pendingPages = pages.filter(
      (page) =>
        !isPageConfirmed(page) &&
        !isPageGenerating(page) &&
        !actingSet.has(page.pageId || '')
    )
    if (pendingPages.length === 0) return
    const batch = pendingPages.map((page) => ({
      pageId: page.pageId || '',
      action: 'regenerate' as const,
    }))
    // 立即把这些页标记为 acting（禁用按钮 + 显示生成中），与逐页点击一致。
    const actingPageIds = batch.map((b) => b.pageId)
    setActingPageIds(actingPageIds)
    runInFlightRef.current = true
    observedRunningRef.current = false
    const payload =
      batch.length === 1
        ? { pageId: batch[0].pageId, action: batch[0].action }
        : { action: 'multi', actions: batch }
    onSubmit(workflow, { ui_design_action: payload })
  }

  // 用户明确选择跳过 UI 设计时，提交结构化动作并直接进入技术规划。
  const skipUiDesign = (): void => {
    if (disabled) return
    onSubmit(workflow, { ui_design_action: { action: 'skip' } })
  }

  // 把队列中所有待处理 action 取出，提交一个 run。单 action 仍发单 action dict
  // （向后兼容），多 action 发 {action:'multi', actions:[...]}，后端入队到进程级
  // worker pool 异步生成。提交前置 runInFlight=true。
  const flushPendingActions = useCallback((): void => {
    if (pendingActionsRef.current.length === 0) return
    const batch = pendingActionsRef.current.splice(0)
    runInFlightRef.current = true
    observedRunningRef.current = false
    const payload =
      batch.length === 1
        ? { pageId: batch[0].pageId, action: batch[0].action, ...(batch[0].templateId ? { templateId: batch[0].templateId } : {}) }
        : { action: 'multi', actions: batch }
    onSubmit(workflow, { ui_design_action: payload })
  }, [onSubmit, workflow])

  // 逐页"选模板"或"重新生成"：点击即把该页加入 acting 集合（立即禁用该页按钮 +
  // 显示"生成中"）并入队。生成已解耦到进程级 worker pool，单页 run 只负责入队 +
  // 重读清单、开销极小，因此无 run 进行中时立即 flush 单页 action，不再攒批；
  // 并发由 pool 内部调度，前端不再设 3 页上限。run 进行中只入队，等 run 完成后
  // cleanup-effect 自动 flush 下一批。已在处理中（本地 acting 或后台 queued/generating）
  // 的页跳过，避免重复入队。
  const submitPageAction = useCallback(
    (pageId: string, action: 'select_template' | 'regenerate', templateId?: string): void => {
      if (actingSet.has(pageId)) return
      const page = pages.find((p) => (p.pageId || '') === pageId)
      if (page && isPageGenerating(page)) return
      setActingPageIds((prev) => (prev.includes(pageId) ? prev : [...prev, pageId]))
      pendingActionsRef.current.push({ pageId, action, templateId })
      onActivePageChange?.(pageId)
      setTemplatePickerFor(null)
      if (runInFlightRef.current) return
      // 无 run 进行中：立即提交单页动作，生成由后台 pool 异步完成。
      flushPendingActions()
    },
    [actingSet, flushPendingActions, isPageGenerating, onActivePageChange, pages, setActingPageIds]
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
    setActingPageIds(['adjust'])
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

  // 后端 run 完成后清除本批 acting 加载态并重置 runInFlight。
  // 完成信号：先观察到本轮 run 进入 running（observedRunningRef=true），再回到
  // requires_user_input。flush 瞬间 workflowStatus 可能仍是上一轮残留的
  // requires_user_input（run 尚未开始流式），此时 observedRunningRef=false，不清理，
  // 避免提前重置 runInFlightRef 导致同 thread 并发新 run（AsyncSqliteSaver checkpoint
  // 链冲突，workflow 回退到 requirements 节点）。
  const workflowStatus = workflow.summary?.status
  useEffect(() => {
    if (workflowStatus === 'running') {
      observedRunningRef.current = true
    }
    if (workflowStatus !== 'requires_user_input') return
    if (!observedRunningRef.current) return
    if (!runInFlightRef.current && pendingActionsRef.current.length === 0) return
    observedRunningRef.current = false
    runInFlightRef.current = false
    if (pendingActionsRef.current.length > 0) {
      // 下一批：保留这些 pageId 的 acting 态（run 中继续显示生成中 + 禁用按钮），
      // 清掉已完成的。必须在 flushPendingActions（splice 清空队列）之前读 pageId。
      const nextBatchPageIds = pendingActionsRef.current.map((b) => b.pageId)
      setActingPageIds(nextBatchPageIds)
      flushPendingActions()
    } else {
      setActingPageIds([])
    }
  }, [workflowStatus, flushPendingActions, setActingPageIds])

  // 后台生成池轮询：有页面处于 queued/generating 时，周期性发起 no-op resume
  // （空澄清答案 → 后端 resume 路径重读 ui-designs.json，不触发动作/确认分支），
  // 直到全部页面进入 confirmed / generation_failed 终态后停止。用 ref 持有最新的
  // onSubmit/workflow，避免父级每次渲染重建 onSubmit 导致定时器反复重启；effect 只依赖
  // generatingPageIds.length（是否仍有页面在生成）。
  const onSubmitRef = useRef(onSubmit)
  onSubmitRef.current = onSubmit
  const workflowRef = useRef(workflow)
  workflowRef.current = workflow
  useEffect(() => {
    if (generatingPageIds.length === 0) return
    const timer = setInterval(() => {
      // 无 run 在飞时才发起轮询，避免与动作 run 并发（同 thread 不能并发 Graph run）。
      if (runInFlightRef.current) {
        // 看门狗：本轮 run 超过 RUN_WATCHDOG_MS 未完成，判定 SSE 流已被网关掐断
        // （永远等不到 requires_user_input），强制复位放行下一轮，避免卡片冻结。
        if (Date.now() - runStartedAtRef.current > RUN_WATCHDOG_MS) {
          runInFlightRef.current = false
          observedRunningRef.current = false
        } else {
          return
        }
      }
      runInFlightRef.current = true
      runStartedAtRef.current = Date.now()
      observedRunningRef.current = false
      onSubmitRef.current(workflowRef.current, {})
    }, 1500)
    return () => clearInterval(timer)
  }, [generatingPageIds.length])

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
          {actingPageIds.length > 0 || generatingPageIds.length > 0 ? (
            <span className={cx('ui-design-processing-hint')}>
              {actingSet.has('adjust')
                ? '正在调整设计稿，请稍候…'
                : `正在生成 ${new Set([...actingPageIds, ...generatingPageIds]).size} 个页面设计稿，请稍候…`}
            </span>
          ) : null}
        </div>
        {pages.length > 0 ? (
          <span className={cx('ui-design-progress-badge')}>
            {confirmedCount} / {pages.length}
          </span>
        ) : null}
        {pages.length - confirmedCount > 0 ? (
          <Button
            className={cx('ui-design-generate-all-btn')}
            disabled={disabled || runInFlightRef.current || actingPageIds.length > 0 || generatingPageIds.length > 0}
            icon={<ThunderboltOutlined />}
            onClick={generateAll}
            title="一次性并发生成所有未确认页面的设计稿"
            type="primary"
          >
            全部生成
          </Button>
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
                const generating = actingSet.has(pageId) || isPageGenerating(page)
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
                          disabled={!page.code || generating || (disabled && actingSet.has('adjust'))}
                          icon={<EyeOutlined />}
                          onClick={() => setActivePageId(pageId)}
                          title={generating ? '正在生成设计稿' : '在右侧查看设计稿'}
                        >
                          查看设计稿
                        </Button>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled={generating || templates.length === 0}
                          icon={<LayoutOutlined />}
                          onClick={() => setTemplatePickerFor(pageId)}
                          title={templates.length === 0 ? '暂无可用页面模板' : '选择模板定版式，由 AI 填入本页内容并重新生成（需等待）'}
                        >
                          选模板
                        </Button>
                        <Button
                          className={cx('ui-design-action-btn')}
                          disabled={generating}
                          icon={<ReloadOutlined />}
                          onClick={() => submitPageAction(pageId, 'regenerate')}
                          title={page.code ? '重新生成本页设计稿' : '生成本页设计稿'}
                        >
                          {page.code ? '换一换' : '生成'}
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
                        {generating ? (
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
                            {disabled && actingSet.has('adjust') ? (
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
                              选择模板定版式，由 AI 填入本页内容并重新生成（需等待）。
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
            // acting 同时覆盖本地瞬时处理（actingSet）与后台生成池状态（queued/generating），
            // 二者都表示该页尚未产出设计稿、正在生成中。
            const acting = actingSet.has(pageId) || isPageGenerating(page)
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
                    <Tag className={cx('ui-design-page-row-status', 'is-generating')}>生成中</Tag>
                  ) : confirmed ? (
                    <Tag className={cx('ui-design-page-row-status', 'is-confirmed')}>已确认</Tag>
                  ) : page.status === 'generation_failed' ? (
                    <Tag className={cx('ui-design-page-row-status', 'is-failed')} color="error">生成失败</Tag>
                  ) : page.code ? (
                    <Tag className={cx('ui-design-page-row-status', 'is-pending')}>待确认</Tag>
                  ) : (
                    <Tag className={cx('ui-design-page-row-status', 'is-empty')}>未生成</Tag>
                  )}
                </div>
                <div className={cx('ui-design-page-row-actions')}>
                  <Button
                    className={cx('ui-design-action-btn')}
                    disabled={!page.code || acting || (disabled && actingSet.has('adjust'))}
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
                    disabled={acting || templates.length === 0}
                    icon={<LayoutOutlined />}
                    onClick={() => setTemplatePickerFor(pageId)}
                    title={templates.length === 0 ? '暂无可用页面模板' : '选择模板定版式，由 AI 填入本页内容并重新生成（需等待）'}
                  >
                    选模板
                  </Button>
                  <Button
                    className={cx('ui-design-action-btn', 'ui-design-action-btn-regenerate')}
                    disabled={acting}
                    icon={<ReloadOutlined />}
                    onClick={() => submitPageAction(pageId, 'regenerate')}
                    title={page.code ? '重新生成本页设计稿' : '生成本页设计稿'}
                  >
                    {page.code ? '换一换' : '生成'}
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
          模板只定版式与结构（表格/表单/页签等），点击后由 AI 把本页真实信息与操作填入该版式，
          需重新生成一次，期间该页显示「生成中」。内容与「换一换」一致，只是版式由你指定。
        </Text>
      </Modal>
    </section>
  )
}
