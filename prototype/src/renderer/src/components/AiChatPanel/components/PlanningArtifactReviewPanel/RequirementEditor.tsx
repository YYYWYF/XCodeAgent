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

/** 编辑需求中的「API契约」分栏：扁平的接口清单——每个接口即名称、方法、路径与用途。 */
const API_METHOD_OPTIONS = ['GET', 'POST', 'PUT', 'DELETE'].map((method) => ({
  label: method,
  value: method
}))

function ApisEditor({
  draft,
  onChange
}: {
  draft: RequirementFormDraft
  onChange: DraftChange
}): ReactElement {
  const apis = Array.isArray(draft.spec.apis) ? draft.spec.apis : []
  /** 应用页面下拉选项：把调用关系挂到页面 ID 上，与审阅态的页面名显示保持一致。 */
  const pageOptions = (Array.isArray(draft.spec.pages) ? draft.spec.pages : []).map(
    (page: any, index: number) => ({
      value: String(page.pageId || `page-${index + 1}`),
      label: String(page.name || page.pageId || `页面 ${index + 1}`)
    })
  )
  /** 更新接口集合，保持其余 RequirementSpec 事实不变。 */
  const updateApis = (items: unknown[]): void =>
    onChange({ ...draft, spec: { ...draft.spec, apis: items } })
  /** 更新单条接口契约的一个字段。 */
  const patchApi = (index: number, patch: Record<string, unknown>): void =>
    updateApis(
      apis.map((item: any, itemIndex: number) =>
        itemIndex === index ? { ...item, ...patch } : item
      )
    )
  return (
    <div className={cx('requirement-editor')}>
      <div className={cx('requirement-editor-toolbar')}>
        <Text type="secondary">
          API契约即接口清单：每个接口声明名称、方法、路径与用途，应用页面通过它们读写数据。
        </Text>
        <Button
          icon={<PlusOutlined />}
          onClick={() =>
            updateApis([
              ...apis,
              {
                id: `api_${apis.length + 1}`,
                name: '',
                method: 'GET',
                path: '',
                summary: '',
                used_by_pages: []
              }
            ])
          }
          size="small"
          type="text"
        >
          新增接口
        </Button>
      </div>
      {apis.map((api: any, index: number) => (
        <section className={cx('requirement-editor-section')} key={api.id || index}>
          <header>
            <Text strong>{`接口 ${index + 1}`}</Text>
            <Button
              aria-label="删除该接口"
              className={cx('requirement-editor-remove')}
              icon={<DeleteOutlined />}
              onClick={() => updateApis(apis.filter((_item, itemIndex) => itemIndex !== index))}
              size="small"
              type="text"
            />
          </header>
          <label className={cx('requirement-editor-field')}>
            接口名称
            <Input
              onChange={(event) => patchApi(index, { name: event.target.value })}
              placeholder="如：查询我的回检"
              value={api.name || ''}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            方法与路径
            <Input
              addonBefore={
                <Select
                  aria-label="请求方法"
                  onChange={(value: string) => patchApi(index, { method: value })}
                  options={API_METHOD_OPTIONS}
                  style={{ width: 84 }}
                  value={api.method || 'GET'}
                />
              }
              onChange={(event) => patchApi(index, { path: event.target.value })}
              placeholder="/api/rechecks/my"
              value={api.path || ''}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            用途说明
            <Input
              onChange={(event) => patchApi(index, { summary: event.target.value })}
              value={api.summary || ''}
            />
          </label>
          <label className={cx('requirement-editor-field')}>
            调用页面
            <Select
              aria-label="调用页面"
              mode="multiple"
              placeholder="选择会调用该接口的应用页面"
              value={(api.used_by_pages || []) as string[]}
              onChange={(value: string[]) => patchApi(index, { used_by_pages: value })}
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
        <Text type="secondary">按步骤拆分业务流程，步骤顺序即应用页面与接口设计的推进顺序。</Text>
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
            description="确认前必须覆盖功能模块、API契约（应用页面调用哪些内部接口）、可验证的验收结果；这些事实会直接供 UI、技术规划方案和测试用例使用。"
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
      key: 'apis',
      label: 'API契约',
      children: <ApisEditor draft={draft} onChange={onChange} />
    },
    {
      key: 'pages',
      label: '应用页面与操作',
      children: <RequirementPagesEditor draft={draft} onChange={onChange} />
    },
    {
      key: 'flows',
      label: '业务流程',
      children: <FlowsEditor draft={draft} onChange={onChange} />
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
