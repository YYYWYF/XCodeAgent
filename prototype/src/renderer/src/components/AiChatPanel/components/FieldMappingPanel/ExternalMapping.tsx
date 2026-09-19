import { useState } from 'react'
import type { ReactElement } from 'react'
import { contractFieldLabel, missingRequiredFeeders, type BindingDraft } from '../../../AppApis/model'
import {
  EXTERNAL_PARAM_LOCATION_LABEL,
  type ExternalApiParamLocation
} from '../../../DataSources/catalog'
import ExpressionEditorModal, { type ExpressionVariable } from './ExpressionEditorModal'
import WireCanvas, { type WireNode, type WireRegion, type WireTone } from './WireCanvas'
import type { FieldMappingContext, MappingFieldOption } from './types'
import { cx } from '../../../../utils'

/** 外部入参的部位展示次序：路径参数 → 查询参数 → 请求体；未声明部位的排到最后。 */
const LOCATION_ORDER: Array<{ key: ExternalApiParamLocation; label: string }> = [
  { key: 'path', label: '路径参数' },
  { key: 'query', label: '查询参数' },
  { key: 'body', label: '请求体' }
]

/** 从字段映射标签还原外部出参名：兼容「来源 · name（说明）」与草稿简写「name（说明）」。 */
function sourceFieldOf(label: string): string {
  const prefixed = label.match(/· (.+?)（/)
  if (prefixed) return prefixed[1]
  const short = label.match(/^(.+?)（/)
  return short ? short[1] : ''
}

/** 表达式摘要文案：空串读作透传，登录用户表达式缩写展示，其余原样截断交给样式省略。 */
function expressionSummary(value: string): string {
  if (value === '') return '透传'
  if (value === ':currentUser') return '登录用户'
  return value
}

/**
 * 参数名文本：必填参数在名称前带红星，紧跟文字同一行内；可选项不加任何标识。
 */
function ParamText({
  code,
  comment,
  required
}: {
  code: string
  comment: string
  required?: boolean
}): ReactElement {
  return (
    <span className={cx('field-mapping-node-text')}>
      {required ? <em className={cx('req-star')}>*</em> : null}
      {contractFieldLabel(code, comment)}
    </span>
  )
}

/**
 * 节点内的 fx 取值钮：连线建立后可对取值做函数加工——入参加工应用侧取值（钮挂在
 * 应用节点），出参加工外部侧原始值（钮挂在外部节点）；未配置时灰显「透传」，配置过
 * 则高亮显示表达式摘要。点击冒泡会被截断，不触发节点自身的连接逻辑。
 */
function NodeFxChip({
  disabled,
  title,
  value,
  onClick
}: {
  disabled: boolean
  title: string
  value: string
  onClick: () => void
}): ReactElement {
  return (
    <button
      aria-label="配置取值表达式"
      className={cx('field-mapping-node-fx', value !== '' && 'active')}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation()
        onClick()
      }}
      title={title ? `${title} · ${value || '直接透传'}` : value || '直接透传'}
      type="button"
    >
      <em>fx</em>
      <span>{expressionSummary(value)}</span>
    </button>
  )
}

/** 一次表达式弹框会话：标题、初值、变量候选与确认写回动作。 */
type EditorSession = {
  title: string
  value: string
  variables: ExpressionVariable[]
  /** 确认回调：把表达式写回草稿。 */
  commit: (value: string) => void
}

type Props = {
  context: FieldMappingContext
  draft: BindingDraft
  /** 提交中：画布与 fx 短暂锁定。 */
  locked: boolean
  /** 已确认绑定：画布仍可调整，动作区只保留「保存更新」。 */
  confirmed?: boolean
  /** 局部更新草稿：由编辑区共享的 patchDraft 透传。 */
  patch: (patch: Partial<BindingDraft>) => void
}

/**
 * 外部服务绑定的双列连线映射：一张画布纵分应用API（左）与外部API（右）两列，每列内
 * 按「入参 / 出参」分组纵向排列，中段连线表达取值关系——点击一侧参数再点击对侧参数
 * 完成连接，点击连线断开；两侧没有对应关系的参数就保持无连线，不设固定值补位。
 * 外部必填入参未连接时红显、警示、「保存并确认」被门禁拦截。
 */
export default function ExternalMappingCards({ context, draft, locked, confirmed = false, patch }: Props): ReactElement {
  const feeders = draft.requestFeeders || {}
  const missing = missingRequiredFeeders(context.requestParams, draft)
  // 待连接状态分分区持有：入参分区的源在左侧（应用入参），出参分区的源在右侧（外部出参）。
  const [pending, setPending] = useState<{ partition: 'in' | 'out'; id: string } | null>(null)
  const [session, setSession] = useState<EditorSession | null>(null)

  /** 外部入参按部位次序排布，右列与请求预览保持同一顺序。 */
  const sortedParams = [...context.requestParams].sort(
    (left, right) =>
      LOCATION_ORDER.findIndex((item) => item.key === left.location) -
      LOCATION_ORDER.findIndex((item) => item.key === right.location)
  )

  /** 节点统一编为 `分区:侧别:参数名`（应用出入参可能同名，前缀防止冲突），取末段即参数名。 */
  const partitionOfId = (id: string): 'in' | 'out' => (id.startsWith('in:') ? 'in' : 'out')
  const nameOfId = (id: string): string => {
    const parts = id.split(':')
    return parts[parts.length - 1]
  }

  /** 某契约入参是否已被某个外部入参连接为取值来源。 */
  const isFeeding = (contractName: string): boolean =>
    Object.values(feeders).some((feederName) => feederName === contractName)

  /** 待连接状态切换：重复点击同一节点视为取消。 */
  const togglePending = (partition: 'in' | 'out', id: string): void =>
    setPending((current) =>
      current && current.partition === partition && current.id === id ? null : { partition, id }
    )

  /** 建立契约入参 → 外部入参连接：外部入参只允许一个取值来源，重连时清理旧来源的表达式。 */
  const connectContract = (extName: string, contractName: string): void => {
    const next = { ...feeders }
    const previous = next[extName]
    const expressions = { ...draft.expressions }
    if (previous && previous !== contractName) {
      // 一个契约入参可以同时喂多个外部入参：仅当没有其它连线仍引用旧来源时才清理其表达式。
      const stillFeeding = Object.entries(next).some(
        ([name, feederName]) => name !== extName && feederName === previous
      )
      if (!stillFeeding) delete expressions[`in:${previous}`]
    }
    next[extName] = contractName
    patch({ requestFeeders: next, expressions })
  }

  /** 节点点击：按分区与侧别分派——源侧切换待连接，目标侧在有待连接源时完成连接。 */
  const handleNodeClick = (side: 'left' | 'right', id: string): void => {
    if (locked) return
    const partition = partitionOfId(id)
    const active = pending && pending.partition === partition ? pending.id : null
    if (partition === 'in') {
      if (side === 'left') {
        togglePending('in', id)
        return
      }
      if (!active) return
      connectContract(nameOfId(id), nameOfId(active))
      setPending(null)
      return
    }
    if (side === 'right') {
      togglePending('out', id)
      return
    }
    if (!active) return
    const column = context.columns.find((item) => item.name === nameOfId(active))
    const field = nameOfId(id)
    if (column) {
      patch({
        mappings: draft.mappings.map((row) =>
          row.field === field
            ? { ...row, matched: true, matchType: 'manual' as const, sourceLabel: `${column.name}（${column.comment}）` }
            : row
        )
      })
    }
    setPending(null)
  }

  /** 断开连接：入参清连接登记与对应表达式（表达式仍被其它连线引用时保留）；出参把字段映射打回未匹配。 */
  const handleWireClick = (wire: { id: string; to: string }): void => {
    if (locked) return
    const expressions = { ...draft.expressions }
    if (wire.id.startsWith('w-in:')) {
      const extName = nameOfId(wire.to)
      const feederName = feeders[extName] || ''
      const next = { ...feeders }
      delete next[extName]
      if (feederName) {
        const stillFeeding = Object.values(next).some((name) => name === feederName)
        if (!stillFeeding) delete expressions[`in:${feederName}`]
      }
      patch({ requestFeeders: next, expressions })
      return
    }
    const field = nameOfId(wire.to)
    delete expressions[`out:${field}`]
    patch({
      mappings: draft.mappings.map((row) =>
        row.field === field ? { ...row, matched: false, matchType: 'manual' as const, sourceLabel: '' } : row
      )
    })
  }

  /* —— 入参分区：应用入参（fx 加工应用侧取值）→ 外部入参 —— */
  const inputLeftRows: WireNode[] = context.inputParams.map((param) => ({
    id: `in:l:${param.name}`,
    tone: (pending?.partition === 'in' && pending.id === `in:l:${param.name}`
      ? 'pending'
      : 'normal') as WireNode['tone'],
    content: (
      <>
        <ParamText code={param.code} comment={param.name} required={param.required} />
        {isFeeding(param.name) ? (
          <NodeFxChip
            disabled={locked}
            onClick={() =>
              setSession({
                title: `配置函数表达式 · ${contractFieldLabel(param.code, param.name)}`,
                value: draft.expressions[`in:${param.name}`] || '',
                variables: [
                  { label: '当前登录用户', insert: ':currentUser' },
                  ...context.inputParams.map((item) => ({
                    label: item.name,
                    insert: `:${item.code || item.name}`
                  }))
                ],
                commit: (value) =>
                  patch({
                    expressions: { ...draft.expressions, [`in:${param.name}`]: value }
                  })
              })
            }
            title={contractFieldLabel(param.code, param.name)}
            value={draft.expressions[`in:${param.name}`] || ''}
          />
        ) : null}
      </>
    )
  }))
  const inputRightRows: WireNode[] = sortedParams.map((param) => {
    const feederName = feeders[param.name] || ''
    return {
      id: `in:r:${param.name}`,
      tone: (!feederName && param.required
        ? 'danger'
        : pending?.partition === 'in'
          ? 'ready'
          : 'normal') as WireNode['tone'],
      content: (
        <>
          <ParamText code={param.name} comment={param.comment} required={param.required} />
          {param.location ? (
            <em className={cx('field-mapping-marker', 'loc')}>
              {EXTERNAL_PARAM_LOCATION_LABEL[param.location]}
            </em>
          ) : null}
        </>
      )
    }
  })
  const inputWires = Object.entries(feeders)
    .filter(([, feederName]) => Boolean(feederName))
    .map(([extName, feederName]) => ({
      id: `w-in:${extName}`,
      from: `in:l:${feederName}`,
      to: `in:r:${extName}`,
      tone: 'manual' as WireTone
    }))

  /* —— 出参分区：应用出参 ← 外部出参（fx 加工外部侧原始值，钮挂外部节点）—— */
  const outputLeftRows: WireNode[] = context.outputs.map((output) => ({
    id: `out:l:${output.name}`,
    tone: (pending?.partition === 'out' ? 'ready' : 'normal') as WireNode['tone'],
    content: <ParamText code={output.code} comment={output.name} />
  }))
  const outputRightRows: WireNode[] = context.columns.map((column) => {
    // 出参加工的是外部返回的原始值：fx 钮挂在外部出参节点上，编辑其对应应用出参的表达式。
    const fedOutput = draft.mappings.find(
      (row) => row.matched && sourceFieldOf(row.sourceLabel) === column.name
    )
    return {
      id: `out:r:${column.name}`,
      tone: (pending?.partition === 'out' && pending.id === `out:r:${column.name}`
        ? 'pending'
        : 'normal') as WireNode['tone'],
      content: (
        <>
          <ParamText code={column.name} comment={column.comment} />
          {fedOutput ? (
            <NodeFxChip
              disabled={locked}
              onClick={() =>
                setSession({
                  title: `配置函数表达式 · ${column.name}（${column.comment}）`,
                  value: draft.expressions[`out:${fedOutput.field}`] || '',
                  variables: [{ label: '当前登录用户', insert: ':currentUser' }],
                  commit: (value) =>
                    patch({
                      expressions: { ...draft.expressions, [`out:${fedOutput.field}`]: value }
                    })
                })
              }
              title={column.name}
              value={draft.expressions[`out:${fedOutput.field}`] || ''}
            />
          ) : null}
        </>
      )
    }
  })
  const outputWires = draft.mappings
    .filter((row) => row.matched)
    .map((row) => {
      const sourceName = sourceFieldOf(row.sourceLabel)
      if (!sourceName || !context.columns.some((column) => column.name === sourceName)) return null
      return {
        id: `w-out:${row.field}`,
        from: `out:r:${sourceName}`,
        to: `out:l:${row.field}`,
        tone: (row.matchType === 'auto' ? 'auto' : 'manual') as WireTone
      }
    })
    .filter((wire): wire is NonNullable<typeof wire> => Boolean(wire))

  /** 单一画布：按「行标题（入参/出参）× 列标题（应用API/外部API）」两区对齐排布。 */
  const regions: WireRegion[] = [
    { id: 'in', rowTitle: '入参', leftRows: inputLeftRows, rightRows: inputRightRows },
    { id: 'out', rowTitle: '出参', leftRows: outputLeftRows, rightRows: outputRightRows }
  ]

  /** 由当前连接草稿拼出外部请求预览：契约供值显示 {code}，未连接显示 {?}。 */
  const buildRequestPreview = (): string => {
    const [method = 'GET', ...rest] = context.targetName.split(' ')
    let path = rest.join(' ')
    const query: string[] = []
    const body: string[] = []
    const valueOf = (param: MappingFieldOption): string => {
      const feederName = feeders[param.name] || ''
      if (!feederName) return '{?}'
      const contract = context.inputParams.find((item) => item.name === feederName)
      return `{${contract?.code || feederName}}`
    }
    sortedParams.forEach((param) => {
      const value = valueOf(param)
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

  return (
    <>
      <div className={cx('field-mapping-guide-row')}>
        <p className={cx('field-mapping-guide')}>
          <em>{confirmed ? '已确认绑定' : '连线操作'}</em>
          {confirmed
            ? '可直接调整连线与 fx；调整后点「保存更新」更新配置，虚线为 AI 建议结果。'
            : '点击一侧参数，再点击对侧参数完成连接；点击连线可断开；虚线为 AI 建议结果。'}
        </p>
        {context.requestParams.length > 0 ? (
          missing.length > 0 ? (
            <span className={cx('field-mapping-status', 'danger')}>必填未连接 {missing.length}</span>
          ) : (
            <span className={cx('field-mapping-status', confirmed ? 'muted' : 'ok')}>
              {confirmed ? '已确认' : '必填已就绪'}
            </span>
          )
        ) : null}
      </div>
      {missing.length > 0 ? (
        <p className={cx('field-mapping-warning')}>
          「{missing.join('、')}」为外部接口必填入参，尚未连接取值来源
          {confirmed ? '。' : '；确认前需要完成连接。'}
        </p>
      ) : null}
      {context.requestParams.length === 0 ? (
        <p className={cx('field-mapping-note')}>外部接口未提供入参定义，契约入参按名称透传。</p>
      ) : null}
      <WireCanvas
        columnTitles={['应用API', '外部API']}
        disabled={locked}
        regions={regions}
        wires={[...inputWires, ...outputWires]}
        pending={pending ? { side: pending.partition === 'in' ? 'left' : 'right', id: pending.id } : null}
        onCanvasClick={() => setPending(null)}
        onNodeClick={handleNodeClick}
        onWireClick={handleWireClick}
      />
      <section className={cx('field-mapping-card')}>
        <div className={cx('field-mapping-card-title')}>请求预览</div>
        <pre className={cx('field-mapping-preview')}>
          <code>{buildRequestPreview()}</code>
        </pre>
      </section>
      <ExpressionEditorModal
        onCancel={() => setSession(null)}
        onOk={(value) => {
          session?.commit(value)
          setSession(null)
        }}
        open={Boolean(session)}
        paramLabel=""
        title={session?.title || ''}
        value={session?.value || ''}
        variables={session?.variables || []}
      />
    </>
  )
}
