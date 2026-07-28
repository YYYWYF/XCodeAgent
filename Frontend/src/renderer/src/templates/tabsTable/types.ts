// ==================== 管理事项 ====================
export interface ManagementItem {
  id: string
  /** 事项编号 */
  itemNo: string
  /** 事项名称 */
  itemName: string
  /** 所属部门 */
  department: string
  /** 负责人 */
  owner: string
  /** 状态 */
  status: 'pending' | 'in_progress' | 'completed'
}

// ==================== 机构参数 ====================
export interface InstitutionParam {
  id: string
  /** 参数编码 */
  paramCode: string
  /** 参数名称 */
  paramName: string
  /** 参数值 */
  paramValue: string
  /** 生效日期 */
  effectiveDate: string
  /** 备注 */
  remark: string
}

// ==================== 用户管理 ====================
export interface UserItem {
  id: string
  /** 用户名 */
  username: string
  /** 姓名 */
  realName: string
  /** 角色 */
  role: string
  /** 邮箱 */
  email: string
  /** 创建时间 */
  createdAt: string
}

// ==================== 通用分页 ====================
export interface PaginatedResult<T> {
  data: T[]
  success: boolean
  total: number
}

export interface MutationResult {
  success: boolean
}
