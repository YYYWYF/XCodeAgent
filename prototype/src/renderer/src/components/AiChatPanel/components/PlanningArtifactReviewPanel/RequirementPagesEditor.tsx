import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Collapse, Input, Select, Switch, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { RequirementFormDraft } from '../../../../planning/requirements'
import { cx } from '../../../../utils'
import { STATE_LABELS } from './RequirementReview'

const { Text } = Typography
const { TextArea } = Input

/** 页面状态的编辑顺序与审阅态展示一致。 */
const PAGE_STATE_KEYS = Object.keys(STATE_LABELS)

/** 将未知值安全收窄为可编辑对象。 */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 将集合收窄为对象数组，过滤无法编辑的值。 */
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

/** 把字段值转换为表单需要的文本。 */
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

/** 为新增条目生成当前草稿内稳定的标识。 */
function draftId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}

type Props = {
  draft: RequirementFormDraft
  onChange: (draft: RequirementFormDraft) => void
}

/** 编辑单个用户操作卡片：名称、说明、预期结果与跳转/确认语义。 */
function ActionItem({
  action,
  index,
  pageOptions,
  onUpdate,
  onRemove
}: {
  action: Record<string, unknown>
  index: number
  pageOptions: Array<{ label: string; value: string }>
  onUpdate: (patch: Record<string, unknown>) => void
  onRemove: () => void
}): ReactElement {
  const behavior = asRecord(action.behavior)
  return (
    <article className={cx('requirement-editor-item')}>
      <header>
        <Text>{`操作 ${index + 1}`}</Text>
        <Button
          aria-label="删除该用户操作"
          className={cx('requirement-editor-remove')}
          icon={<DeleteOutlined />}
          onClick={onRemove}
          size="small"
          type="text"
        />
      </header>
      <label className={cx('requirement-editor-field')}>
        操作名称
        <Input onChange={(event) => onUpdate({ name: event.target.value })} value={textValue(action.name)} />
      </label>
      <label className={cx('requirement-editor-field')}>
        操作说明
        <TextArea
          autoSize={{ minRows: 2, maxRows: 4 }}
          onChange={(event) => onUpdate({ description: event.target.value })}
          value={textValue(action.description)}
        />
      </label>
      <label className={cx('requirement-editor-field')}>
        预期结果
        <TextArea
          autoSize={{ minRows: 2, maxRows: 4 }}
          onChange={(event) => onUpdate({ behavior: { ...behavior, expectedResult: event.target.value } })}
          value={textValue(behavior.expectedResult)}
        />
      </label>
      {behavior.type === 'navigation' && (
        <label className={cx('requirement-editor-field')}>
          目标页面
          <Select
            aria-label="目标页面"
            onChange={(value) => onUpdate({ behavior: { ...behavior, targetPageId: value } })}
            options={pageOptions}
            value={textValue(behavior.targetPageId) || undefined}
          />
        </label>
      )}
      <label className={cx('requirement-editor-field')}>
        执行前确认 <Switch checked={action.requiresConfirmation === true} onChange={(checked) => onUpdate({ requiresConfirmation: checked })} />
      </label>
    </article>
  )
}

/** 按审阅态“页面与操作”分栏编辑页面：基础信息与页面行为、状态、验收保持同一章节。 */
export default function RequirementPagesEditor({ draft, onChange }: Props): ReactElement {
  const specPages = recordList(draft.spec.pages)
  const behaviorPages = recordList(draft.productPlan.pages)
  const moduleOptions = recordList(draft.spec.feature_modules)
    .map((item) => ({ label: textValue(item.name) || textValue(item.id), value: textValue(item.id) }))
    .filter((item) => item.value)
  const pageOptions = specPages
    .map((item) => ({ label: textValue(item.name) || textValue(item.pageId), value: textValue(item.pageId) }))
    .filter((item) => item.value)
  // Collapse 默认展开第一个页面；键的归一化规则与渲染循环保持一致。
  const firstPageKey = specPages.length ? textValue(specPages[0].pageId) || 'page-0' : ''

  /** 更新 spec.pages 的页面基础字段（名称、路由、说明、模块）。 */
  const updateSpecPage = (pageId: string, patch: Record<string, unknown>): void => {
    onChange({
      ...draft,
      spec: {
        ...draft.spec,
        pages: specPages.map((item) => (item.pageId === pageId ? { ...item, ...patch } : item))
      }
    })
  }

  /** 更新 productPlan.pages 的页面行为字段；条目缺失时按当前页面信息补建，保留未展示的结构化数据。 */
  const updateBehavior = (pageId: string, patch: Record<string, unknown>): void => {
    const existing = behaviorPages.find((item) => item.pageId === pageId) || { pageId }
    const nextItems = behaviorPages.some((item) => item.pageId === pageId)
      ? behaviorPages.map((item) => (item.pageId === pageId ? { ...item, ...patch } : item))
      : [...behaviorPages, { ...existing, ...patch }]
    onChange({ ...draft, productPlan: { ...draft.productPlan, pages: nextItems } })
  }

  /** 在页面列表末尾追加一个带默认值的新页面。 */
  const addPage = (): void => {
    const next = [
      ...specPages,
      {
        pageId: draftId('page'),
        name: '新页面',
        path: '/',
        module_id: moduleOptions[0]?.value || '',
        description: ''
      }
    ]
    onChange({ ...draft, spec: { ...draft.spec, pages: next } })
  }

  /** 删除页面时同步移除 spec 与 productPlan 两侧条目，避免悬空行为数据。 */
  const removePage = (pageId: string): void => {
    onChange({
      ...draft,
      spec: { ...draft.spec, pages: specPages.filter((item) => item.pageId !== pageId) },
      productPlan: {
        ...draft.productPlan,
        pages: behaviorPages.filter((item) => item.pageId !== pageId)
      }
    })
  }

  return (
    <div className={cx('requirement-editor')}>
      <div className={cx('requirement-editor-toolbar')}>
        <Text type="secondary">{`共 ${specPages.length} 个页面；页面行为、状态与验收已合并到各页面内维护。`}</Text>
        <Button icon={<PlusOutlined />} onClick={addPage} size="small" type="text">
          新增页面
        </Button>
      </div>
      <Collapse defaultActiveKey={firstPageKey ? [firstPageKey] : []}>
        {specPages.map((page, index) => {
          const pageId = textValue(page.pageId) || `page-${index}`
          const behavior = behaviorPages.find((item) => item.pageId === pageId) || {}
          const informationItems = recordList(behavior.information_items)
          const actions = recordList(behavior.actions)
          const stateRequirements = asRecord(behavior.state_requirements)
          const acceptance = Array.isArray(behavior.acceptance_criteria)
            ? (behavior.acceptance_criteria as unknown[]).map(textValue)
            : []
          return (
            <Collapse.Panel
              extra={
                <Button
                  aria-label="删除该页面"
                  className={cx('requirement-editor-remove')}
                  icon={<DeleteOutlined />}
                  onClick={(event) => {
                    event.stopPropagation()
                    removePage(pageId)
                  }}
                  size="small"
                  type="text"
                />
              }
              header={textValue(page.name) || '未命名页面'}
              key={pageId}
            >
              <div className={cx('requirement-editor-grid')}>
                <label className={cx('requirement-editor-field')}>
                  页面名称
                  <Input
                    onChange={(event) => updateSpecPage(pageId, { name: event.target.value })}
                    placeholder="请输入页面名称"
                    value={textValue(page.name)}
                  />
                </label>
                <label className={cx('requirement-editor-field')}>
                  页面路由
                  <Input
                    onChange={(event) => updateSpecPage(pageId, { path: event.target.value })}
                    placeholder="例如 /orders"
                    value={textValue(page.path)}
                  />
                </label>
                <label className={cx('requirement-editor-field')}>
                  所属功能模块
                  <Select
                    onChange={(value) => updateSpecPage(pageId, { module_id: value })}
                    options={moduleOptions}
                    placeholder="请选择所属功能模块"
                    value={textValue(page.module_id) || undefined}
                  />
                </label>
              </div>
              <label className={cx('requirement-editor-field')}>
                页面说明
                <TextArea
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  onChange={(event) => updateSpecPage(pageId, { description: event.target.value })}
                  placeholder="请输入页面说明"
                  value={textValue(page.description)}
                />
              </label>
              <label className={cx('requirement-editor-field')}>
                页面目标
                <TextArea
                  autoSize={{ minRows: 2, maxRows: 4 }}
                  onChange={(event) => updateBehavior(pageId, { goal: event.target.value })}
                  value={textValue(behavior.goal)}
                />
              </label>
              <section className={cx('requirement-editor-section')}>
                <header>
                  <Text strong>业务信息</Text>
                  <Button
                    icon={<PlusOutlined />}
                    onClick={() =>
                      updateBehavior(pageId, {
                        information_items: [
                          ...informationItems,
                          { itemId: draftId('info'), label: '新信息项', description: '' }
                        ]
                      })
                    }
                    size="small"
                    type="text"
                  >
                    新增信息项
                  </Button>
                </header>
                {informationItems.map((item, itemIndex) => (
                  <div className={cx('requirement-editor-grid')} key={textValue(item.itemId) || itemIndex}>
                    <label className={cx('requirement-editor-field')}>
                      信息名称
                      <Input
                        onChange={(event) =>
                          updateBehavior(pageId, {
                            information_items: informationItems.map((entry, entryIndex) =>
                              entryIndex === itemIndex ? { ...entry, label: event.target.value } : entry
                            )
                          })
                        }
                        value={textValue(item.label)}
                      />
                    </label>
                    <label className={cx('requirement-editor-field')}>
                      信息说明
                      <Input
                        onChange={(event) =>
                          updateBehavior(pageId, {
                            information_items: informationItems.map((entry, entryIndex) =>
                              entryIndex === itemIndex ? { ...entry, description: event.target.value } : entry
                            )
                          })
                        }
                        value={textValue(item.description)}
                      />
                    </label>
                  </div>
                ))}
              </section>
              <section className={cx('requirement-editor-section')}>
                <header>
                  <Text strong>用户操作</Text>
                  <Button
                    icon={<PlusOutlined />}
                    onClick={() =>
                      updateBehavior(pageId, {
                        actions: [
                          ...actions,
                          {
                            actionId: draftId('action'),
                            name: '新用户操作',
                            description: '',
                            requiresConfirmation: false,
                            behavior: { type: 'business', expectedResult: '' }
                          }
                        ]
                      })
                    }
                    size="small"
                    type="text"
                  >
                    新增用户操作
                  </Button>
                </header>
                {actions.map((action, actionIndex) => (
                  <ActionItem
                    action={action}
                    index={actionIndex}
                    key={textValue(action.actionId) || actionIndex}
                    onUpdate={(patch) =>
                      updateBehavior(pageId, {
                        actions: actions.map((entry, entryIndex) =>
                          entryIndex === actionIndex ? { ...entry, ...patch } : entry
                        )
                      })
                    }
                    onRemove={() =>
                      updateBehavior(pageId, {
                        actions: actions.filter((_entry, entryIndex) => entryIndex !== actionIndex)
                      })
                    }
                    pageOptions={pageOptions}
                  />
                ))}
              </section>
              <section className={cx('requirement-editor-section')}>
                <header>
                  <Text strong>页面状态</Text>
                </header>
                <div className={cx('requirement-editor-grid')}>
                  {PAGE_STATE_KEYS.map((key) => (
                    <label className={cx('requirement-editor-field')} key={key}>
                      {STATE_LABELS[key as keyof typeof STATE_LABELS]}
                      <Input
                        onChange={(event) =>
                          updateBehavior(pageId, {
                            state_requirements: { ...stateRequirements, [key]: event.target.value }
                          })
                        }
                        value={textValue(stateRequirements[key])}
                      />
                    </label>
                  ))}
                </div>
              </section>
              <label className={cx('requirement-editor-field')}>
                页面验收标准（每行一项）
                <TextArea
                  autoSize={{ minRows: 3 }}
                  onChange={(event) =>
                    updateBehavior(pageId, { acceptance_criteria: event.target.value.split('\n') })
                  }
                  value={acceptance.join('\n')}
                />
              </label>
            </Collapse.Panel>
          )
        })}
      </Collapse>
    </div>
  )
}
