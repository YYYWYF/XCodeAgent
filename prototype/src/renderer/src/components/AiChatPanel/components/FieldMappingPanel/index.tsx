import { ApiOutlined } from '@ant-design/icons'
import { Button, Select } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { BindingDraft } from '../../../AppApis/model'
import {
  CONDITION_OPERATORS,
  conditionSqlFragment,
  contractFieldLabel
} from '../../../AppApis/model'
import {
  EXTERNAL_PARAM_LOCATION_LABEL,
  type ExternalApiParamLocation
} from '../../../DataSources/catalog'
import ExpressionEditorModal, { type ExpressionVariable } from './ExpressionEditorModal'
import { cx } from '../../../../utils'
import './index.less'

/** 一条映射候选：表列 / 外部出参 / 外部入参，comment 为业务说明，location 为请求部位。 */
export type MappingFieldOption = {
  name: string
  comment: string
  required?: boolean
  location?: ExternalApiParamLocation
}

/**
 * 字段映射面板的上下文：由待确认的应用API「映射绑定」澄清载荷派生。
 * 应用侧是契约出入参，目标侧是数据表列或外部接口的结构化定义。
 */
export type FieldMappingContext = {
  kind: '数据库' | '外部服务'
  objectName: string
  appMethod: string
  appPath: string
  sourceName: string
  targetName: string
  op: string
  columns: MappingFieldOption[]
  requestParams: MappingFieldOption[]
  inputParams: Array<{ code: string; name: string; summary: string; required: boolean }>
  outputs: Array<{ code: string; name: string }>
}

/** 目录里的一个应用API条目：只展示名称，方法与来源在右侧内容区呈现。 */
export type FieldMappingApiItem = {
  id: string
  name: string
}

type Props = {
  /** 应用API目录：以应用的全部接口为导航主体。 */
  apis: FieldMappingApiItem[]
  /** 当前绑定工作流的对象：编辑器只承载它，其余条目展示只读说明。 */
  activeApiId: string
  /** 目录当前选中的条目：由面板层持有，「打开字段映射」用它定位到具体API。 */
  selectedApiId: string
  onSelectApi: (id: string) => void
  /** 当前绑定对象的契约上下文与来源结构；无进行中绑定时为 null。 */
  context: FieldMappingContext | null
  draft: BindingDraft | null
  submitting?: boolean
  /** 只读态：已确认绑定的常驻查看，控件禁用且不显示保存/确认动作。 */
  readOnly?: boolean
  onChange: (draft: BindingDraft) => void
  /** 保存当前草稿：写回应用API状态但不触发下一步。 */
  onSave: (draft: BindingDraft) => void
  /** 保存并确认：提交当前草稿，工作流进入下一步。 */
  onConfirm: (draft: BindingDraft) => void
}

/** 从映射标签还原来源字段名：兼容「来源 · name（说明）」与「name（说明）」。 */
function sourceNameOf(label: string): string {
  const short = label.match(/^(?:.+? · )?(.+?)（/)
  return short ? short[1] : ''
}

/**
 * 由当前草稿实时生成模板 SQL 预览（数据库绑定）：与适配代码生成器共用 conditionSqlFragment，
 * 「表字段 + 操作符 + 取值来源」的每处改动都即时反映到语句上，是表绑定的闭环结果。
 */
function buildSqlPreview(draft: BindingDraft, table: string): string {
  const mappedSetters = draft.setters.filter((row) => row.column)
  const whereLines = draft.conditions.map((row, index) => {
    const column = row.column || '?'
    const fragment = conditionSqlFragment(
      column,
      row.operator,
      row.fixed ? ':currentUser' : `:${column}`
    )
    return `${index === 0 ? 'WHERE' : '  AND'} ${fragment}`
  })
  if (draft.op === '删除') {
    return [`DELETE FROM ${table}`, ...whereLines].join('\n')
  }
  if (draft.op === '新增') {
    const insertColumns = mappedSetters.map((row) => row.column).join(', ')
    const insertValues = mappedSetters.map((row) => `:${row.column}`).join(', ')
    return `INSERT INTO ${table} (${insertColumns})\nVALUES (${insertValues})`
  }
  if (draft.op === '修改') {
    const setList = mappedSetters.map((row) => `${row.column} = :${row.column}`).join(', ')
    return [`UPDATE ${table}`, `SET ${setList}`, ...whereLines].join('\n')
  }
  const selectList = draft.mappings
    .map((row) => (row.matched ? sourceNameOf(row.sourceLabel) : `/* ${row.field} 未映射 */`))
    .join(', ')
  const lines = [`SELECT ${selectList}`, `FROM ${table}`, ...whereLines]
  if (draft.orderBy) lines.push(`ORDER BY ${draft.orderBy}`)
  return lines.join('\n')
}

/** 外部参数下拉选项文案：部位前缀让用户直观看到值会发到请求的哪个部位。 */
function externalParamLabel(param: MappingFieldOption): string {
  const location = param.location ? `${EXTERNAL_PARAM_LOCATION_LABEL[param.location]} · ` : ''
  return `${param.name}（${location}${param.comment}）`
}

/**
 * fx 取值配置钮：点击打开函数表达式编辑弹框。值即草稿 expressions 里的字符串
 * （空串 = 透传）；按钮常态灰显「透传」，配置过则高亮显示表达式摘要。
 */
function ExpressionFx({
  disabled,
  onChange,
  paramLabel,
  value,
  variables
}: {
  disabled: boolean
  value: string
  /** 弹框标题用的契约字段标签。 */
  paramLabel: string
  /** 可插入的变量候选：当前登录用户 + 入参侧的契约入参。 */
  variables: ExpressionVariable[]
  onChange: (value: string) => void
}): ReactElement {
  const [open, setOpen] = useState(false)
  /** 触发钮文案：透传灰显；配置过则高亮显示表达式摘要。 */
  const summary = value === '' ? '透传' : value === ':currentUser' ? '登录用户' : value
  return (
    <>
      <button
        aria-label="配置取值表达式"
        className={cx('field-mapping-fx', value !== '' && 'active')}
        disabled={disabled}
        onClick={() => setOpen(true)}
        title={value || '直接透传'}
        type="button"
      >
        <em>fx</em>
        <span>{summary}</span>
      </button>
      <ExpressionEditorModal
        onCancel={() => setOpen(false)}
        onOk={(next) => {
          onChange(next)
          setOpen(false)
        }}
        open={open}
        paramLabel={paramLabel}
        value={value}
        variables={variables}
      />
    </>
  )
}

/**
 * 字段映射面板：应用API映射绑定的编辑工作台。左侧以应用的全部API为目录
 * （状态点标注进行中/已确认），右侧是当前绑定对象的映射编辑区——数据库形态为
 * 模板槽位 + SQL 预览卡片，外部API形态把入参按「路径参数/查询参数/请求体」分组装配、
 * 出参取响应 data 层字段、fx 表达式做简单加工，底部请求预览实时拼出实际调用。
 * 确认动作在工作流节点卡上，这里只承载配置本身。
 */
export default function FieldMappingPanel({
  apis,
  activeApiId,
  selectedApiId,
  onSelectApi,
  context,
  draft,
  submitting = false,
  readOnly = false,
  onChange,
  onSave,
  onConfirm
}: Props): ReactElement {
  const selected = apis.find((item) => item.id === selectedApiId)
  // 有上下文与草稿即可渲染（活动绑定为可编辑，已确认绑定为只读）；activeApiId 仅用于行高亮。
  const editing = Boolean(selected && context && draft)

  return (
    <section aria-label="字段映射" className={cx('field-mapping-panel')} role="group">
      <aside aria-label="应用API目录" className={cx('field-mapping-directory')}>
        <div className={cx('field-mapping-directory-body')}>
          <div className={cx('field-mapping-directory-title')}>应用API</div>
          <div className={cx('field-mapping-directory-list')}>
            {apis.map((item) => (
              <button
                key={item.id}
                aria-label={item.name}
                className={cx(
                  'field-mapping-api-row',
                  selectedApiId === item.id && 'selected',
                  item.id === activeApiId && 'active'
                )}
                onClick={() => onSelectApi(item.id)}
                type="button"
              >
                <ApiOutlined aria-hidden="true" />
                <span className={cx('field-mapping-api-name')}>{item.name}</span>
              </button>
            ))}
          </div>
        </div>
      </aside>
      <main className={cx('field-mapping-content')}>
        {!selected || !editing || !context || !draft ? (
          <div className={cx('field-mapping-empty-state')}>
            <strong>{selected ? selected.name : '字段映射'}</strong>
            <span>
              {readOnly
                ? '该应用API尚未开始数据绑定；完成映射绑定后，这里会常驻展示其映射配置。'
                : '该应用API当前没有进行中的映射绑定；已确认的绑定与接口调试视图可在「开发产物」查看。'}
            </span>
          </div>
        ) : (
          <FieldMappingEditor
            context={context}
            draft={draft}
            locked={submitting || readOnly}
            submitting={submitting}
            onChange={onChange}
            onSave={onSave}
            onConfirm={onConfirm}
          />
        )}
      </main>
    </section>
  )
}

/** 编辑区入参：当前绑定对象的契约上下文 + 共享草稿；locked 时控件禁用且隐藏动作区。 */
type EditorProps = {
  context: FieldMappingContext
  draft: BindingDraft
  locked: boolean
  submitting: boolean
  onChange: (draft: BindingDraft) => void
  onSave: (draft: BindingDraft) => void
  onConfirm: (draft: BindingDraft) => void
}

/** 映射编辑区：契约头（动作按钮跟随标题行右侧，只读态隐藏）+ 分节卡片；行网格全段共用，纵向严格对齐。 */
function FieldMappingEditor({ context, draft, locked, submitting, onChange, onSave, onConfirm }: EditorProps): ReactElement {
  const isExternal = context.kind === '外部服务'
  const columnOptions = context.columns.map((column) => ({
    value: column.name,
    label: `${column.name}（${column.comment}）`
  }))
  const externalParamOptions = context.requestParams.map((param) => ({
    value: param.name,
    label: externalParamLabel(param)
  }))
  const operatorOptions = CONDITION_OPERATORS.map((value) => ({ value }))
  /** 契约入参展示标签：按参数名回查英文标识。 */
  const inputLabel = (name: string): string => {
    const param = context.inputParams.find((item) => item.name === name)
    return contractFieldLabel(param?.code || '', name)
  }
  /** 契约出参展示标签：按出参名回查英文标识。 */
  const outputLabel = (name: string): string => {
    const output = context.outputs.find((item) => item.name === name)
    return contractFieldLabel(output?.code || '', name)
  }

  /** 更新草稿并上抛：草稿正本由面板层持有，切目录/Tab 不丢。 */
  const patchDraft = (patch: Partial<BindingDraft>): void => {
    onChange({ ...draft, ...patch })
  }
  /** 调整模板条件行的落列或操作符。 */
  const updateCondition = (
    index: number,
    patch: Partial<BindingDraft['conditions'][number]>
  ): void => {
    patchDraft({
      conditions: draft.conditions.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row
      )
    })
  }
  /** 调整模板写入行的落列。 */
  const updateSetter = (index: number, patch: Partial<BindingDraft['setters'][number]>): void => {
    patchDraft({
      setters: draft.setters.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row))
    })
  }
  /** 选定某出参的来源字段：以「name（说明）」写入映射标签。 */
  const updateMapping = (field: string, sourceField: string): void => {
    const column = context.columns.find((item) => item.name === sourceField)
    if (!column) return
    patchDraft({
      mappings: draft.mappings.map((row) =>
        row.field === field
          ? {
              ...row,
              matched: true,
              matchType: 'manual' as const,
              sourceLabel: `${column.name}（${column.comment}）`
            }
          : row
      )
    })
  }
  /** 调整函数表达式；置空即直接透传。 */
  const updateExpression = (key: string, expression: string): void => {
    patchDraft({ expressions: { ...draft.expressions, [key]: expression } })
  }
  /** 调整契约入参对齐的外部入参；清空选择即回到待对齐。 */
  const updateRequestParam = (param: string, externalName: string): void => {
    patchDraft({ requestParamMap: { ...draft.requestParamMap, [param]: externalName } })
  }
  /** 入参适配的当前对齐值：人工登记优先，其次按名称；对不上留空待选（不硬凑）。 */
  const alignedExternal = (param: string): string => {
    const manual = draft.requestParamMap[param]
    if (manual) return manual
    const byName = context.requestParams.find((item) => item.name === param)
    return byName ? byName.name : ''
  }
  /** 找到对齐到某个外部入参的契约入参：请求预览的取值占位用它。 */
  const feederOf = (externalName: string): { code: string } | null => {
    const param = context.inputParams.find((item) => alignedExternal(item.name) === externalName)
    return param ? { code: param.code || param.name } : null
  }

  // 外部入参按部位分组：行跟随其对外对齐的参数落位，尚未对齐的契约入参进「待对齐」组。
  const locationOrder: Array<{ key: ExternalApiParamLocation; label: string }> = [
    { key: 'path', label: '路径参数' },
    { key: 'query', label: '查询参数' },
    { key: 'body', label: '请求体' }
  ]
  type InputGroup = {
    key: ExternalApiParamLocation | 'pending'
    label: string
    rows: FieldMappingContext['inputParams']
  }
  const inputGroups: InputGroup[] = locationOrder
    .map(({ key, label }) => ({
      key: key as ExternalApiParamLocation | 'pending',
      label,
      rows: context.inputParams.filter((param) => {
        const target = context.requestParams.find((item) => item.name === alignedExternal(param.name))
        return target?.location === key
      })
    }))
    .filter((group) => group.rows.length > 0)
  const pendingRows = context.inputParams.filter((param) => {
    const target = context.requestParams.find((item) => item.name === alignedExternal(param.name))
    return !target
  })
  if (pendingRows.length > 0) {
    inputGroups.push({ key: 'pending', label: '待对齐', rows: pendingRows })
  }

  /** 入参适配的变量候选：当前登录用户 + 契约入参（:code）。 */
  const inputVariables: ExpressionVariable[] = [
    { label: '当前登录用户', insert: ':currentUser' },
    ...context.inputParams.map((param) => ({
      label: param.name,
      insert: `:${param.code || param.name}`
    }))
  ]
  /** 出参适配的变量候选：当前登录用户（外部出参取回的原始值作为表达式输入隐含传入）。 */
  const outputVariables: ExpressionVariable[] = [{ label: '当前登录用户', insert: ':currentUser' }]

  /** 由当前草稿实时拼出外部请求预览：与适配生成器共用同一份确认数据。 */
  const buildRequestPreview = (): string => {
    const [method = 'GET', ...rest] = context.targetName.split(' ')
    let path = rest.join(' ')
    const query: string[] = []
    const body: string[] = []
    context.requestParams.forEach((param) => {
      const feeder = feederOf(param.name)
      const value = `{${feeder ? feeder.code : '?'}}`
      if (param.location === 'path') {
        path = path.split(`{${param.name}}`).join(value)
        return
      }
      if (param.location === 'query') {
        query.push(`${param.name}=${value}`)
        return
      }
      body.push(`  "${param.name}": ${value}`)
    })
    const lines = [`${method} ${path}${query.length ? `?${query.join('&')}` : ''}`]
    if (body.length > 0) {
      lines.push('', '{', ...body, '}')
    }
    return lines.join('\n')
  }

  const sourceText = [context.sourceName, context.targetName].filter(Boolean).join(' · ')

  return (
    <div className={cx('field-mapping-editor')}>
      <header className={cx('field-mapping-head')}>
        <code className={cx('field-mapping-method')}>{context.appMethod}</code>
        <strong>{context.appPath}</strong>
        {/* 保存 / 保存并确认跟随接口标题行右侧，与接口调试视图的发送按钮同位；只读态隐藏。 */}
        {!locked && (
          <div className={cx('field-mapping-head-actions')}>
            <Button onClick={() => onSave(draft)}>保存</Button>
            <Button loading={submitting} onClick={() => onConfirm(draft)} type="primary">
              保存并确认
            </Button>
          </div>
        )}
        <span className={cx('field-mapping-head-source')} title={sourceText}>
          {context.objectName} → {sourceText}
        </span>
      </header>
      {!isExternal && (context.op === '查询' || context.op === '删除') && draft.conditions.length > 0 && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>
            {context.op === '查询' ? '查询条件' : '定位条件'}
          </div>
          {draft.conditions.map((row, index) => (
            <div key={`${row.param}-${index}`} className={cx('field-mapping-row', 'row-condition', row.fixed && 'row-fixed')}>
              <span className={cx('field-mapping-param')} title={inputLabel(row.param)}>
                <em>{row.fixed ? '登录用户' : '入参'}</em>
                <code>{row.fixed ? row.param : inputLabel(row.param)}</code>
              </span>
              <Select
                aria-label={`${row.param} 条件操作符`}
                className={cx('field-mapping-operator')}
                disabled={locked}
                options={operatorOptions}
                value={row.operator}
                onChange={(operator) => updateCondition(index, { operator })}
              />
              <Select
                aria-label={`${row.param} 条件落列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder="选择落列"
                value={row.column || undefined}
                onChange={(column) => {
                  const columnInfo = context.columns.find((item) => item.name === column)
                  updateCondition(index, { column, columnComment: columnInfo?.comment || '' })
                }}
              />
            </div>
          ))}
        </section>
      )}
      {!isExternal && (context.op === '新增' || context.op === '修改') && draft.setters.length > 0 && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>写入字段</div>
          {draft.setters.map((row, index) => (
            <div key={`${row.param}-${index}`} className={cx('field-mapping-row', 'row-mapping')}>
              <span className={cx('field-mapping-param')} title={inputLabel(row.param)}>
                <em>入参</em>
                <code>{inputLabel(row.param)}</code>
                {row.required ? <em>必填</em> : null}
              </span>
              <span className={cx('field-mapping-arrow')}>→</span>
              <Select
                aria-label={`${row.param} 写入列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder="选择写入列"
                value={row.column || undefined}
                onChange={(column) => {
                  const columnInfo = context.columns.find((item) => item.name === column)
                  updateSetter(index, { column, columnComment: columnInfo?.comment || '' })
                }}
              />
            </div>
          ))}
        </section>
      )}
      {!isExternal && context.op === '查询' && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>返回字段</div>
          {draft.mappings.map((mapping) => (
            <div
              key={mapping.field}
              className={cx('field-mapping-row', 'row-mapping', !mapping.matched && 'row-pending')}
            >
              <span className={cx('field-mapping-param')} title={outputLabel(mapping.field)}>
                <em>出参</em>
                <code>{outputLabel(mapping.field)}</code>
              </span>
              <span className={cx('field-mapping-arrow')}>←</span>
              <Select
                aria-label={`选择 ${mapping.field} 的来源列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder={mapping.matched ? undefined : '选择来源列'}
                value={mapping.matched ? sourceNameOf(mapping.sourceLabel) : undefined}
                onChange={(source) => updateMapping(mapping.field, source)}
              />
            </div>
          ))}
        </section>
      )}
      {!isExternal && context.op && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>SQL 预览</div>
          <pre className={cx('field-mapping-preview')}>
            <code>{buildSqlPreview(draft, context.targetName)}</code>
          </pre>
        </section>
      )}
      {isExternal && (
        <>
          <section className={cx('field-mapping-card')}>
            <div className={cx('field-mapping-card-title')}>入参适配</div>
            {context.requestParams.length === 0 ? (
              <p className={cx('field-mapping-note')}>
                外部接口未提供入参定义，契约入参按名称透传。
              </p>
            ) : (
              inputGroups.map((group) => (
                <div className={cx('field-mapping-group')} key={group.key}>
                  <div className={cx('field-mapping-group-title')}>{group.label}</div>
                  {group.rows.map((param) => (
                    <div
                      key={`in-${param.name}`}
                      className={cx(
                        'field-mapping-row',
                        'row-external',
                        group.key === 'pending' && 'row-pending'
                      )}
                    >
                      <span className={cx('field-mapping-param')} title={inputLabel(param.name)}>
                        <em>入参</em>
                        <code>{contractFieldLabel(param.code, param.name)}</code>
                        {param.required ? <em>必填</em> : null}
                      </span>
                      <span className={cx('field-mapping-arrow')}>→</span>
                      <Select
                        aria-label={`${param.name} 对齐的外部入参`}
                        className={cx('field-mapping-field')}
                        disabled={locked}
                        options={externalParamOptions}
                        placeholder="选择外部入参"
                        value={alignedExternal(param.name) || undefined}
                        onChange={(value) => updateRequestParam(param.name, value)}
                      />
                      <ExpressionFx
                        disabled={locked}
                        paramLabel={inputLabel(param.name)}
                        value={draft.expressions[`in:${param.name}`] || ''}
                        variables={inputVariables}
                        onChange={(expression) => updateExpression(`in:${param.name}`, expression)}
                      />
                    </div>
                  ))}
                </div>
              ))
            )}
          </section>
          <section className={cx('field-mapping-card')}>
            <div className={cx('field-mapping-card-title')}>出参适配</div>
            {draft.mappings.map((mapping) => (
              <div
                key={`out-${mapping.field}`}
                className={cx('field-mapping-row', 'row-external', !mapping.matched && 'row-pending')}
              >
                <span className={cx('field-mapping-param')} title={outputLabel(mapping.field)}>
                  <em>出参</em>
                  <code>{outputLabel(mapping.field)}</code>
                </span>
                <span className={cx('field-mapping-arrow')}>←</span>
                <Select
                  aria-label={`选择 ${mapping.field} 的外部出参`}
                  className={cx('field-mapping-field')}
                  disabled={locked}
                  options={columnOptions}
                  placeholder="选择外部出参"
                  value={mapping.matched ? sourceNameOf(mapping.sourceLabel) : undefined}
                  onChange={(source) => updateMapping(mapping.field, source)}
                />
                <ExpressionFx
                  disabled={locked}
                  paramLabel={outputLabel(mapping.field)}
                  value={draft.expressions[`out:${mapping.field}`] || ''}
                  variables={outputVariables}
                  onChange={(expression) => updateExpression(`out:${mapping.field}`, expression)}
                />
              </div>
            ))}
          </section>
          <section className={cx('field-mapping-card')}>
            <div className={cx('field-mapping-card-title')}>请求预览</div>
            <pre className={cx('field-mapping-preview')}>
              <code>{buildRequestPreview()}</code>
            </pre>
          </section>
        </>
      )}
    </div>
  )
}
