import { CheckCircleFilled } from '@ant-design/icons'
import { Button, Empty, Radio, Tabs, Tag, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useLayoutEffect, useState } from 'react'
import type {
  FormalArtifactKey,
  InitializationPlanningRecord,
  PlanningAction,
  UiDesignPage
} from '../../../../initializationPlanning'
import { applyRequirementForm, type RequirementFormDraft } from '../../../../planning/requirements'
import { cx } from '../../../../utils'
import RichLoading from '../DesignProgress/RichLoading'
import UiTemplateWireframe, { templateIdForName } from '../UiTemplateWireframe'
import { buildRequirementEditorItems } from './RequirementEditor'
import { buildRequirementReviewItems } from './RequirementReview'
import TechnicalPlanReview from './TechnicalPlanReview'
import './PlanningArtifactReviewPanel.less'

const { Text, Title } = Typography

type Props = {
  artifactKey: FormalArtifactKey
  record: InitializationPlanningRecord
  disabled?: boolean
  onRequirementAction?: (action: PlanningAction) => Promise<boolean>
  onRequirementEditorClose?: () => void
  /** 审阅头部请求进入原位表单编辑；编辑是右侧本机状态，不经过工作流。 */
  onRequirementEditorOpen?: () => void
  requirementEditorOpen?: boolean
}

/** 把未知对象安全收窄为记录，避免演示数据缺字段导致整个审阅面板白屏。 */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 产物状态标签：确认/跳过/待确认的统一呈现，供各产物面板头部复用。 */
function statusTagFor(status: string): ReactElement {
  return (
    <Tag
      icon={status === 'confirmed' ? <CheckCircleFilled /> : undefined}
      color={status === 'confirmed' ? 'green' : status === 'skipped' ? 'default' : 'purple'}
    >
      {status === 'confirmed' ? '已确认' : status === 'skipped' ? '已跳过' : '待确认'}
    </Tag>
  )
}

/** 读取对象中的第一个非空展示字段。 */
function textOf(value: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = value[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }
  return ''
}

/** 读取对象中的第一个非空展示字段。 */

/**
 * 渲染一页 UI 设计稿预览：内容按该页选定的版式模板渲染低保真线框（与对话卡选模板时看到的
 * 线框同源，保证“所选即所见”）；页面尚未生成（还在等版式选择）时显示占位提示而非假稿。
 */
function UiDesignPreview({
  appName,
  generating = false,
  page
}: {
  appName: string
  generating?: boolean
  page: UiDesignPage
}): ReactElement {
  const generated = page.status === 'generated' || page.status === 'confirmed'
  return (
    <div className={cx('planning-ui-preview')}>
      <header>
        {/* 浏览器壳头部：左侧应用名，右侧当前版式名——选模板后在此直接看到生效结果。 */}
        <strong>{appName || '应用'}</strong>
        <span>{generated ? page.template : '待选择版式模板'}</span>
      </header>{' '}
      <main>
        {generating ? (
          // 生成中只在预览区显示状态：面板布局（头部/页签/动作）保持稳定不闪烁。
          <RichLoading bare title="正在按所选模板重新生成本页设计稿…" />
        ) : generated ? (
          <UiTemplateWireframe templateId={templateIdForName(page.template)} />
        ) : (
          <div className={cx('planning-ui-preview-placeholder')}>
            <Text type="secondary">
              该页还没选定版式模板——在对话卡的「确认 UI
              设计稿」中选模板并生成后，这里才会出现设计稿。
            </Text>
          </div>
        )}
      </main>
    </div>
  )
}

/**
 * 展示逐页 UI 设计状态与预览：页签在顶部横排（与本面板需求规格说明书、技术规划方案的分栏方式一致）。
 * 右侧只承载静态预览与终态标记（页签上的已确认对勾）；选模板、确认、重画等过程互动
 * 全部在对话区的「确认 UI 设计稿」卡内完成，这里不再提供第二套确认入口。
 */
function UiDesignReview({
  appName,
  pages,
  generating = false
}: {
  appName: string
  pages: UiDesignPage[]
  /** 静默改稿轮的生成中状态：预览区显示生成提示，布局保持稳定。 */
  generating?: boolean
}): ReactElement {
  const [activePageId, setActivePageId] = useState(pages[0]?.pageId || '')
  const activePage = pages.find((page) => page.pageId === activePageId) || pages[0]
  if (!activePage) return <Empty description="暂无 UI 设计稿" />
  return (
    <div className={cx('planning-ui-review')}>
      <Tabs
        activeKey={activePage.pageId}
        onChange={setActivePageId}
        items={pages.map((page) => ({
          key: page.pageId,
          label: (
            <span className={cx('planning-ui-page-label')}>
              {page.name}
              {page.status === 'confirmed' && (
                <CheckCircleFilled className={cx('planning-ui-page-check')} />
              )}
            </span>
          )
        }))}
      />
      <UiDesignPreview appName={appName} generating={generating} page={activePage} />
    </div>
  )
}

/** 根据正式产物键渲染对应结构化审阅视图。 */
export default function PlanningArtifactReviewPanel({
  artifactKey,
  record,
  disabled,
  onRequirementAction,
  onRequirementEditorClose,
  onRequirementEditorOpen,
  requirementEditorOpen = false
}: Props): ReactElement {
  // ProductPlan 仅是需求表单的内部结构，不再作为单独的用户产物或审阅入口。
  const visibleArtifactKey = artifactKey === 'product-plan' ? 'requirement-spec' : artifactKey
  // 审阅与编辑共用同一激活分栏：切换状态时停留在文档的同一章节，观感上是同一份文档。
  const [activeSection, setActiveSection] = useState('overview')
  // 原位表单草稿由面板持有：单一 Tabs 实例只切换面板内容，草稿在编辑期间跨分栏保留。
  const [requirementDraft, setRequirementDraft] = useState<RequirementFormDraft | null>(null)
  const [requirementSaving, setRequirementSaving] = useState(false)
  // 进入原位编辑时从正式记录克隆草稿；退出或保存成功后丢弃，避免残留旧稿。
  // 使用 layout 时机保证切换到编辑的同一帧就渲染表单，不闪现审阅内容。
  // 注意：必须位于下方 draft/generating 提前返回之前——hooks 不能出现在条件返回之后，
  // 否则产物从未生成（提前返回）到生成完成（完整渲染）的转换会触发 hooks 数量变化而白屏。
  useLayoutEffect(() => {
    if (requirementEditorOpen) {
      setRequirementDraft({
        spec: structuredClone(record.artifacts.requirementSpec),
        productPlan: structuredClone(record.artifacts.productPlan)
      })
    } else {
      setRequirementDraft(null)
    }
    // 草稿只在进入/退出编辑的瞬间克隆；record 的后续刷新不得打断正在编辑的草稿。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requirementEditorOpen])
  const status = record.artifactStatus[visibleArtifactKey]
  const title =
    visibleArtifactKey === 'requirement-spec'
      ? '需求规格说明书'
      : visibleArtifactKey === 'ui-designs'
        ? 'UI 设计稿'
        : '技术规划方案'
  const generating =
    visibleArtifactKey === 'requirement-spec'
      ? record.stage === 'generating_requirement_document'
      : visibleArtifactKey === 'ui-designs'
        ? record.stage === 'generating_ui_designs'
        : record.stage === 'generating_technical_plan'
  // UI 设计稿面板结构恒定（头部 + 页签 + 预览）：重新生成期间不整面板切换生成态，
  // 只把状态落到页签预览里，避免右侧闪烁——要变的也是状态，不是布局。
  const uiDesignsGenerating =
    visibleArtifactKey === 'ui-designs' && record.stage === 'generating_ui_designs'
  if (visibleArtifactKey === 'ui-designs') {
    return (
      <div className={cx('planning-artifact-review')}>
        <header className={cx('planning-artifact-review-header')}>
          <div className={cx('planning-artifact-review-heading')}>
            <div className={cx('planning-artifact-review-kicker')}>
              <Text type="secondary">正式产物</Text>
              {statusTagFor(status)}
            </div>
            <Title level={4}>UI 设计稿</Title>
          </div>
        </header>
        <UiDesignReview
          appName={textOf(asRecord(record.artifacts.requirementSpec.app_info), 'name')}
          generating={uiDesignsGenerating}
          pages={record.artifacts.uiDesigns.pages}
        />
      </div>
    )
  }
  if (status === 'draft' || generating) {
    return (
      <div className={cx('planning-artifact-review', 'is-empty')}>
        {generating ? (
          // 与“应用文件”文档区的生成态共用同一视觉（渐变轨道 + 标题 + 提示），不再使用裸 Spin。
          <RichLoading title={`正在生成${title}`} hint="完成后可在此结构化审阅。" />
        ) : (
          <Empty description={`${title}尚未生成`} />
        )}
      </div>
    )
  }
  /** 校验并通过 AG-UI 保存草稿；保存只落草稿，不推进确认门，校验失败用全局提示承载。 */
  const saveRequirementDraft = async (): Promise<void> => {
    if (!requirementDraft || !onRequirementAction) return
    setRequirementSaving(true)
    try {
      applyRequirementForm(record, requirementDraft)
      const saved = await onRequirementAction({
        action: 'save_requirements',
        artifactKey: 'requirement-spec',
        form: requirementDraft
      })
      if (saved) {
        // 保存成功立即回到审阅视图；确认门保持挂起，由对话确认卡承载下一步。
        message.success('需求规格说明书草稿已保存')
        onRequirementEditorClose?.()
      } else {
        message.error('保存未完成，请查看当前对话的错误提示后重试。', 5)
      }
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : '保存失败', 5)
    } finally {
      setRequirementSaving(false)
    }
  }
  // 需求规格说明书：预览与编辑共用同一个 Tabs 实例，只切换分栏内容，切换零抖动。
  const requirementEditing =
    visibleArtifactKey === 'requirement-spec' &&
    requirementEditorOpen &&
    Boolean(requirementDraft) &&
    Boolean(onRequirementAction)
  const requirementItems =
    requirementEditing && requirementDraft
      ? buildRequirementEditorItems(requirementDraft, setRequirementDraft)
      : buildRequirementReviewItems(record.artifacts)
  // 需求规格说明书待确认期间提供“预览/编辑”本机切换；确认与跳过后只保留只读预览。
  const requirementModeSwitch =
    visibleArtifactKey === 'requirement-spec' && status === 'pending' && onRequirementEditorOpen ? (
      <Radio.Group
        buttonStyle="solid"
        disabled={disabled}
        onChange={(event) =>
          event.target.value === 'edit' ? onRequirementEditorOpen() : onRequirementEditorClose?.()
        }
        size="small"
        value={requirementEditorOpen ? 'edit' : 'preview'}
      >
        <Radio.Button value="preview">预览</Radio.Button>
        <Radio.Button value="edit">编辑</Radio.Button>
      </Radio.Group>
    ) : null
  // 状态标签归属左侧标题区：右侧操作区只有开关与保存按钮，切换预览/编辑时开关位置不动，避免误点。
  const statusTag = (
    <Tag
      icon={status === 'confirmed' ? <CheckCircleFilled /> : undefined}
      color={status === 'confirmed' ? 'green' : status === 'skipped' ? 'default' : 'purple'}
    >
      {status === 'confirmed'
        ? '已确认'
        : status === 'skipped'
          ? '已跳过'
          : requirementEditing
            ? '编辑中'
            : '待确认'}
    </Tag>
  )
  return (
    <div className={cx('planning-artifact-review')}>
      <header className={cx('planning-artifact-review-header')}>
        <div className={cx('planning-artifact-review-heading')}>
          <div className={cx('planning-artifact-review-kicker')}>
            <Text type="secondary">正式产物</Text>
            {statusTag}
          </div>
          <Title level={4}>{title}</Title>
        </div>
        <div className={cx('planning-artifact-review-actions')}>
          {/* 操作区右对齐：保存按钮置于开关左侧，进入/退出编辑时只增减左侧空区，开关锚定右缘零位移，杜绝误点。 */}
          {requirementEditing && (
            <Button
              disabled={disabled}
              loading={requirementSaving}
              onClick={() => void saveRequirementDraft()}
              size="small"
              type="primary"
            >
              保存草稿
            </Button>
          )}
          {requirementModeSwitch}
        </div>
      </header>
      {visibleArtifactKey === 'requirement-spec' ? (
        <section className={cx('planning-requirement-editor')}>
          <Tabs activeKey={activeSection} items={requirementItems} onChange={setActiveSection} />
        </section>
      ) : (
        <TechnicalPlanReview
          requirementSpec={record.artifacts.requirementSpec}
          value={record.artifacts.technicalPlan}
        />
      )}
    </div>
  )
}
