import { ApiOutlined } from '@ant-design/icons'
import { Button, Select } from 'antd'
import type { ReactElement } from 'react'
import type { BindingDraft } from '../../../AppApis/model'
import {
  CONDITION_OPERATORS,
  conditionSqlFragment,
  contractFieldLabel,
  missingRequiredFeeders
} from '../../../AppApis/model'
import ExternalMappingCards from './ExternalMapping'
import { cx } from '../../../../utils'
import type { FieldMappingApiItem, FieldMappingContext } from './types'
import './index.less'

export type { FieldMappingApiItem, FieldMappingContext, MappingFieldOption } from './types'

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

/**
 * 字段映射面板：应用API映射绑定的编辑工作台。左侧以应用的全部API为目录
 * （状态点标注进行中/已确认），右侧是当前绑定对象的映射编辑区——数据库形态为
 * 模板槽位 + SQL 预览卡片；外部API形态为「双列连线映射」：应用与外部的出入参
 * 两列平铺，中段连线表达取值关系，外部必填入参未连接时警示并拦截确认。
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
            // 画布与控件只在提交瞬间锁定；已确认绑定为「可调整」态（保存更新配置）。
            locked={submitting}
            readOnly={readOnly}
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

/** 编辑区入参：当前绑定对象的契约上下文 + 共享草稿；locked（提交中）时控件禁用。 */
type EditorProps = {
  context: FieldMappingContext
  draft: BindingDraft
  locked: boolean
  /** 已确认绑定：可继续调整并保存，但不出现「保存并确认」（无待推进的工作流）。 */
  readOnly: boolean
  submitting: boolean
  onChange: (draft: BindingDraft) => void
  onSave: (draft: BindingDraft) => void
  onConfirm: (draft: BindingDraft) => void
}

/** 映射编辑区：契约头（动作按钮跟随标题行右侧）+ 分节卡片；数据库为模板槽位，外部为双列连线画布。 */
function FieldMappingEditor({ context, draft, locked, readOnly, submitting, onChange, onSave, onConfirm }: EditorProps): ReactElement {
  const isExternal = context.kind === '外部服务'
  const columnOptions = context.columns.map((column) => ({
    value: column.name,
    label: `${column.name}（${column.comment}）`
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

  // 外部必填入参未连接的名单：连接登记是唯一事实，命中即拦截「保存并确认」，
  // 避免外部调用缺参数的隐患随确认进入应用。
  const missingFeeders = isExternal ? missingRequiredFeeders(context.requestParams, draft) : []

  return (
    <div className={cx('field-mapping-editor')}>
      <header className={cx('field-mapping-head')}>
        <div className={cx('field-mapping-head-row')}>
          <code className={cx('field-mapping-method')}>{context.appMethod}</code>
          <strong>{context.appPath}</strong>
          <span className={cx('field-mapping-head-name')}>{context.objectName}</span>
          {/* 保存跟随主标题行右侧（已确认绑定为「保存更新」），与接口调试视图的发送按钮同位；
              「保存并确认」只在有待推进工作流时出现，且被外部必填门禁拦截。 */}
          {!locked && (
            <div className={cx('field-mapping-head-actions')}>
              <Button onClick={() => onSave(draft)}>{readOnly ? '保存更新' : '保存'}</Button>
              {!readOnly && (
                <Button
                  disabled={missingFeeders.length > 0}
                  loading={submitting}
                  onClick={() => onConfirm(draft)}
                  title={
                    missingFeeders.length > 0
                      ? `「${missingFeeders.join('、')}」为外部接口必填入参，尚未连接取值来源`
                      : undefined
                  }
                  type="primary"
                >
                  保存并确认
                </Button>
              )}
            </div>
          )}
        </div>
        {/* 数据来源行：主标题始终是应用API，外部API/数据表作为来源标记放在次行。 */}
        <p className={cx('field-mapping-head-source')}>
          <span className={cx('field-mapping-head-source-label')}>数据来源</span>
          <em className={cx('field-mapping-head-role', 'ext')}>{isExternal ? '外部API' : '数据表'}</em>
          <span className={cx('field-mapping-head-source-text')}>
            {context.sourceName}
            {context.targetName ? ` · ${context.targetName}` : ''}
          </span>
        </p>
      </header>
      {isExternal ? (
        // 外部服务：双列连线映射画布（入参适配 / 出参适配 / 请求预览）。
        <ExternalMappingCards confirmed={readOnly} context={context} draft={draft} locked={locked} patch={patchDraft} />
      ) : (
        <>
          {(context.op === '查询' || context.op === '删除') && draft.conditions.length > 0 && (
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
          {(context.op === '新增' || context.op === '修改') && draft.setters.length > 0 && (
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
          {context.op === '查询' && (
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
          {context.op && (
            <section className={cx('field-mapping-card')}>
              <div className={cx('field-mapping-card-title')}>SQL 预览</div>
              <pre className={cx('field-mapping-preview')}>
                <code>{buildSqlPreview(draft, context.targetName)}</code>
              </pre>
            </section>
          )}
        </>
      )}
    </div>
  )
}
