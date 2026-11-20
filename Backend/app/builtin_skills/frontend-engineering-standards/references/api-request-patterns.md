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

/** 提交订单确认；按当前 API Contract，id 为不含 URL 保留字符的标准业务标识。 */
export async function confirmOrder(id: string): Promise<void> {
  await service.post<void>(`/api/orders/${id}/confirm`);
}
```

## 动态路径参数：直接插值与 URL 编码

两种写法都用于替换 API Contract 中的路径占位符（如 `/api/orders/{id}/confirm`），区别在于是否对参数值进行 URL 编码：

- **直接插值 `${id}`**：原样放入路径。适用于契约已明确为数字 ID、UUID、员工号等不包含 URL 保留字符的标准业务标识；本文件默认示例采用这种写法。
- **编码插值 `${encodeURIComponent(id)}`**：先编码参数值，避免参数中的 `/`、`?`、`#`、空格、`%` 等字符被解释为 URL 结构。仅在契约或已确认的业务取值允许此类特殊字符时按需使用，例如：

```ts
/** 提交订单确认，id 可能包含 URL 特殊字符，且传入的是未编码的原始值。 */
export async function confirmOrder(id: string): Promise<void> {
  await service.post<void>(`/api/orders/${encodeURIComponent(id)}/confirm`);
}
```

**生成与修复一致性**：优先按正式 API Contract 的参数取值约束选择写法；契约未要求支持特殊字符的普通业务 ID，默认直接插值。不要因复制另一份代码示例，就在两种写法之间反复切换；已有实现若满足契约且无实际 URL 编码问题，保持原样。两种写法不改变接口的 HTTP 方法或路径模板，不应仅因是否调用 `encodeURIComponent` 就认定 API 路径不匹配。
