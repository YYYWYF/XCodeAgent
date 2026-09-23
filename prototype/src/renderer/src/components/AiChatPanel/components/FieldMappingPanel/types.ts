import type { ReactNode } from 'react'
import type { ExternalApiParamLocation } from '../../../DataSources/catalog'

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

/** 目录里的一个应用API条目：名称 + 绑定状态点（已确认/绑定中/未开始），方法与来源在右侧内容区呈现。 */
export type FieldMappingApiItem = {
  id: string
  name: string
  /** 绑定进度：confirmed 已确认；binding 已选定来源待确认；pending 尚未开始。缺省按 pending 处理。 */
  state?: 'confirmed' | 'binding' | 'pending'
}

/**
 * 直连绑定的三级来源树节点：类型（数据表/外部接口）→ 连接/域 → 表/接口。
 * 叶子 value 为绑定目标键（bindingKey），父节点只承担分组、不可选中；
 * title 为富样式节点，搜索过滤按 filterTitle 匹配。
 */
export type MappingSourceTreeNode = {
  title: ReactNode
  /** 搜索过滤文案（纯文本）：title 为富样式节点时过滤按本字段匹配。 */
  filterTitle: string
  value: string
  selectable?: boolean
  children?: MappingSourceTreeNode[]
}
