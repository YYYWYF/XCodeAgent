/** 报销记录实体 */
export interface ReimbursementItem {
  /** 主键 */
  id: string;
  /** 报销单号 */
  reimbursementNo: string;
  /** 科目代码 */
  subjectCode: string;
  /** 科目名称 */
  subjectName: string;
  /** 销方名称or商户名称 */
  merchantName: string;
  /** 金额 */
  amount: number;
  /** 日期 */
  date: string;
  /** 报销单申请人 */
  applicant: string;
  /** 交易记录持卡人 */
  cardHolder: string;
  /** 规则明细 */
  ruleDetail: string;
  /** 详细描述 */
  detailDesc: string;
  /** 消费内容 */
  consumeContent: string;
  /** 事项类型 */
  eventType: string;
  /** 申请时间 */
  applyTime: string;
  /** 供应商名称 */
  supplierName: string;
  /** 员工编号 */
  employeeId: string;
}

/** 报销列表查询参数 */
export interface ReimbursementQuery {
  page?: number;
  pageSize?: number;
  reimbursementNo?: string;
  subjectCode?: string;
  subjectName?: string;
  merchantName?: string;
  amount?: number;
  dateStart?: string;
  dateEnd?: string;
  applicant?: string;
  cardHolder?: string;
  ruleDetail?: string;
  detailDesc?: string;
  consumeContent?: string;
  eventType?: string;
  applyTime?: string;
  supplierName?: string;
  employeeId?: string;
}

/** 分页列表响应 */
export interface PaginatedResult<T> {
  data: T[];
  success: boolean;
  total: number;
}

/** 修改报销记录入参（id 必填，其余为可改字段） */
export type ReimbursementUpdate = Partial<Omit<ReimbursementItem, 'id'>> & { id: string };

/** 修改/删除结果 */
export interface MutationResult {
  success: boolean;
}
