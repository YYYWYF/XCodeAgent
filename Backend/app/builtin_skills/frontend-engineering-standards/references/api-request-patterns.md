# API 请求与页面消费

真实传输复用模板 `src/apis/service.ts`。GET 查询参数通过 `config.params` 传递，POST 请求体作为第二参数，DELETE 的配置可携带 `params` 或契约要求的 `data`。模板 service 校验后端响应并直接返回业务 `body`。

```ts
// src/apis/orderApi.ts
import service from './service';

export interface Order {
  id: string;
  status: string;
}

export interface OrderQuery {
  page: number;
  pageSize: number;
}

export interface OrderList {
  data: Order[];
  total: number;
}

/** 按已确认接口分页查询订单。 */
export function fetchOrderList(params: OrderQuery): Promise<OrderList> {
  return service.get<OrderList>('/api/orders', { params });
}

/** 提交订单确认，接口成功时无业务响应体。 */
export async function confirmOrder(id: string): Promise<void> {
  await service.post<void>(`/api/orders/${encodeURIComponent(id)}/confirm`);
}
```
