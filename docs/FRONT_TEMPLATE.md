# 前端代码生成节点同步摘要

## 核心原则

前端模板遵循最小权限改造原则：只有平台 `authorization_manifest` 已明确声明资源绑定的页面、操作和接口，才参与前端权限投影。

- 未声明 `resourceKey` 表示该资源**不参与前端业务权限控制**，不是“无权限”。
- 不得为普通业务页面自动补充资源点、`RouteGuard`、菜单过滤、`Permission` 或接口权限逻辑。
- 前端模板不生成或维护 `authorization_manifest`；只在明确存在绑定时引用相应资源键。

## 文件职责

| 职责 | 文件 | 代码生成节点是否常规修改 |
|---|---|---|
| 页面资源键契约 | `src/constants/resources.ts` | 是，但仅添加 manifest 已明确绑定的资源键 |
| 页面组件导入与页面配置 | `src/constants/routes.tsx` | 是，仅在 XCODEAGENT 插槽内 |
| 页面配置类型 | `src/typings/routes.ts` | 否，除非调整配置协议 |
| 路由守卫与菜单派生 | `src/utils/route.tsx` | 否 |
| 权限菜单状态与首页落点 | `src/hooks/usePageMenus.ts` | 否 |
| 路由树组装 | `src/routes/index.tsx` | 否 |
| 权限运行时 Provider | `src/providers/AuthProvider.tsx` | 否 |
| 页面/操作权限组件 | `src/components/authorization/` | 仅在 manifest 明确绑定操作时引用 |
| 授权接口类型 | `src/typings/authorization.ts`、`src/typings/generated/` | 否；使用生成脚本更新 |

## 新增页面

先在 `src/pages/` 创建页面组件，然后只在 `src/constants/routes.tsx` 的两个插槽中追加导入和配置：

```tsx
// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START
import AssetListPage from '@/pages/AssetListPage';
// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END
```

### 未声明权限绑定的普通页面

```tsx
// XCODEAGENT_BUSINESS_ROUTES_START
{
  path: '/page/assets',
  menu: { key: 'assets', label: '资产管理' },
  element: <AssetListPage />,
},
// XCODEAGENT_BUSINESS_ROUTES_END
```

该页面会注册路由并显示菜单，但不会被 `RouteGuard` 包裹，也不会因权限加载或资源集合缺失而隐藏。

### 已声明权限绑定的页面

仅当平台 manifest 明确绑定该页面时：

1. 在 `src/constants/resources.ts` 添加与后端 `resourceKeys` 完全一致的资源键。
2. 在同一页面配置中增加 `resourceKey`：

```tsx
{
  path: '/page/assets',
  resourceKey: RESOURCES.PAGE.ASSET_LIST_PAGE,
  menu: { key: 'assets', label: '资产管理' },
  element: <AssetListPage />,
},
```

此时模板会自动为该路由添加 `RouteGuard`，并按权限过滤菜单；无需在 `src/routes/index.tsx` 或 Layout 中再写一套逻辑。

## 规则与边界

- `path` 必须唯一且使用绝对路径。
- `menu` 可省略：省略时页面仍注册路由，但不出现在菜单中。
- `/page` 跳转至配置顺序中第一个未绑定资源或已获授权的菜单页面。
- 当前预置的权限管理页为 `/authorization-management`，使用 `RESOURCES.SYSTEM.AUTHORIZATION_MANAGEMENT`。
- 对明确绑定的页面内操作，可使用 `Permission`（`src/components/authorization/Permission.tsx`）和 `usePermission`（`src/hooks/usePermission.ts`）；未绑定操作不得自动添加。
- 接口权限由平台 manifest 与后端实现；普通业务页面不修改 API 层以添加前端权限拦截。

## 禁止常规修改与验证

- 不修改 `src/routes/index.tsx`、`src/layout/index.tsx`、`src/utils/route.tsx` 或 `src/hooks/usePageMenus.ts` 来注册普通页面。
- 不恢复 `BIZ_MENUS`、`src/authorization/`、`DefaultPage`、`System/Role`。
- 不手工编辑 `src/typings/generated/authorizationApiTypes.ts`；使用 `pnpm authorization:types` 更新。
- 生成后执行 `pnpm test` 与 `pnpm build`。
