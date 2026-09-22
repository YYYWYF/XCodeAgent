import { flattenTargets, type DataSource, type ExternalApiParamLocation } from '../DataSources/catalog'
import { contractRequestParams } from './templates'
import type { AppApi } from './model'

/**
 * 外部服务的参数适配推导：外部入参一行一条的适配行视图、表达式存储键，以及
 * 语义不一致处的函数表达式推荐。从 model.ts 拆出的纯推导模块——只依赖类型、
 * 模板推导与数据来源目录；由 model.ts 统一再导出，引用方无需感知拆分。
 */

/** 一条参数适配行：外部侧一行一条，描述它的取值来源；未连接行 matched=false。 */
export type AdaptationRow = {
  direction: '入参适配' | '出参适配'
  /** 契约侧供值入参名；未连接行为空。 */
  param: string
  /** 契约侧业务含义（仅入参适配且来自契约入参时提供）。 */
  paramSummary: string
  required: boolean
  /** 外部侧名称；出参适配未匹配时为空字符串。 */
  external: string
  externalComment: string
  /** 入参适配的外部请求部位：路径参数/查询参数/请求体；出参适配为空。 */
  location: ExternalApiParamLocation | ''
  /** 该行是否已连接取值来源；入参适配随连接登记，出参适配随字段映射状态。 */
  matched: boolean
}

/** 适配行的表达式存储键：入参/出参分开前缀，避免同名互相覆盖。 */
export function adaptationExpressionKey(row: AdaptationRow): string {
  return `${row.direction === '入参适配' ? 'in' : 'out'}:${row.param}`
}

/** 从字段映射标签还原来源字段名：兼容「来源 · name（说明）」与对话卡草稿的简写「name（说明）」。 */
export function mappingSourceName(sourceLabel: string): string {
  const prefixed = sourceLabel.match(/· (.+?)（/)
  if (prefixed) return prefixed[1]
  const short = sourceLabel.match(/^(.+?)（/)
  return short ? short[1] : ''
}

/**
 * 外部服务绑定的参数适配视图：外部入参一行一条，取值来源以 requestFeeders 的
 * 显式连接登记为准（契约入参名），没有登记即未连接——不再按名称隐式推导，也不做
 * 固定值补位：应用侧没有对应入参就连线都不显示；出参适配直接读取字段映射的确认
 * 结果，保证与「确认绑定」状态和生成的适配代码一致。
 */
export function externalAdaptations(object: AppApi, sources: DataSource[]): AdaptationRow[] {
  if (object.implementation.kind !== '外部服务') return []
  const target = flattenTargets(sources).find(
    (item) =>
      item.sourceKind === 'external_service' &&
      object.implementation.bindings.some(
        (binding) => binding.sourceId === item.sourceId && binding.targetName === item.targetName
      )
  )
  if (!target) return []
  const feeders = object.implementation.requestFeeders || {}
  const contractParams = contractRequestParams(object)
  const requestRows: AdaptationRow[] = target.requestParams.map((param) => {
    const feederName = feeders[param.name] || ''
    const contractParam = contractParams.find((item) => item.name === feederName)
    return {
      direction: '入参适配',
      param: contractParam?.name || '',
      paramSummary: contractParam?.summary || '',
      required: Boolean(param.required),
      external: param.name,
      externalComment: param.comment,
      location: param.location,
      matched: Boolean(contractParam)
    }
  })
  const responseRows: AdaptationRow[] = object.response.map((output) => {
    const mapping = object.implementation.mappings.find((item) => item.field === output.name)
    const externalField =
      mapping?.matched
        ? target.fields.find((item) => item.name === mappingSourceName(mapping.sourceLabel))
        : undefined
    return {
      direction: '出参适配',
      param: output.name,
      paramSummary: '',
      required: false,
      external: externalField?.name || '',
      externalComment: externalField?.comment || '',
      location: '',
      matched: Boolean(externalField)
    }
  })
  return [...requestRows, ...responseRows]
}

/** 判断两段业务含义是否指同一件事：去掉“路径参数”等套话后看有无二字片段重合。 */
function termsOverlap(left: string, right: string): boolean {
  const clean = (text: string): string =>
    text.replace(/路径参数|请求参数|入参|出参|参数|必填|可选|[，,。·()（）\s]/g, '')
  const source = clean(left)
  const target = clean(right)
  if (!source || !target) return true
  for (let index = 0; index + 2 <= source.length; index += 1) {
    if (target.includes(source.slice(index, index + 2))) return true
  }
  return false
}

/**
 * 语义不一致的适配由 AI 推荐一条函数表达式做简单加工。演示启发两类：
 * 入参「回检单号 → 员工工号」这类跨域取数——按回检单号在审核轨迹表定位审核人工号；
 * 出参「申请人」拿到的是工号——按工号回查员工表译成姓名。已有人工表达式时不覆盖。
 */
export function recommendAdaptationExpressions(
  object: AppApi,
  sources: DataSource[]
): Record<string, string> {
  const seeded: Record<string, string> = { ...object.implementation.expressions }
  externalAdaptations(object, sources).forEach((row) => {
    const key = adaptationExpressionKey(row)
    if (seeded[key]) return
    if (row.direction === '入参适配') {
      // 未连接行没有契约入参语义，不做跨域取数推荐。
      if (!row.param) return
      if (termsOverlap(row.paramSummary, row.externalComment)) return
      if (/回检/.test(row.paramSummary) && /工号|员工/.test(row.externalComment)) {
        seeded[key] = 'LOOKUP(recheck_audit, recheck_id, reviewer_id)'
      }
      return
    }
    // 出参适配：来源是工号而契约要姓名/申请人时，回查员工表翻译。
    if (!row.matched) return
    if (/申请人|姓名/.test(row.param) && /工号/.test(row.externalComment)) {
      seeded[key] = 'LOOKUP(user, id, name)'
    }
  })
  return seeded
}
