# 复杂查询编排

多个查询共享筛选、分页和刷新规则时，把这部分编排放到任务已声明的 Hook。组件消费 Hook 返回的状态和操作，沿用 API 模块类型。

```ts
// src/hooks/useOrders.ts
import { useState } from 'react';
import { useRequest } from 'ahooks';
import { fetchOrderList } from '@/apis/orderApi';
import type { OrderQuery } from '@/apis/orderApi';

/** 管理列表分页并在每页条数变化时回到第一页。 */
export function useOrders() {
  const [query, setQuery] = useState<OrderQuery>({ page: 1, pageSize: 10 });
  const result = useRequest(() => fetchOrderList(query), { refreshDeps: [query] });

  /** 更新分页条件，切换页容量时重置页码。 */
  function changePage(page: number, pageSize: number) {
    setQuery(current => ({ page: current.pageSize === pageSize ? page : 1, pageSize }));
  }

  return { ...result, query, changePage };
}
```

固定状态文案可放在授权的 `src/constants/order.ts`。从 API 返回的选项由查询状态提供。可复用筛选表单放在授权的 `src/components/`，由页面传入状态和事件。
