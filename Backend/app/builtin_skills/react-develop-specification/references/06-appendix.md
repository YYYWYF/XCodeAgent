# 附录

## 6.1 React 版本差异

### 6.1.1 16 版本

- 支持 React.PropTypes、React.createClass、React.DOM 等 API（16 版本开始废弃）。
- `React.render` 等 API 迁移到 `ReactDOM`。
- SSR 相关 API 迁移到 `ReactDOMServer`。
- 引入错误边界（Error Boundaries）机制。

### 6.1.2 17 版本

- **废弃 API**（17+ 不再使用）：`componentWillMount`、`componentWillReceiveProps`、`componentWillUpdate`。
- 事件委托目标由 `document` 改为 React 树的根 DOM 容器，支持多个 React 版本共存。
- 不再引入 React（新 JSX 转换）。

### 6.1.3 18 版本

- **废弃 API**（18+ 不再使用）：
  - `ReactDOM.render()`
  - `ReactDOM.hydrate()`
  - `ReactDOM.unmountComponentAtNode()`
  - `ReactDOM.renderSubtreeIntoContainer()`
  - `ReactDOMServer.renderToNodeStream()`
- 新增 `createRoot` / `hydrateRoot` API。
- 引入并发特性（Concurrent Rendering）：自动批处理、`startTransition`、`useDeferredValue`、`useTransition`、`useId`、`useSyncExternalStore`、`useInsertionEffect` 等新 hooks。
- 支持 Suspense on the Server（SSR + Suspense）。
- Strict Mode 下双重调用 mount 逻辑，帮助发现副作用问题。

## 6.2 推荐的 tsconfig.json 配置

参考配置：

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "noFallthroughCasesInSwitch": true,
    "module": "esnext",
    "moduleResolution": "node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "baseUrl": "./src",
    "paths": {
      "@/*": ["*"]
    }
  },
  "include": ["src"]
}
```

项目可依据实际需要调整 `target`、`lib`、`paths`、严格性选项等。

## 6.3 React 过时 API

综合 3.3.11 的说明，梳理如下：

| 过时 API | 迁移建议 |
|----------|---------|
| `React.render` | 使用 `ReactDOM.render`（16-17）或 `createRoot(container).render()`（18+） |
| `React.unmountComponentAtNode` | 使用 `ReactDOM.unmountComponentAtNode`（16-17）或 `root.unmount()`（18+） |
| `React.findDOMNode` | 使用 `ref` 直接引用 DOM 节点 |
| `React.renderToString` | 使用 `ReactDOMServer.renderToString` |
| `React.renderToStaticMarkup` | 使用 `ReactDOMServer.renderToStaticMarkup` |
| `React.PropTypes` | 使用 TypeScript 类型定义 |
| `React.createClass` | 使用 `class ... extends React.Component` 或函数式组件 |
| `React.DOM` | 使用 JSX |
| `componentWillMount` | 使用 `constructor` 或 `componentDidMount` |
| `componentWillReceiveProps` | 使用 `getDerivedStateFromProps` 或 `componentDidUpdate` |
| `componentWillUpdate` | 使用 `getSnapshotBeforeUpdate` 或 `componentDidUpdate` |
| `ReactDOM.render`（18+） | 使用 `createRoot` |
| `ReactDOM.hydrate`（18+） | 使用 `hydrateRoot` |
| `ReactDOMServer.renderToNodeStream`（18+） | 使用 `renderToPipeableStream` 或 `renderToReadableStream` |

## 6.4 服务端渲染的常见使用场景

**SSR（服务端渲染）** 相比 CSR（客户端渲染）有以下常见优势：

1. **更快的首次内容绘制（FCP）**：HTML 由服务端生成后直接返回，用户能更快看到首屏内容。
2. **更好的搜索引擎优化（SEO）**：搜索引擎爬虫能直接抓取服务端渲染的 HTML 内容，对内容型站点尤其重要。
3. **减轻客户端负担**：初次渲染工作交给服务端，避免客户端设备性能不足导致的渲染延迟。
4. **更好地利用服务器资源**：可将大量计算和数据处理放在服务端，客户端只负责展示。

**常见适用场景：**

- 内容型站点（新闻、博客、商城首页等）需要良好 SEO。
- 对首屏性能有严格要求的营销页、活动页。
- 需要在低端设备上获得较好体验的 C 端场景。
- 混合渲染（同构应用），首屏 SSR，交互后走 CSR。

**常见框架：**

- **Next.js**：功能完善的 React SSR/SSG 框架，社区最活跃。
- **Remix**：基于 Web 标准的现代化 React 框架。
- **自建 SSR**：基于 `renderToPipeableStream` / `renderToString` 结合 Express/Koa 等自定义搭建。

**注意事项：**

- 组件中避免直接访问 `window`、`document` 等浏览器全局对象，必要时在 `useEffect` 中访问。
- CSS-in-JS 方案在 SSR 场景下需注意样式提取和水合问题。
- 服务端渲染增加了服务器压力，需评估机器资源和缓存策略（CDN、页面缓存、数据缓存）。
