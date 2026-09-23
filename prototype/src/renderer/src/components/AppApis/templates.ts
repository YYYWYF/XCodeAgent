import { flattenTargets, type BindableTarget, type DataSource } from '../DataSources/catalog'
import type { AppApi, ContractParam, TableOpKind, TemplateConditionRow, TemplateSetterRow } from './model'

/**
 * 契约与模板推导：契约入参全集、按注释相似度的列匹配评分，以及数据库绑定套用的
 * 增删查改固定模板。从 model.ts 拆出的纯推导模块——只依赖类型与数据来源目录，
 * 不含任何状态生命周期；由 model.ts 统一再导出，引用方无需感知拆分。
 */

/** 返回字段与表列的相似度评分：注释与字段名同义自动确认，前后缀关系视为疑似。 */
export function scoreFieldToColumn(field: string, column: { name: string; comment: string }): number {
  if (!column.comment) return 0
  if (column.comment === field) return 100
  if (column.comment.includes(field) || field.includes(column.comment)) return 70
  if (field.length >= 2 && column.comment.startsWith(field)) return 60
  return 0
}

/** 提取契约路径中的占位参数：`/api/rechecks/{id}/reviewer` → ['id']。 */
export function contractPathParams(path: string): string[] {
  const params: string[] = []
  const pattern = /\{([^}]+)\}/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(path))) params.push(match[1].trim())
  return params.filter(Boolean)
}

/** 契约入参全集：需求文档登记的入参 + 路径占位参数（未登记时按路径合成）。 */
export function contractRequestParams(object: AppApi): ContractParam[] {
  const registered = new Set(object.request.map((param) => param.name))
  const synthesized = contractPathParams(object.path)
    .filter((name) => !registered.has(name))
    .map((name) => ({ code: name, name, required: true, summary: `路径参数 {${name}}` }))
  return [...object.request, ...synthesized]
}

/**
 * 按接口功能自动判定数据库绑定套用的增删查改模板：
 * HTTP 方法是强信号，名称/用途中的动词做补充；查询类措辞优先于“提交”等背景词。
 */
export function deriveTableOp(object: AppApi): TableOpKind {
  const text = `${object.name}${object.description}`
  if (object.method === 'DELETE' || /删除|移除/.test(text)) return '删除'
  if (object.method === 'PUT' || object.method === 'PATCH' || /修改|更新|变更/.test(text)) {
    return '修改'
  }
  if (object.method === 'POST' && !/查询|查看|列表|搜索/.test(text)) return '新增'
  if (/新增|创建|提交|登记/.test(text) && !/查询|查看|列表/.test(text)) return '新增'
  return '查询'
}

/** 按业务术语挑列：复用注释相似度评分，返回得分最高的列。 */
function columnForTerm(
  term: string,
  columns: Array<{ name: string; comment: string }>
): { column: string; columnComment: string } | undefined {
  const ranked = columns
    .map((column) => ({ column, value: scoreFieldToColumn(term, column) }))
    .sort((left, right) => right.value - left.value)
  const best = ranked[0]
  return best && best.value > 0
    ? { column: best.column.name, columnComment: best.column.comment }
    : undefined
}

/** 「我的/本人/自己」类契约语义 → 登录态固定条件的触发词。 */
const LOGIN_SCOPE_PATTERN = /我的|本人|自己/

/** 表绑定模板推导结果：操作类型 + 槽位行 + 自动排序。 */
export type TableTemplate = {
  op: TableOpKind
  conditions: TemplateConditionRow[]
  setters: TemplateSetterRow[]
  orderBy: string
}

/**
 * 数据库绑定的固定增删查改模板：把契约出入参按语义填进模板槽位——
 * 条件/写入行由列注释匹配自动生成，「我的」类接口追加登录态固定条件，
 * 查询模板再按出参中的时间列建议倒序排序。槽位填好后人工只需逐项确认。
 */
export function buildTableTemplate(object: AppApi, sources: DataSource[]): TableTemplate {
  const op = deriveTableOp(object)
  const empty: TableTemplate = { op, conditions: [], setters: [], orderBy: '' }
  const binding = object.implementation.bindings[0]
  if (!binding || binding.sourceId === 'local') return empty
  const target: BindableTarget | undefined = flattenTargets(sources).find(
    (item) => item.sourceId === binding.sourceId && item.targetName === binding.targetName
  )
  if (!target) return empty
  if (op === '查询' || op === '删除') {
    const conditions: TemplateConditionRow[] = []
    // 「我的回检」这类接口：数据范围由登录态圈定，模板追加一条当前用户固定条件。
    if (op === '查询' && LOGIN_SCOPE_PATTERN.test(`${object.name}${object.description}`)) {
      const scope = columnForTerm('申请人', target.fields) || columnForTerm('创建', target.fields)
      if (scope) {
        conditions.push({ param: '当前登录用户', fixed: true, ...scope, operator: '等于' })
      }
    }
    contractRequestParams(object).forEach((param) => {
      // 未匹配到列的契约入参也进模板（落列留空待人工选择），保证出入参映射完整可配。
      const matched = columnForTerm(param.name, target.fields)
      conditions.push({
        param: param.name,
        fixed: false,
        column: matched?.column || '',
        columnComment: matched?.columnComment || '',
        operator: '等于'
      })
    })
    return {
      op,
      conditions,
      setters: [],
      orderBy: op === '查询' ? suggestOrderBy(object, target.fields) : ''
    }
  }
  // 新增/修改模板：契约入参逐个落到写入列，未匹配到列的留空待人工选择。
  const setters = object.request.map((param) => {
    const matched = columnForTerm(param.name, target.fields)
    return {
      param: param.name,
      column: matched?.column || '',
      columnComment: matched?.columnComment || '',
      required: param.required
    }
  })
  return { op, conditions: [], setters, orderBy: '' }
}

/** 查询模板的自动排序：契约出参对应列中的时间列倒序，让最新记录排在最前。 */
function suggestOrderBy(
  object: AppApi,
  columns: Array<{ name: string; comment: string }>
): string {
  const timeColumn = object.response
    .map((output) => columnForTerm(output.name, columns))
    .find((item) => item && /时间/.test(item.columnComment))
  return timeColumn ? `${timeColumn.column} DESC` : ''
}
