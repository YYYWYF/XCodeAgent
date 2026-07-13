# React 规则

## 通用 React 规则

1. 保持渲染纯净。不要在渲染期间请求数据、订阅、修改外部状态、记录分析日志或操作 DOM。
2. 优先使用已有的项目模式、包装器、请求 Hook、样式和命名。
3. 不要进行无关的重构。
4. 始终在适用场景下处理加载中、空数据、错误、无权限和未找到状态。

## 状态

使用最小合适的状态持有者：

| 状态类型 | 首选持有者 |
| --- | --- |
| 单组件 UI 状态 | `useState` |
| 复杂局部交互状态 | `useReducer` |
| 可分享/可恢复的页面状态 | URL 路径或查询参数 |
| 低频跨树状态 | Context |
| 高频全局协作状态 | 已有的外部 store |
| 服务端数据 | 已有项目请求 Hook 或请求缓存 |
| 表单数据 | 表单库或局部受控状态 |

避免重复状态：

```tsx
const canEdit = appInfo?.appStatus === AppStatusType.NORMAL && hasPermission;
```

不要将该派生值存入 state 并用 `useEffect` 同步。

## Context

1. Context 用于低频共享状态。
2. 不要将大量 API 响应、大列表或快速变化的编辑器状态放入一个 Context。
3. 使用 `useMemo` 记忆化 Context 值。
4. 需要时保持 Provider 函数稳定。
5. 按更新频率拆分 Context，如状态 context 和操作 context。

## Hook

1. Hook 只能在 React 函数组件或自定义 Hook 中运行。
2. Hook 必须在顶层调用，每次渲染保持相同顺序。
3. 绝不在条件、循环、事件处理器、普通函数或 `try/catch` 中调用 Hook。
4. 绝不在提前 return 之后调用 Hook。
5. 不要通过删除依赖项来消除 `exhaustive-deps` 警告；应重构代码结构。
6. 自定义 Hook 必须以 `use` 开头，并体现业务含义。
7. 组件私有 Hook 放在组件目录中；功能内共享 Hook 放在功能模块 `hooks/` 中；跨功能 Hook 放到 `src/hooks/`。

正确示例：

```tsx
function Editor() {
  const { data, loading, error } = usePageInfo();
  const contextValue = useMemo(() => ({ data }), [data]);

  if (error) return <ErrorView />;
  if (loading) return <Loading />;

  return (
    <EditorContext.Provider value={contextValue}>
      <Content />
    </EditorContext.Provider>
  );
}
```

错误示例：

```tsx
function Editor() {
  const { data, loading } = usePageInfo();
  if (loading) return <Loading />;

  const contextValue = useMemo(() => ({ data }), [data]);
  return <Content />;
}
```

## Effect

仅在需要与外部系统同步时使用 `useEffect`：

1. 事件订阅。
2. 浏览器 API、DOM API 和第三方 SDK。
3. 外部连接及清理。
4. 手动请求/取消流程。
5. 分析、日志、页面标题等副作用。

不要用 Effect 处理派生状态：

```tsx
const pageTitle = `${pageInfo?.name ?? ''}-${appInfo?.appName ?? ''}`;
```

涉及订阅、创建定时器、启动异步流程或分配外部资源的 Effect 必须做清理并处理竞态。

## API

1. `service/` 管理请求实例、拦截器、登录行为、加密和错误归一化。
2. `apis/` 只包含业务 API 函数；不应包含组件状态逻辑。
3. 组件和业务 Hook 使用已有的项目请求 Hook。
4. 不要引入新的请求库，除非已声明并获批。
5. API 输入输出必须有明确类型。
6. 独立请求应并行发出。
7. 依赖请求使用 `ready` 或项目等效方案。
8. 搜索、自动补全、筛选和自动保存必须做防抖/节流并处理竞态。
9. 写操作必须考虑重试、幂等、版本或锁机制。

## 组件

1. 优先使用普通函数组件而非 `React.FC`。
2. Props 必须有明确类型。
3. 需要时显式声明 `children?: React.ReactNode`。
4. 页面组件编排数据与布局；UI 组件负责展示和局部交互。
5. 大页面、编辑器、预览器和低频模块可使用 `React.lazy`。
6. 懒加载代码必须有 `Suspense`；chunk 加载失败需要 Error Boundary 覆盖。
7. 复杂表单、表格、筛选、弹窗、详情面板和工具栏应按功能拆分。

## 类型

1. 对象形状优先使用 `interface`。
2. 联合类型、工具类型和映射类型使用 `type`。
3. 稳定的后端数值编码可使用 `enum`。
4. 前端选项列表优先使用 `as const` 加联合类型。
5. API 参数、API 响应、组件 props 和 UI 状态必须分别建模。
6. 避免使用 `any`；使用 `unknown` 并窄化类型。如果外部库缺少类型，局部隔离 `any` 并解释原因。
7. 不要将原始后端响应对象深层传递到组件树中。

## 性能

1. 操作相互独立时使用并行异步工作。
2. 大模块使用懒加载。
3. 长列表使用分页、虚拟滚动或分块渲染。
4. 列表 key 必须稳定且唯一；对可变列表避免使用 index 作为 key。
5. Context 值引用必须稳定。
6. 不要为每个函数包裹 `useCallback` 或为每个对象包裹 `useMemo`。
7. 仅在 Context、Hook 依赖项、`React.memo` 子组件或昂贵计算时稳定引用。

## 路由与错误

1. 路由常量应集中管理。
2. 当匹配项目时使用配置式路由如 `useRoutes`。
3. 页面级 Provider 可放在路由节点上。
4. 动态路由必须校验后端配置并处理无权限/未找到。
5. 路由兜底必须渲染 NotFound。
6. 路由页面、懒加载模块和编辑器核心区域需要 Error Boundary 覆盖。
7. 优先使用已有的项目 Error Boundary；不要为此引入未声明的库。
