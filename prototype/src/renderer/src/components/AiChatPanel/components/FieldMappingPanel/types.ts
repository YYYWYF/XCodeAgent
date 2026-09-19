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

/** 目录里的一个应用API条目：只展示名称，方法与来源在右侧内容区呈现。 */
export type FieldMappingApiItem = {
  id: string
  name: string
}
