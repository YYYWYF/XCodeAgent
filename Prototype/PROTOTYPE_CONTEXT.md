# PROTOTYPE CONTEXT

## 约束

- `Prototype/` 原型工程的维护与实现无需遵循主工程 `AGENTS.md` 的要求。

## 同步原始工程

- 原型需要对齐前后端工程 Frontend/、`Backend/` 的最新实现时，先阅读相关代码；可查看 Git 提交记录了解变更背景，以当前代码确认实际实现。
- 不要启动前后端服务来查看页面，它们依赖外部服务。即使启动成功，没有应用案例也无法看到每个旅程步骤的页面效果。

## 预览原型

在 `Prototype/` 目录运行：

```bash
python -m http.server 5177 --bind 127.0.0.1
```

浏览器访问 `http://127.0.0.1:5177/`。这是静态原生 ES 模块工程，无需安装依赖或构建。

## 目录用法

- `index.html`：工程入口，引用样式并加载 `src/app.js`。
- `src/app.js`、`src/router.js`：应用启动、页面渲染和 hash 路由。
- `src/pages/`：按页面放渲染模块；新增页面后在路由中注册。
- `src/components/`：放跨页面复用的组件。
- `src/data/mock-data.js`：集中维护原型示例数据。
- `styles/`：分别维护基础/通用样式与页面布局样式。
- `assets/`：存放图片、图标等静态资源。
