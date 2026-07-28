import type { ReimbursementQuery, ReimbursementItem, PaginatedResult, ReimbursementUpdate, MutationResult } from './types';

// ---------- 内存数据源（开发期 mock，与 vite mock 等价，集中在此便于增删改查共用一份） ----------
const mockReimbursementList: ReimbursementItem[] = Array.from({ length: 46 }, (_, i) => {
  const id = String(i + 1).padStart(4, '0');
  return {
    id,
    reimbursementNo: `BX-2026-${id}`,
    subjectCode: `SC${1000 + i}`,
    subjectName: ['差旅费', '办公费', '招待费', '通讯费', '交通费'][i % 5],
    merchantName: ['北京华联超市', '上海锦江酒店', '深圳腾讯科技', '杭州阿里巴巴', '广州白云机场'][i % 5],
    amount: parseFloat((Math.random() * 5000 + 100).toFixed(2)),
    date: `2026-07-${String((i % 28) + 1).padStart(2, '0')}`,
    applicant: ['张三', '李四', '王五', '赵六'][i % 4],
    cardHolder: ['张三', '李四', '王五', '赵六'][i % 4],
    ruleDetail: `规则明细-${id}`,
    detailDesc: `详细描述内容-${id}`,
    consumeContent: ['办公用品', '机票', '酒店住宿', '餐饮', '打车'][i % 5],
    eventType: ['日常报销', '差旅报销', '招待报销'][i % 3],
    applyTime: `2026-07-${String((i % 28) + 1).padStart(2, '0')} ${String(8 + (i % 10)).padStart(2, '0')}:00:00`,
    supplierName: `供应商-${(i % 8) + 1}`,
    employeeId: `EMP${1000 + i}`,
  };
});

// 模拟网络延迟
const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** 查询报销列表（分页 + 筛选） */
export const fetchReimbursementList = async (params: ReimbursementQuery): Promise<PaginatedResult<ReimbursementItem>> => {
  await delay(300);
  const { page = 1, pageSize = 10, ...rest } = params;

  let filtered = [...mockReimbursementList];

  // 日期范围筛选（特殊处理）
  let dateStart: string | undefined;
  let dateEnd: string | undefined;
  Object.entries(rest).forEach(([key, value]) => {
    if (key === 'dateStart') dateStart = String(value);
    else if (key === 'dateEnd') dateEnd = String(value);
    else if (value === undefined || value === null || value === '') return;
    else {
      filtered = filtered.filter((item) =>
        String((item as unknown as Record<string, unknown>)[key] ?? '')
          .toLowerCase()
          .includes(String(value).toLowerCase()),
      );
    }
  });
  if (dateStart || dateEnd) {
    filtered = filtered.filter((item) => {
      const d = item.date;
      if (dateStart && d < dateStart) return false;
      if (dateEnd && d > dateEnd) return false;
      return true;
    });
  }

  const total = filtered.length;
  const start = (page - 1) * pageSize;
  const data = filtered.slice(start, start + pageSize);
  return { data, success: true, total };
};

/** 修改报销记录 */
export const updateReimbursement = async (payload: ReimbursementUpdate): Promise<MutationResult> => {
  await delay(300);
  const idx = mockReimbursementList.findIndex((item) => item.id === payload.id);
  if (idx === -1) return { success: false };
  mockReimbursementList[idx] = { ...mockReimbursementList[idx], ...payload };
  return { success: true };
};

/** 删除报销记录 */
export const deleteReimbursement = async (id: string): Promise<MutationResult> => {
  await delay(300);
  const idx = mockReimbursementList.findIndex((item) => item.id === id);
  if (idx === -1) return { success: false };
  mockReimbursementList.splice(idx, 1);
  return { success: true };
};

/** 批量删除报销记录 */
export const batchDeleteReimbursement = async (ids: string[]): Promise<{ success: boolean; deleted: number }> => {
  await delay(300);
  if (!ids || ids.length === 0) return { success: false, deleted: 0 };
  let deleted = 0;
  for (const id of ids) {
    const idx = mockReimbursementList.findIndex((item) => item.id === id);
    if (idx !== -1) {
      mockReimbursementList.splice(idx, 1);
      deleted++;
    }
  }
  return { success: true, deleted };
};
