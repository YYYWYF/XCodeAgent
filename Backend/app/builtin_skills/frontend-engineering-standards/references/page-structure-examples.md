# 复杂查询编排

只有多个查询、筛选、分页或刷新规则需要复用，或者业务编排确实复杂时，才将其抽取到任务已授权的自定义 Hook。简单查询直接在页面使用 `useRequest`，不得为了符合示例而额外创建 Hook。

自定义 Hook 同样必须区分“当前请求参数”和“对应的成功结果”，不能把当前分页状态与上一次成功的列表混合返回。

```ts
// src/hooks/useOrders.ts
import { useState } from 'react';
import { useRequest } from 'ahooks';
import { fetchOrderList } from '@/apis/orderApi';
import type { OrderQuery } from '@/apis/orderApi';

/** 在确有复用需求时管理订单查询与分页。 */
export function useOrders() {
  const [query, setQuery] = useState<OrderQuery>({ page: 1, pageSize: 10 });

  const result = useRequest(
    async () => {
      const requestQuery = { ...query };
      const response = await fetchOrderList(requestQuery);
      return { requestQuery, response };
    },
    { refreshDeps: [query.page, query.pageSize] },
  );

  /** 更新分页条件，切换页容量时重置页码。 */
  function changePage(page: number, pageSize: number) {
    setQuery(current => ({
      page: current.pageSize === pageSize ? page : 1,
      pageSize,
    }));
  }

  const currentData =
    !result.loading &&
    !result.error &&
    result.data &&
    result.data.requestQuery.page === query.page &&
    result.data.requestQuery.pageSize === query.pageSize
      ? result.data.response
      : undefined;

  return {
    query,
    currentData,
    loading: result.loading,
    error: result.error,
    refresh: result.refresh,
    changePage,
  };
}
```

调用方仅将 `currentData` 作为当前查询结果展示；加载或失败时不得把旧 `data` 与当前 `query` 组合成新的列表与分页。若业务明确要求保留历史结果，应单独建模最近一次成功查询及其对应分页信息，不将其伪装成当前页。

固定状态文案可放在授权的 `src/constants/order.ts`。从 API 返回的选项由查询状态提供。可复用筛选表单放在授权的 `src/components/`，由页面传入状态和事件。
