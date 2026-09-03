/** 描述预览元素对应的源码定位，供审查标签和二次修改请求共享。 */
export type InspectedElementContext = {
  tagName: string
  sourcePath: string
  line: number
  column: number
}
