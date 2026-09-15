import { Alert, Button, Input, Select, Typography } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { TabsProps } from 'antd'
import RequirementSpecEditor from '../../../Welcome/RequirementSpecEditor'
import RequirementAuthorizationEditor from '../../../Welcome/RequirementAuthorizationEditor'
import RequirementSpecFlowEditor from '../../../Welcome/RequirementSpecFlowSteps'
import type { RequirementFormDraft } from '../../../../planning/requirements'
import { cx } from '../../../../utils'
import RequirementPagesEditor from './RequirementPagesEditor'

const { Text } = Typography
const { TextArea } = Input

type DraftChange = (draft: RequirementFormDraft) => void

/** 编辑需求中的实体：仅描述用途、需要记录的信息及业务操作，不进行技术建模。 */
function EntitiesEditor({
  draft,
  onChange
}: {
  draft: RequirementFormDraft
  onChange: DraftChange
}): ReactElement {
  const entities = Array.isArray(draft.spec.entities) ? draft.spec.entities : []
  /** 页面下拉选项：把使用关系挂到页面 ID 上，与审阅态的页面名显示保持一致。 */
  const pageOptions = (Array.isArray(draft.spec.pages) ? draft.spec.pages : []).map(
    (page: any, index: number) => ({
      value: String(page.pageId || `page-${index + 1}`),
      label: String(page.name || page.pageId || `页面 ${index + 1}`)
    })
  )
  /** 更新实体集合，保持其余 RequirementSpec 事实不变。 */
  const updateEntities = (items: unknown[]): void =>
    onChange({ ...draft, spec: { ...draft.spec, entities: items } })
  /** 从多行业务字段说明保留业务含义，给后续技术规划方案继续细化。 */
  const fieldsFromText = (
    value: string,
    previous: Array<Record<string, unknown>>
  ): Array<Record<string, unknown>> =>
    value
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const [label, description = '业务字段'] = line.split(/[：:]/, 2)
        return {
          ...previous[index],
          name: previous[index]?.name || `field_${index + 1}`,
          label: label.trim(),
          description: description.trim()
        }
      })
  return (
    <div className={cx('requirement-editor')}>
      <div className={cx('requirement-editor-toolbar')}>
        <Text type="secondary">
          描述这是什么、需要记录什么、用户能对它做什么。数据结构与实现方式在计划阶段确定。
        </Text>
        <Button
          icon={<PlusOutlined />}
          onClick={() =>
            updateEntities([
              ...entities,
              {
                id: `entity_${entities.length + 1}`,
                name: '新实体',
                description: '',
                fields: []
              }
            ])
          }
          size="small"
          type="text"
        >
          新增实体
        </Button>
      </div>
      {entities.map((entity: any, index: number) => (
        <section className={cx('requirement-editor-section')} key={entity.id || index}>
          <header>
            <Text strong>{`实体 ${index + 1}`}</Text>
            <Button
              aria-label="删除该实体"
              className={cx('requirement-editor-remove')}
              icon={<DeleteOutlined />}
              onClick={() =>
                updateEntities(entities.filter((_item, itemIndex) => itemIndex !== index))
              }
              size="small"
              type="text"
            />
          </header>
          <label className={cx('requirement-editor-field')}>
            实体名称
            <Input
              onChange={(event) =>
                updateEntities(
                  entities.map((item: any, itemIndex: number) =>
                    itemIndex === index ? { ...item, name: event.target.value } : item
                  )
                )
              }
              value={entity.name || ''}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            实体用途
            <TextArea
              autoSize={{ minRows: 2, maxRows: 4 }}
              onChange={(event) =>
                updateEntities(
                  entities.map((item: any, itemIndex: number) =>
                    itemIndex === index ? { ...item, description: event.target.value } : item
                  )
                )
              }
              value={entity.description || ''}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            业务字段（每行“名称：业务含义”）
            <TextArea
              autoSize={{ minRows: 3 }}
              onChange={(event) =>
                updateEntities(
                  entities.map((item: any, itemIndex: number) =>
                    itemIndex === index
                      ? { ...item, fields: fieldsFromText(event.target.value, item.fields || []) }
                      : item
                  )
                )
              }
              value={(entity.fields || [])
                .map((field: any) => `${field.label || field.name}：${field.description || ''}`)
                .join('\n')}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            需要支持的业务操作（每行“操作名称()：业务目的；预期结果”）
            <TextArea
              autoSize={{ minRows: 3 }}
              placeholder={
                '查询我的回检()：查看自己提交的回检及处理状态；返回可跟踪的记录\n提交回检()：提交填报内容并进入审核；返回待审核状态\n审核回检()：给出结论，驳回时说明原因；返回审核结果'
              }
              value={(entity.business_operations || [])
                .map((item: any) =>
                  typeof item === 'string'
                    ? item
                    : String(item.name || '') + '：' + String(item.description || '')
                )
                .join('\n')}
              onChange={(event) =>
                updateEntities(
                  entities.map((item: any, itemIndex: number) =>
                    itemIndex === index
                      ? { ...item, business_operations: event.target.value.split('\n') }
                      : item
                  )
                )
              }
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            使用该实体的页面
            <Select
              aria-label="使用该实体的页面"
              mode="multiple"
              placeholder="选择会使用该实体的页面"
              value={(entity.used_by_pages || []) as string[]}
              onChange={(value: string[]) =>
                updateEntities(
                  entities.map((item: any, itemIndex: number) =>
                    itemIndex === index ? { ...item, used_by_pages: value } : item
                  )
                )
              }
              options={pageOptions}
            />
          </label>
        </section>
      ))}
    </div>
  )
}

/** 编辑核心业务流程集合：流程说明与步骤由专用流程编辑器承载。 */
function FlowsEditor({
  draft,
  onChange
}: {
  draft: RequirementFormDraft
  onChange: DraftChange
}): ReactElement {
  const flows = Array.isArray(draft.spec.business_flows) ? draft.spec.business_flows : []
  /** 替换流程集合，保留各流程内部已编辑内容。 */
  const updateFlows = (items: unknown[]): void =>
    onChange({ ...draft, spec: { ...draft.spec, business_flows: items } })
  return (
    <div className={cx('requirement-editor')}>
      <div className={cx('requirement-editor-toolbar')}>
        <Text type="secondary">按步骤拆分业务流程，步骤顺序即页面与接口设计的推进顺序。</Text>
        <Button
          icon={<PlusOutlined />}
          onClick={() =>
            updateFlows([
              ...flows,
              { id: `flow_${flows.length + 1}`, name: '新业务流程', description: '', steps: [] }
            ])
          }
          size="small"
          type="text"
        >
          新增业务流程
        </Button>
      </div>
      {flows.map((flow: any, index: number) => (
        <RequirementSpecFlowEditor
          flowIndex={index}
          item={flow}
          key={flow.id || index}
          onRemove={(flowIndex) =>
            updateFlows(flows.filter((_item, itemIndex) => itemIndex !== flowIndex))
          }
          onUpdate={(flowIndex, key, value) =>
            updateFlows(
              flows.map((item: any, itemIndex: number) =>
                itemIndex === flowIndex ? { ...item, [key]: value } : item
              )
            )
          }
        />
      ))}
    </div>
  )
}

/** 编辑应用验收标准：spec 与 productPlan 两个口径同步写回，审阅态合并展示保持一致。 */
function AcceptanceEditor({
  draft,
  onChange
}: {
  draft: RequirementFormDraft
  onChange: DraftChange
}): ReactElement {
  const specCriteria = Array.isArray(draft.spec.acceptance_criteria)
    ? (draft.spec.acceptance_criteria as unknown[]).map(String)
    : []
  /** 同步写回两侧验收口径，避免审阅态合并展示时出现编辑不掉的残留项。 */
  const updateAcceptance = (value: string): void => {
    const items = value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean)
    onChange({
      ...draft,
      spec: { ...draft.spec, acceptance_criteria: items },
      productPlan: { ...draft.productPlan, product_acceptance_criteria: items }
    })
  }
  return (
    <div className={cx('requirement-editor')}>
      <section className={cx('requirement-editor-section')}>
        <label className={cx('requirement-editor-field')}>
          应用验收标准（每行一项）
          <TextArea
            autoSize={{ minRows: 6 }}
            onChange={(event) => updateAcceptance(event.target.value)}
            placeholder="例如：回检单提交后可在“我的回检”中实时查看审核状态"
            value={specCriteria.join('\n')}
          />
        </label>
      </section>
    </div>
  )
}

/** 构建编辑态六个分栏的内容：分栏键与审阅态一致，由外层统一的 Tabs 承载。 */
export function buildRequirementEditorItems(
  draft: RequirementFormDraft,
  onChange: DraftChange
): TabsProps['items'] {
  return [
    {
      key: 'overview',
      label: '概览',
      children: (
        <div className={cx('requirement-editor')}>
          <Alert
            description="确认前必须覆盖功能模块、实体的用途与业务操作、可验证的验收结果；这些事实会直接供 UI、技术规划方案和测试用例使用。"
            message="需求规格质量要求"
            showIcon
            type="info"
          />
          <RequirementSpecEditor
            onChange={(spec) => onChange({ ...draft, spec })}
            spec={draft.spec}
          />
        </div>
      )
    },
    {
      key: 'pages',
      label: '页面与操作',
      children: <RequirementPagesEditor draft={draft} onChange={onChange} />
    },
    {
      key: 'flows',
      label: '业务流程',
      children: <FlowsEditor draft={draft} onChange={onChange} />
    },
    {
      key: 'entities',
      label: '实体',
      children: <EntitiesEditor draft={draft} onChange={onChange} />
    },
    {
      key: 'authorization',
      label: '权限需求',
      children: (
        <RequirementAuthorizationEditor
          onChange={(spec) => onChange({ ...draft, spec })}
          spec={draft.spec}
        />
      )
    },
    {
      key: 'acceptance',
      label: '验收标准',
      children: <AcceptanceEditor draft={draft} onChange={onChange} />
    }
  ]
}
