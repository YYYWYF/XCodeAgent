import type { ManagementItem, InstitutionParam, UserItem, PaginatedResult, MutationResult } from './types'

// ==================== Mock 数据 ====================
const mockManagementList: ManagementItem[] = Array.from({ length: 32 }, (_, i) => {
  const id = String(i + 1).padStart(4, '0')
  return {
    id,
    itemNo: `ITEM-2026-${id}`,
    itemName: ['员工考勤管理', '报销审批流程', '资产采购申请', '合同签署管理', '会议预约'][i % 5],
    department: ['技术部', '财务部', '行政部', '市场部'][i % 4],
    owner: ['张三', '李四', '王五', '赵六'][i % 4],
    status: ['pending', 'in_progress', 'completed'][i % 3] as ManagementItem['status'],
  }
})

const mockInstitutionList: InstitutionParam[] = Array.from({ length: 28 }, (_, i) => {
  const id = String(i + 1).padStart(4, '0')
  return {
    id,
    paramCode: `PARAM_${1000 + i}`,
    paramName: ['最大并发数', '超时阈值(ms)', '重试次数', '缓存过期时间(s)', '单页条数上限'][i % 5],
    paramValue: String(Math.floor(Math.random() * 500 + 10)),
    effectiveDate: `2026-${String((i % 6) + 1).padStart(2, '0')}-${String((i % 28) + 1).padStart(2, '0')}`,
    remark: `参数说明-${id}`,
  }
})

const mockUserList: UserItem[] = Array.from({ length: 40 }, (_, i) => {
  const id = String(i + 1).padStart(4, '0')
  return {
    id,
    username: `user_${id}`,
    realName: ['张三', '李四', '王五', '赵六', '陈七', '周八'][i % 6],
    role: ['管理员', '普通用户', '审计员'][i % 3],
    email: `user${id}@example.com`,
    createdAt: `2026-${String((i % 8) + 1).padStart(2, '0')}-${String((i % 28) + 1).padStart(2, '0')} 10:00:00`,
  }
})

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** 从 ProTable request params 中提取 page / pageSize */
const pageFrom = (params: Record<string, unknown>) => ({
  page: (params.current as number) || (params.page as number) || 1,
  pageSize: (params.pageSize as number) || 10,
})

// ==================== 管理事项 ====================
export const fetchManagementList = async (params: Record<string, unknown>): Promise<PaginatedResult<ManagementItem>> => {
  await delay(300)
  const { page, pageSize } = pageFrom(params)
  let filtered = [...mockManagementList]
  if (params.itemNo) { filtered = filtered.filter((item) => item.itemNo.includes(String(params.itemNo))) }
  if (params.itemName) { filtered = filtered.filter((item) => item.itemName.includes(String(params.itemName))) }
  if (params.department) { filtered = filtered.filter((item) => item.department.includes(String(params.department))) }
  if (params.owner) { filtered = filtered.filter((item) => item.owner.includes(String(params.owner))) }
  if (params.status) { filtered = filtered.filter((item) => item.status === params.status) }
  const total = filtered.length
  const data = filtered.slice((page - 1) * pageSize, page * pageSize)
  return { data, success: true, total }
}

export const updateManagement = async (payload: Partial<ManagementItem> & { id: string }): Promise<MutationResult> => {
  await delay(200)
  const idx = mockManagementList.findIndex((item) => item.id === payload.id)
  if (idx === -1) return { success: false }
  mockManagementList[idx] = { ...mockManagementList[idx], ...payload }
  return { success: true }
}

export const deleteManagement = async (id: string): Promise<MutationResult> => {
  await delay(200)
  const idx = mockManagementList.findIndex((item) => item.id === id)
  if (idx === -1) return { success: false }
  mockManagementList.splice(idx, 1)
  return { success: true }
}

// ==================== 机构参数 ====================
export const fetchInstitutionList = async (params: Record<string, unknown>): Promise<PaginatedResult<InstitutionParam>> => {
  await delay(300)
  const { page, pageSize } = pageFrom(params)
  let filtered = [...mockInstitutionList]
  if (params.paramCode) { filtered = filtered.filter((item) => item.paramCode.includes(String(params.paramCode))) }
  if (params.paramName) { filtered = filtered.filter((item) => item.paramName.includes(String(params.paramName))) }
  if (params.paramValue !== undefined && params.paramValue !== '') { filtered = filtered.filter((item) => String(item.paramValue).includes(String(params.paramValue))) }
  if (params.effectiveDateRangeStart || params.effectiveDateRangeEnd) {
    const start = (params.effectiveDateRangeStart as string) || ''
    const end = (params.effectiveDateRangeEnd as string) || '9999-12-31'
    filtered = filtered.filter((item) => item.effectiveDate >= start && item.effectiveDate <= end)
  }
  if (params.remark) { filtered = filtered.filter((item) => item.remark.includes(String(params.remark))) }
  const total = filtered.length
  const data = filtered.slice((page - 1) * pageSize, page * pageSize)
  return { data, success: true, total }
}

export const updateInstitution = async (payload: Partial<InstitutionParam> & { id: string }): Promise<MutationResult> => {
  await delay(200)
  const idx = mockInstitutionList.findIndex((item) => item.id === payload.id)
  if (idx === -1) return { success: false }
  mockInstitutionList[idx] = { ...mockInstitutionList[idx], ...payload }
  return { success: true }
}

export const deleteInstitution = async (id: string): Promise<MutationResult> => {
  await delay(200)
  const idx = mockInstitutionList.findIndex((item) => item.id === id)
  if (idx === -1) return { success: false }
  mockInstitutionList.splice(idx, 1)
  return { success: true }
}

// ==================== 用户管理 ====================
export const fetchUserList = async (params: Record<string, unknown>): Promise<PaginatedResult<UserItem>> => {
  await delay(300)
  const { page, pageSize } = pageFrom(params)
  let filtered = [...mockUserList]
  if (params.username) { filtered = filtered.filter((item) => item.username.includes(String(params.username))) }
  if (params.realName) { filtered = filtered.filter((item) => item.realName.includes(String(params.realName))) }
  if (params.role) { filtered = filtered.filter((item) => item.role === params.role) }
  if (params.email) { filtered = filtered.filter((item) => item.email.includes(String(params.email))) }
  if (params.createdAtRangeStart || params.createdAtRangeEnd) {
    const start = (params.createdAtRangeStart as string) || ''
    const end = (params.createdAtRangeEnd as string) || '9999-12-31 23:59:59'
    filtered = filtered.filter((item) => (item.createdAt || '') >= start && (item.createdAt || '') <= end)
  }
  const total = filtered.length
  const data = filtered.slice((page - 1) * pageSize, page * pageSize)
  return { data, success: true, total }
}

export const updateUser = async (payload: Partial<UserItem> & { id: string }): Promise<MutationResult> => {
  await delay(200)
  const idx = mockUserList.findIndex((item) => item.id === payload.id)
  if (idx === -1) return { success: false }
  mockUserList[idx] = { ...mockUserList[idx], ...payload }
  return { success: true }
}

export const deleteUser = async (id: string): Promise<MutationResult> => {
  await delay(200)
  const idx = mockUserList.findIndex((item) => item.id === id)
  if (idx === -1) return { success: false }
  mockUserList.splice(idx, 1)
  return { success: true }
}
