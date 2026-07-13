# React 工程示例

## 5. 工程示例

此示例搭建了一个 React + TypeScript + babel 的工程，引入了 antd 组件库，并实现了按需加载，Less module 的配置，并集成了 prettier、eslint 等工程化配置提供开发态的格式化和代码校验。此外，在部署层面，实现了代码压缩混淆、css 文件抽离、gzip 压缩等性能优化配置，同时，构建工具还集成了 chunk 分析工具，帮助进行实际项目开发的 chunk 抽离配置。最后，添加了一份行内流水线的部署示例配置，以及行内流水线的详细配置示例等。

## 推荐工程目录

```
├── BuildScript
├── mock    // mock 服务
│   └── db
├── node_modules
├── public  // 公共静态资源
│   └── static
│       └── js
├── src     // 源代码目录
│   ├── apis        // 请求相关
│   ├── assets      // 静态资源
│   ├── components  // 公共组件
│   │   └── ErrorBoundary
│   ├── constants   // 公共常量
│   ├── hooks       // 公共 hooks
│   ├── pages       // 页面目录，每个文件夹是一个页面
│   │   ├── About
│   │   ├── Home
│   │   └── Login
│   ├── providers   // 公共 provider
│   ├── routes      // 路由
│   ├── styles      // 公共样式
│   ├── typings     // 类型定义
│   └── utils       // 工具函数
└── __tests__       // 单元测试文件
```

## 目录说明

- **BuildScript**：构建脚本目录，存放项目构建相关的自定义脚本。
- **mock**：本地 mock 服务，配合 mock/db 存放 mock 数据，用于前端与后端接口解耦开发。
- **node_modules**：npm 依赖包目录，由包管理器维护。
- **public**：公共静态资源目录，构建时会原样复制到产物目录。
- **src**：源代码目录，是主要开发工作区。
  - **apis**：接口请求封装，按业务模块划分。
  - **assets**：静态资源（图片、字体、SVG 等），会被打包处理。
  - **components**：项目内通用组件（如 `ErrorBoundary`、通用按钮、通用表格）。
  - **constants**：全局常量、枚举、配置项。
  - **hooks**：项目内通用自定义 hooks（以 `use` 开头）。
  - **pages**：页面目录，每个文件夹是一个页面，与路由结构对应。
  - **providers**：全局 Context Provider（如 Theme、Auth、I18n 等）。
  - **routes**：路由定义与配置。
  - **styles**：全局样式、主题、变量、Mixin。
  - **typings**：全局 TypeScript 类型定义与 `.d.ts` 声明。
  - **utils**：纯函数工具库。
- **__tests__**：单元测试文件目录，与源码结构保持一致。
