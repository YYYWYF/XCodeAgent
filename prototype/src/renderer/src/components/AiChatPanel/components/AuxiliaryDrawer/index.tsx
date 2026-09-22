import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseOutlined,
  DeleteOutlined,
  EyeOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined
} from '@ant-design/icons'
import { Button, Collapse, Empty, Input, Modal, Progress, Tag } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { ApplicationConfig } from '../../../../typings'
import type { TestCasePreparationSnapshot } from '../../../../testCasePreparation'
import { testCasePreparationLabel } from '../../../../testCasePreparation'
import { cx } from '../../../../utils'
import databaseFilledIcon from '../../../../assets/icons/database-filled.svg'
import apiFilledIcon from '../../../../assets/icons/api-filled.svg'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import DataSourcesPage from '../../../DataSources/DataSourcesPage'
import ExternalApisPage from '../../../DataSources/ExternalApisPage'
import ExternalApiDetailPage from '../../../DataSources/ExternalApiDetailPage'
import DatabaseTableDetailPage from '../../../DataSources/DatabaseTableDetailPage'
import AgentFilesPage from '../../../AgentFilesPage/AgentFilesPage'
import SkillsPage from '../../../SkillsPage/SkillsPage'
import SettingsPage from '../../../SettingsPage/SettingsPage'
import { externalApiSignature, importedTables, useDataSources, type ExternalApiSource } from '../../../DataSources/catalog'
import './AuxiliaryDrawer.less'

const { Panel } = Collapse
const { TextArea } = Input

export type AuxiliaryDrawerMode =
  | 'conversation-management'
  | 'temporary-conversation'
  | 'test-preparation'
  | 'data-sources'
  | 'external-apis'
  | 'files'
  | 'skills'
  | 'settings'

/** 功能页抽屉：页面自带头部（徽标+标题+动作+关闭），壳不再叠加自己的头部。 */
type FunctionalDrawerMode = 'files' | 'skills' | 'settings'

/** 判断当前模式是否为功能页抽屉模式。 */
function isFunctionalDrawerMode(mode: AuxiliaryDrawerMode): mode is FunctionalDrawerMode {
  return mode === 'files' || mode === 'skills' || mode === 'settings'
}

/** 功能页抽屉的无障碍标题（壳头部不再渲染时，由抽屉节点自身提供可读名称）。 */
const FUNCTIONAL_DRAWER_LABELS: Record<FunctionalDrawerMode, string> = {
  files: '文件',
  skills: '技能',
  settings: '应用设置'
}

/** 数据来源详情层的定位：接口维护（含新增，外部API抽屉）或数据表详情（数据源抽屉）。 */
export type DataSourcesDetailTarget =
  | { kind: 'api'; id: string }
  | { kind: 'api-new'; domainId?: string }
  | { kind: 'table'; sourceId: string; name: string }

/** 解析数据来源详情层的头部文案：接口名+契约签名，或表名+说明。 */
function describeDataSourcesDetail(
  target: DataSourcesDetailTarget,
  sources: ReturnType<typeof useDataSources>[0]
): { title: string; subtitle: string } {
  if (target.kind === 'api-new') return { title: '新增外部 API', subtitle: '按 Postman 语义维护接口定义' }
  if (target.kind === 'api') {
    const source = sources.find(
      (item): item is ExternalApiSource => item.id === target.id && item.type === 'external_service'
    )
    return source
      ? { title: source.name, subtitle: externalApiSignature(source) }
      : { title: '外部 API', subtitle: '该接口已不在来源清单中' }
  }
  const item = importedTables(sources).find(
    (entry) => entry.sourceId === target.sourceId && entry.table.name === target.name
  )
  return item
    ? { title: item.table.name, subtitle: item.table.comment }
    : { title: target.name, subtitle: '该数据表已不在来源清单中' }
}

/** 任务管理抽屉的内容快照：由工作台在打开抽屉时向聊天面板查询获得。 */
export type ConversationManagementContent = {
  activeSessionId?: string
  editingSessionId?: string
  conversations: Array<{
    id: string
    title: string
    messageCount: number
    updatedAt: number
    active: boolean
    runStatus?: 'running' | 'stopping' | 'awaiting_user'
    /** 是否允许删除；阶段默认任务是唯一推进入口，不提供删除。 */
    deletable?: boolean
  }>
  onSelectSession: (sessionId: string) => void
  /** 删除一条用户自建任务；抽屉层负责二次确认，这里只接收确认后的结果。 */
  onDeleteSession?: (sessionId: string) => void
  /** 当前推进任务存在未结束事项时说明为什么不能新建。 */
  createConversationDisabledReason?: string
  onCreateConversation?: () => void
}

type Props = {
  /** 当前抽屉模式；null 表示收起。抽屉常挂载，收起只是滑出隐藏，展开/收起/切换模式都有统一动画。 */
  mode: AuxiliaryDrawerMode | null
  onClose: () => void
  onRetryTestCases: () => void
  testPreparation: TestCasePreparationSnapshot
  /** 任务管理模式的内容快照与切换动作；仅在 conversation-management 模式使用。 */
  conversationManagement?: ConversationManagementContent
  /** 从任务管理切换到临时问答视图。 */
  onOpenTemporaryConversation: () => void
  /** 从临时任务返回统一任务列表。 */
  onOpenConversationManagement: () => void
  /** 应用设置抽屉的配置对象与保存回调；与聊天面板收到的 application/onApplicationUpdate 同源。 */
  application: ApplicationConfig
  onApplicationSaved: (application: ApplicationConfig) => void
  /** 技能抽屉里停用技能：经工作台转交聊天面板，始终命中当前草稿的技能选择。 */
  onSkillDisabled?: (skillName: string) => void
}

type TemporaryMessage = { id: number; role: 'assistant' | 'user'; content: string }
type TemporaryConversationRecord = {
  id: string
  title: string
  messages: TemporaryMessage[]
}

/** 渲染不具备正式写权限的临时问答空间；消息状态由抽屉层持有，切换视图不丢历史。 */
function TemporaryConversation({
  messages,
  onSend
}: {
  messages: TemporaryMessage[]
  onSend: (content: string) => void
}): ReactElement {
  const [draft, setDraft] = useState('')

  /** 追加用户问题和基础演示回复，不创建任何 Workflow。 */
  const send = (): void => {
    const content = draft.trim()
    if (!content) return
    onSend(content)
    setDraft('')
  }

  return (
    <>
      <div className={cx('temporary-conversation-messages')}>
        {messages.map((message) => (
          <div className={cx('temporary-message', message.role)} key={message.id}>
            {message.content}
          </div>
        ))}
      </div>
      <div className={cx('temporary-conversation-composer')}>
        <TextArea
          autoSize={{ minRows: 1, maxRows: 5 }}
          bordered={false}
          onChange={(event) => setDraft(event.target.value)}
          onPressEnter={(event) => {
            if (event.shiftKey) return
            event.preventDefault()
            send()
          }}
          placeholder="问问当前应用、产物或代码的问题…"
          value={draft}
        />
        <button
          type="button"
          aria-label="发送"
          className={cx('temporary-conversation-send')}
          disabled={!draft.trim()}
          onClick={send}
        >
          <SendOutlined />
        </button>
      </div>
    </>
  )
}

/** 任务过滤标签键：常规 / 临时；默认展示常规任务。 */
type ConversationFilter = 'regular' | 'temporary'

/** 渲染任务管理视图：过滤标签 + 单一列表，条目内以类型标签区分并提供删除入口；切换/新建等管理动作不关闭抽屉。 */
function ConversationManagement({
  content,
  onCreateTemporaryConversation,
  onDeleteTemporaryConversation,
  onOpenTemporaryConversation,
  temporaryConversations
}: {
  content: ConversationManagementContent
  onCreateTemporaryConversation: () => void
  onDeleteTemporaryConversation: (conversationId: string) => void
  onOpenTemporaryConversation: (conversationId: string) => void
  temporaryConversations: TemporaryConversationRecord[]
}): ReactElement {
  // 默认落在常规任务：临时任务按需创建，不在初始列表中预置。
  const [filter, setFilter] = useState<ConversationFilter>('regular')
  const regularCount = content.conversations.length
  const temporaryCount = temporaryConversations.length
  // 新建动作跟随过滤标签：临时视图直接新建临时任务，常规视图新建常规任务。
  const creatingTemporary = filter === 'temporary'
  const filters: Array<{ key: ConversationFilter; label: string; count: number }> = [
    { count: regularCount, key: 'regular', label: '常规任务' },
    { count: temporaryCount, key: 'temporary', label: '临时任务' }
  ]
  // 空态：常规任务为空显示空态插图；临时任务为空时列表留白，能力说明已常显在列表上方。
  let emptyNode: ReactElement | null = null
  if (filter === 'regular' && regularCount === 0) {
    emptyNode = <Empty description="当前阶段暂无常规任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  /** 常规任务删除的二次确认；确认后交给快照回调执行，抽屉保持打开并等待列表刷新。 */
  const confirmDeleteSession = (conversationId: string, title: string): void => {
    Modal.confirm({
      cancelText: '取消',
      content: '删除后该任务的聊天记录将一并清除，不可恢复。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => content.onDeleteSession?.(conversationId),
      title: `删除任务「${title}」？`
    })
  }

  /** 临时任务删除的二次确认；临时任务只存在于抽屉本地状态中。 */
  const confirmDeleteTemporary = (conversationId: string, title: string): void => {
    Modal.confirm({
      cancelText: '取消',
      content: '删除后该临时任务的聊天记录将一并清除，不可恢复。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => onDeleteTemporaryConversation(conversationId),
      title: `删除临时任务「${title}」？`
    })
  }

  return (
    // 类名必须与抽屉根节点的模式类（conversation-management）区分开：
    // 根节点会携带模式类，若视图类同名，视图的 padding/gap 会误命中抽屉根节点。
    <div className={cx('conversation-management-view')}>
      {/* 固定 Tab 分栏与异步/潮汐任务抽屉同款交互：下划线选中态 + 数量角标，右侧挂新建按钮。 */}
      <div className={cx('conversation-tabbar')}>
        <nav aria-label="任务分段" className={cx('conversation-tabs')}>
          {filters.map((tab) => (
            <button
              aria-pressed={filter === tab.key}
              className={cx('conversation-tab', filter === tab.key && 'active')}
              key={tab.key}
              onClick={() => setFilter(tab.key)}
              type="button"
            >
              {tab.label}
              <em>{tab.count}</em>
            </button>
          ))}
        </nav>
        {creatingTemporary ? (
          <button
            aria-label="新建临时任务"
            className={cx('conversation-create-btn')}
            onClick={onCreateTemporaryConversation}
            type="button"
          >
            <PlusOutlined />
            <span>新建任务</span>
          </button>
        ) : content.onCreateConversation ? (
          <button
            aria-label={
              content.createConversationDisabledReason
                ? `新建任务（${content.createConversationDisabledReason}）`
                : '新建任务'
            }
            className={cx('conversation-create-btn')}
            disabled={Boolean(content.createConversationDisabledReason)}
            onClick={() => {
              content.onCreateConversation?.()
              // 新建是管理动作：抽屉保持打开，用户可直接看到新任务接住「当前推进」并继续管理。
            }}
            title={content.createConversationDisabledReason}
            type="button"
          >
            <PlusOutlined />
            <span>新建任务</span>
          </button>
        ) : null}
      </div>
      {/* 过滤标签下方的说明行：常规解释推进权语义，临时解释能力边界，位置与样式保持一致。 */}
      {filter === 'regular' ? (
        <p className={cx('conversation-hint')}>
          <InfoCircleOutlined aria-hidden="true" />
          仅「当前推进」的任务可执行工作流，其余任务仅供查看。
        </p>
      ) : null}
      {filter === 'temporary' ? (
        <p className={cx('conversation-hint')}>
          <InfoCircleOutlined aria-hidden="true" />
          临时任务可用于讨论问答，无法执行工作流。
        </p>
      ) : null}
      <div className={cx('conversation-list')}>
        {filter === 'regular' &&
          content.conversations.map((conversation) => {
            // 阶段默认任务不渲染删除入口；自建任务常显删除按钮，但当前推进中的任务
            // 在推进权转移前业务上不允许删除——按钮保留并置灰提示，而不是直接隐藏。
            const showDelete = Boolean(content.onDeleteSession) && Boolean(conversation.deletable)
            const deleteDisabled = showDelete && conversation.id === content.editingSessionId
            return (
              // 删除按钮绝对定位收进任务条块右缘：所有条块同宽，不再出现行宽不一致。
              <div
                className={cx('conversation-row', showDelete && 'has-delete')}
                key={conversation.id}
              >
                <button
                  aria-current={conversation.active ? 'true' : undefined}
                  className={cx('conversation-item', conversation.active && 'active')}
                  onClick={() => {
                    // 点击条目只切换右侧查看对象，抽屉保持打开：连续切换/删除等管理动作不被打断；
                    // 徽标与选中态由工作台重渲染时刷新快照跟随更新。
                    content.onSelectSession(conversation.id)
                  }}
                  type="button"
                >
                  <MessageOutlined aria-hidden="true" />
                  <span className={cx('conversation-item-title')}>{conversation.title}</span>
                  {conversation.id === content.editingSessionId &&
                  (conversation.runStatus === 'running' ||
                    conversation.runStatus === 'stopping') ? (
                    <Tag className={cx('conversation-item-state')}>
                      <LoadingOutlined spin />
                      推进中
                    </Tag>
                  ) : conversation.id === content.editingSessionId ? (
                    <Tag className={cx('conversation-item-state')}>当前推进</Tag>
                  ) : content.editingSessionId ? (
                    // 非推进任务默认即展示“仅查看”，不依赖选中切换才可见。
                    <span className={cx('conversation-item-view-only')}>
                      <EyeOutlined />
                      仅查看
                    </span>
                  ) : null}
                </button>
                {showDelete ? (
                  <button
                    aria-disabled={deleteDisabled || undefined}
                    aria-label={`删除任务 ${conversation.title}`}
                    className={cx('conversation-item-delete', deleteDisabled && 'disabled')}
                    onClick={() => {
                      if (deleteDisabled) return
                      confirmDeleteSession(conversation.id, conversation.title)
                    }}
                    title={deleteDisabled ? '当前推进中的任务不可删除，请先转移推进权' : '删除任务'}
                    type="button"
                  >
                    <DeleteOutlined />
                  </button>
                ) : null}
              </div>
            )
          })}
        {filter === 'temporary' &&
          temporaryConversations.map((conversation) => (
            <div className={cx('conversation-row', 'has-delete')} key={conversation.id}>
              <button
                className={cx('conversation-item')}
                onClick={() => onOpenTemporaryConversation(conversation.id)}
                type="button"
              >
                <MessageOutlined aria-hidden="true" />
                <span className={cx('conversation-item-title')}>{conversation.title}</span>
                <span className={cx('conversation-item-meta')}>
                  {conversation.messages.length > 1
                    ? `${conversation.messages.length} 条消息`
                    : '查阅与 Chat · 功能受限'}
                </span>
              </button>
              <button
                aria-label={`删除临时任务 ${conversation.title}`}
                className={cx('conversation-item-delete')}
                onClick={() => confirmDeleteTemporary(conversation.id, conversation.title)}
                title="删除临时任务"
                type="button"
              >
                <DeleteOutlined />
              </button>
            </div>
          ))}
        {emptyNode}
      </div>
    </div>
  )
}

/** 渲染后台用例生成的分组进度和重试入口。 */
function TestPreparation({
  onRetry,
  snapshot
}: {
  onRetry: () => void
  snapshot: TestCasePreparationSnapshot
}): ReactElement {
  const percent = snapshot.total ? Math.round((snapshot.generated / snapshot.total) * 100) : 0
  return (
    <div className={cx('test-preparation-content')}>
      <div className={cx('test-preparation-summary')}>
        <div>
          <strong>{testCasePreparationLabel(snapshot)}</strong>
          <span>需求规格说明书确认后在后台按业务场景分批生成</span>
        </div>
        <Progress
          percent={percent}
          size="small"
          status={snapshot.status === 'failed' ? 'exception' : 'active'}
        />
      </div>
      <Collapse
        bordered={false}
        defaultActiveKey={snapshot.groups.find((group) => group.status === 'generating')?.id}
      >
        {snapshot.groups.map((group) => (
          <Panel
            header={`${group.label} · ${group.generated}/${group.total}`}
            key={group.id}
            extra={
              group.status === 'completed' ? (
                <CheckCircleOutlined className={cx('completed')} />
              ) : null
            }
          >
            <p>该分组将生成页面、接口组合路径、异常处理和数据校验用例。</p>
            <p>具体前置条件、步骤和预期结果将在用例生成后继续按条目折叠展示。</p>
          </Panel>
        ))}
      </Collapse>
      <div className={cx('test-preparation-actions')}>
        {snapshot.status === 'failed' || snapshot.status === 'stale' ? (
          <Button icon={<ReloadOutlined />} onClick={onRetry}>
            重新生成
          </Button>
        ) : null}
        {snapshot.status === 'ready' ? <Tag color="success">已满足测试准备条件</Tag> : null}
      </div>
    </div>
  )
}

/** 抽屉头文案按模式切换，保持同一槽位各视图的结构一致；功能页抽屉的头部由页面自持，不在此配置。 */
const DRAWER_HEADERS: Record<Exclude<AuxiliaryDrawerMode, FunctionalDrawerMode>, { title: string; description: string }> = {
  'conversation-management': {
    title: '任务管理',
    description: '按需拆分上下文，当前阶段仅一条任务可继续推进'
  },
  'temporary-conversation': { title: '临时问答', description: '只读问答，不触发工作流' },
  'test-preparation': { title: '测试准备', description: '后台异步生成业务测试用例' },
  'data-sources': { title: '数据源', description: '已接入的数据库连接与数据表，按字段粒度绑定' },
  'external-apis': { title: '外部API', description: '已登记的接口域与外部接口，按出入参粒度绑定' }
}

/** 抽屉徽标图标按模式选择：所有模式统一走 mask 实底图标槽位，保证头部视觉规则一致。 */
const DRAWER_BADGE_ICONS: Record<Exclude<AuxiliaryDrawerMode, FunctionalDrawerMode>, string> = {
  'conversation-management': freeChatIcon,
  'temporary-conversation': freeChatIcon,
  'test-preparation': freeChatIcon,
  'data-sources': databaseFilledIcon,
  'external-apis': apiFilledIcon
}

/** 在同一辅助槽位中承载任务管理、临时问答和测试准备、数据源与外部API，禁止抽屉叠加。 */
export default function AuxiliaryDrawer(props: Props): ReactElement {
  // 数据来源模式：右侧衔接的详情维护层定位；其它模式恒为空。
  const [dataSourcesDetail, setDataSourcesDetail] = useState<DataSourcesDetailTarget | null>(null)
  const [sources] = useDataSources()
  // 一级抽屉切换或收起后，二级抽屉不再自动恢复展开——详情只随行点击显式打开，
  // 切换过程就只有一级抽屉自己的滑入/滑出动画，规则统一无例外。
  useEffect(() => {
    setDataSourcesDetail(null)
  }, [props.mode])
  // 抽屉常挂载：展示模式滞后于 props.mode——收起动画期间（0.26s）继续渲染原内容，
  // 滑出完成后才清空，避免滑出过程露出空抽屉。
  const [displayMode, setDisplayMode] = useState<AuxiliaryDrawerMode | null>(props.mode)
  useEffect(() => {
    if (props.mode) {
      setDisplayMode(props.mode)
      return
    }
    const timer = window.setTimeout(() => setDisplayMode(null), 300)
    return () => window.clearTimeout(timer)
  }, [props.mode])
  const open = props.mode != null
  // 详情层内容同样滞后清空：关闭滑出动画期间保留原内容。
  const [detailRender, setDetailRender] = useState<DataSourcesDetailTarget | null>(null)
  useEffect(() => {
    if (dataSourcesDetail) {
      setDetailRender(dataSourcesDetail)
      return
    }
    const timer = window.setTimeout(() => setDetailRender(null), 300)
    return () => window.clearTimeout(timer)
  }, [dataSourcesDetail])
  const detailHead = detailRender ? describeDataSourcesDetail(detailRender, sources) : null
  // 详情层按抽屉模式过滤目标类型：数据源只出数据表，外部API只出接口；模式切换后的残留目标不展开。
  const detailVisible = detailRender
    ? displayMode === 'data-sources'
      ? detailRender.kind === 'table'
      : displayMode === 'external-apis'
        ? detailRender.kind !== 'table'
        : false
    : false
  // 详情层只在其列表抽屉家族展开时滑出；列表抽屉收起时跟随收起。
  const familyOpen = open && (displayMode === 'data-sources' || displayMode === 'external-apis')
  const detailOpen = Boolean(familyOpen && dataSourcesDetail && detailVisible)
  // 临时任务按需创建、初始为空，可多开，但永远不具备 Workflow 与工作区写入能力。
  const [temporaryConversations, setTemporaryConversations] = useState<
    TemporaryConversationRecord[]
  >([])
  const [activeTemporaryConversationId, setActiveTemporaryConversationId] = useState('')
  const activeTemporaryConversation = temporaryConversations.find(
    (item) => item.id === activeTemporaryConversationId
  ) ||
    // 占位记录仅在临时列表为空时兜底：此时不应停留在临时问答视图。
    { id: '', title: '临时问答', messages: [] as TemporaryMessage[] }
  // 壳头部只服务非功能页模式；功能页抽屉（文件/技能/设置）的头部由页面自持。
  const shellMode: Exclude<AuxiliaryDrawerMode, FunctionalDrawerMode> | null =
    displayMode && !isFunctionalDrawerMode(displayMode) ? displayMode : null
  const header = shellMode
    ? shellMode === 'temporary-conversation'
      ? {
          title: activeTemporaryConversation.title,
          description: '查阅与 Chat 类事务，不触发 Workflow 或写入工作区'
        }
      : DRAWER_HEADERS[shellMode]
    : null

  /** 新建并进入一条功能受限的临时任务。 */
  const createTemporaryConversation = (): void => {
    const sequence = temporaryConversations.length + 1
    const conversation: TemporaryConversationRecord = {
      id: `temporary-${Date.now()}`,
      title: `临时任务 ${sequence}`,
      messages: [
        {
          id: Date.now(),
          role: 'assistant',
          content: '这是一个新的临时任务。你可以查阅、分析和讨论，但不能从这里发起正式工作流。'
        }
      ]
    }
    setTemporaryConversations((current) => [...current, conversation])
    setActiveTemporaryConversationId(conversation.id)
    props.onOpenTemporaryConversation()
  }

  /** 删除一条临时任务；若删除的是当前查看的临时任务，则回退到剩余的第一条。 */
  const deleteTemporaryConversation = (conversationId: string): void => {
    setTemporaryConversations((current) => {
      const next = current.filter((conversation) => conversation.id !== conversationId)
      if (activeTemporaryConversationId === conversationId) {
        setActiveTemporaryConversationId(next[0]?.id ?? '')
      }
      return next
    })
  }
  return (
    <>
      {/* 抽屉常挂载：滑入/滑出只发生在「关闭↔展开」的开关切换（transform 过渡）；
          已展开时切换模式原地替换内容、不做整抽屉动画——尤其二级抽屉开着时切换一级，
          抽屉体保持不动，仅详情层向右收回，中间不会出现被腾空的区域。 */}
      <section
        aria-hidden={!open}
        aria-label={
          displayMode && isFunctionalDrawerMode(displayMode)
            ? FUNCTIONAL_DRAWER_LABELS[displayMode]
            : header?.title || '辅助抽屉'
        }
        className={cx('auxiliary-drawer', displayMode, open && 'open')}
      >
      {header && shellMode ? (
      <>
      <header>
        <span aria-hidden="true" className={cx('auxiliary-drawer-badge')}>
          <span
            aria-hidden="true"
            className={cx('auxiliary-drawer-badge-icon')}
            style={
              {
                '--auxiliary-drawer-badge-source': `url("${DRAWER_BADGE_ICONS[shellMode]}")`
              } as CSSProperties
            }
          />
        </span>
        <div>
          <strong>{header.title}</strong>
          <small>{header.description}</small>
        </div>
        {shellMode === 'temporary-conversation' ? (
          <button
            aria-label="返回任务管理"
            onClick={props.onOpenConversationManagement}
            title="返回任务管理"
            type="button"
          >
            <ArrowLeftOutlined />
          </button>
        ) : null}
        <button aria-label="关闭辅助抽屉" onClick={props.onClose} type="button">
          <CloseOutlined />
        </button>
      </header>
      <div className={cx('auxiliary-drawer-body')}>
        {shellMode === 'data-sources' ? (
          <DataSourcesPage onOpenDetail={setDataSourcesDetail} />
        ) : shellMode === 'external-apis' ? (
          <ExternalApisPage onOpenDetail={setDataSourcesDetail} />
        ) : shellMode === 'conversation-management' && props.conversationManagement ? (
          <ConversationManagement
            content={props.conversationManagement}
            onCreateTemporaryConversation={createTemporaryConversation}
            onDeleteTemporaryConversation={deleteTemporaryConversation}
            onOpenTemporaryConversation={(conversationId) => {
              setActiveTemporaryConversationId(conversationId)
              props.onOpenTemporaryConversation()
            }}
            temporaryConversations={temporaryConversations}
          />
        ) : shellMode === 'conversation-management' ? (
          <Empty description="任务信息加载中" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : shellMode === 'temporary-conversation' ? (
          <TemporaryConversation
            messages={activeTemporaryConversation.messages}
            onSend={(content) => {
              const now = Date.now()
              setTemporaryConversations((current) =>
                current.map((conversation) =>
                  conversation.id === activeTemporaryConversation.id
                    ? {
                        ...conversation,
                        messages: [
                          ...conversation.messages,
                          { id: now, role: 'user', content },
                          {
                            id: now + 1,
                            role: 'assistant',
                            content:
                              '我会在只读范围内分析这个问题；如需真正修改，请切换到拥有编辑权限的常规任务。'
                          }
                        ]
                      }
                    : conversation
                )
              )
            }}
          />
        ) : (
          <TestPreparation onRetry={props.onRetryTestCases} snapshot={props.testPreparation} />
        )}
      </div>
      </>
      ) : null}
      {/* 功能页抽屉：头部由页面自持（徽标+标题+动作+关闭，与壳头部同一套语言），壳只提供容器。 */}
      {displayMode && isFunctionalDrawerMode(displayMode) ? (
        <div className={cx('auxiliary-drawer-body')}>
          {displayMode === 'files' ? (
            <AgentFilesPage onClose={props.onClose} />
          ) : displayMode === 'skills' ? (
            <SkillsPage onClose={props.onClose} onSkillDisabled={props.onSkillDisabled} />
          ) : (
            <SettingsPage
              application={props.application}
              onClose={props.onClose}
              onSaved={props.onApplicationSaved}
            />
          )}
        </div>
      ) : null}
      </section>
      {/* 数据来源详情层：衔接在列表抽屉右侧的第二个抽屉，承载接口/表的维护页。
          头部与列表抽屉同构：左侧标题，右上角关闭按钮只收起详情层、回到列表。
          两个抽屉各维护自己的目标类型：数据源只出数据表详情，外部API只出接口详情。
          渲染只看滞后的 detailRender（300ms 后清空）：切换/收起时详情层向右收回淡出，
          滑出动画期间内容保留，不会瞬间消失；是否展开由模式与目标类型匹配决定。 */}
      {detailRender && detailHead ? (
        <section
          aria-hidden={!detailOpen}
          aria-label="数据来源详情"
          className={cx('auxiliary-drawer', 'data-sources-detail', detailOpen && 'open')}
        >
          <header>
            <span aria-hidden="true" className={cx('auxiliary-drawer-badge')}>
              <span
                aria-hidden="true"
                className={cx('auxiliary-drawer-badge-icon')}
                style={
                  {
                    '--auxiliary-drawer-badge-source': `url("${
                      detailRender.kind === 'table' ? databaseFilledIcon : apiFilledIcon
                    }")`
                  } as CSSProperties
                }
              />
            </span>
            <div>
              <strong>{detailHead.title}</strong>
              <small>{detailHead.subtitle}</small>
            </div>
            <button aria-label="关闭详情" onClick={() => setDataSourcesDetail(null)} type="button">
              <CloseOutlined />
            </button>
          </header>
          <div className={cx('auxiliary-drawer-body')}>
            {detailRender.kind === 'table' ? (
              <DatabaseTableDetailPage
                onBack={() => setDataSourcesDetail(null)}
                target={detailRender}
              />
            ) : (
              <ExternalApiDetailPage onBack={() => setDataSourcesDetail(null)} target={detailRender} />
            )}
          </div>
        </section>
      ) : null}
    </>
  )
}
