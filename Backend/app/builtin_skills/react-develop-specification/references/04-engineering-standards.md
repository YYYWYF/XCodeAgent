# React 工程规范

本章规范了 React 的工程结构，包括版本信息、相关 npm 包使用信息、工程化配置信息等。

此规范文档使用的依赖版本参考，项目根据实际场景使用合适版本：

- Node.js: `16.14.0`
- VSCode: `1.81.1`
- npm: `6.14.17`
- yarn: `1.22.19`
- eslint: `^7.32.0`
- prettier: `^2.7.1`
- typescript: `^4.9.3`
- webpack: `^5.76.2`

## 4.1 React 版本规范

考虑 React 的功能和生态，推荐新增的 React 工程使用 `v18.2.0` 及以上版本。React 各版本区别参见附录 6.1 节：React 的版本更新差异。

## 4.2 组件定义规范

需要对项目中的组件进行合适的 TypeScript 定义。

### 4.2.1【强制】函数式组件按需定义组件属性及 hooks 类型

**说明**：函数式组件只有外部的 props 输入，只需要定义组件属性；函数式组件的内部生命周期及内部状态由对应的 hooks 实现。

**正例：**

```tsx
interface IHello {
  name: string;
  age: number;
}

const Hello = ({ name }: IHello) => {
  const [isJohn, setIsJohn] = useState<boolean>(false);

  useEffect(() => {
    setIsJohn(name === "John");
  }, [name, setIsJohn]);

  return <div>{`Hello ${isJohn ? "John" : "someone"}`}</div>;
};

export default Hello;
```

**反例：**

```tsx
const Hello = ({ name }) => {
  const [isJohn, setIsJohn] = useState(false);

  useEffect(() => {
    setIsJohn(name === "John");
  }, [name, setIsJohn]);

  return <div>{`Hello ${isJohn ? "John" : "someone"}`}</div>;
};

export default Hello;
```

### 4.2.2【强制】类组件按需定义属性、状态、组件生命周期等类型

**说明**：类组件由于存在内部的 state 以及组件的相关生命周期，因此需要对组件的属性 props、状态 state、组件生命周期等进行类型定义。

**正例：**

```tsx
import React, { ReactNode } from "react";

export interface ButtonProps {
  color: string;
}

export interface ButtonState {
  count: number;
}

class Button extends React.Component<ButtonProps, ButtonState> {
  state = {
    count: 0,
  };

  static getDerivedStateFromProps(
    nextProps: Readonly<ButtonProps>,
    prevState: ButtonState
  ): Partial<ButtonState> | null {
    return null;
  }

  constructor(props: ButtonProps) {
    super(props);
  }

  componentDidMount() {}

  componentDidUpdate(
    prevProps: Readonly<ButtonProps>,
    prevState: Readonly<ButtonState>,
    snapshot?: any
  ) {}

  shouldComponentUpdate(
    nextProps: Readonly<ButtonProps>,
    nextState: Readonly<ButtonState>,
    nextContext: any
  ): boolean {
    return true;
  }

  getSnapshotBeforeUpdate(
    prevProps: Readonly<ButtonProps>,
    prevState: Readonly<ButtonState>
  ): any | null {
    return null;
  }

  render(): ReactNode {
    return <div>{this.state.count}</div>;
  }
}
```

## 4.3 项目 npm 包规范

**说明**：本章节规范了 React 工程在开发时需要添加的一些必备的 npm 包，如路由管理、状态管理等。在添加 npm 包时，需要遵循以下几点约束：

1. npm 包有对应相对完善的社区解决方案。
2. npm 包必须有活跃的更新与修复，或已经相对成熟。
3. 不能在项目中引入多个相同功能的 npm 包，如同时引入 `monaco-editor` 和 `ace-editor`。
4. npm 包有良好的工程化基础，利于项目后期的构建与打包。

### 4.3.1【推荐】路由管理

**说明**：使用 `react-router` 作为 react 工程的路由管理库。react-router 最新版本为 v6，在 v5 的基础上，简化了嵌套路由的定义，强化了函数式和 hook 思想。若项目为客户端渲染，则直接安装 `react-router-dom`。

**正例：**

```tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Foo from "./Foo/";
import Bar from "./Bar/";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/foo" element={<Foo />} />
        <Route index={true} element={<Bar />} />
      </Routes>
    </BrowserRouter>
  );
}
```

### 4.3.2【推荐】状态管理

**说明**：在 React 工程中，使用 Context API 搭配适当的状态管理工具（redux、mobx、recoil、Zustand）的混合使用方式。在 React 项目中是否使用状态管理工具，需要开发者依据模块的设计进行评估，避免过度设计。使用状态管理工具，根据实际使用情况在项目中使用 redux、mobx、recoil 和 Zustand。

### 4.3.3【推荐】组件库

**说明**：工程构建的组件库使用 antd，推荐使用 v4 版本，项目可按需升级 v5。项目中若含有相关组件的扩展，也应基于 antd 进行，如基于 antd 进行二次组合封装、使用 pro-components 等。在使用 antd 的过程中，通过一些工程化的配置，可实现 antd 的按需引入，Less module 配置，主题定制等。v5 版本的技术调整：

1. 弃用 less，采用 CSS-in-JS，更好地支持动态主题。
2. 移除 css variables 以及在此之上构筑的动态主题方案。
3. LocaleProvider 在 4.x 中已经废弃（使用 `<ConfigProvider locale />` 替代），5.x 里彻底移除了相关目录 `antd/es/locale-provider`、`antd/lib/locale-provider`。
4. 内置的时间库使用 Dayjs 替代 Moment.js。
5. 不再支持 babel-plugin-import，CSS-in-JS 本身具有按需加载的能力，不再需要插件支持。

### 4.3.4【推荐】其他库

**说明**：此章节规范了 React 工程中常用的一些工具类的库，如 http 客户端等。

1. **http 客户端**
   使用 `axios` 作为 React 工程的 http 客户端。axios 是一个基于 Promise 的 HTTP 库，可以用在浏览器和 Node.js 中，拥有以下特性：支持 Promise API、能够拦截请求和响应、能够转换请求和响应数据、客户端支持防御 CSRF 攻击、同时支持浏览器和 Node.js、能够取消请求或自动转换 JSON 数据。axios 的详细 API 见官方文档：https://axios-http.com。
   除 axios 之外，还可使用原生的 Fetch API 做为 http 客户端。

2. **函数工具库**
   使用 `lodash` 作为 React 工程的函数工具库。lodash 提供了关于 array、number、objects、string 的常用方法，帮助处理项目中的数据。lodash 官方提供了插件可以对 lodash 进行按需引入处理：`lodash-webpack-plugin`、`babel-plugin-lodash` 和 `lodash-es`，lodash 的全部功能详见官方文档：https://lodash.com。

3. **React hooks 库**
   使用 `ahooks` 作为 React 工程的 hooks 库，推荐使用 v3 版本。ahooks 不仅提供了常用的用于异步参数管理的 useRequest、组件生命周期关联的 useMount、useUnmount，还有一些搭配 antd 使用的 hook，如 `useAntdTable`、`usePagination` 等。ahooks 更多使用方式详见官方文档：https://ahooks.js.org。

4. **图表库**
   使用 `echarts` 作为 React 工程的数据可视化图表库。echarts 提供了常规的折线图、柱状图、散点图、饼图、K 线图，用于统计的盒形图，用于地理数据可视化的地图、热力图、线图，用于关系数据可视化的关系图、旭日图，多维数据可视化的平行坐标，还有用于 BI 的漏斗图、仪表盘，并且支持图与图之间的混搭。由于 echarts 是以原生 JavaScript 的方式提供，若需要 react 的相关封装，可使用开源 npm 包 `echarts-for-react`。echarts 的更多的使用方式详见官方文档：https://echarts.apache.org/zh/index.html。

5. **动画库**
   React 常用的动画库有：react-transition-group、react-spring、Framer Motion。

   - **react-transition-group** 提供了在组件挂载、卸载、切换时配置其过渡样式的功能，可以辅助地实现更复杂的动画效果。官网：https://reactcommunity.org/react-transition-group。
   - **react-spring** 是一个基于弹簧物理模型创建平滑、自然且高性能动画效果的现代化库。通过使用一系列 Hooks（如 useSpring、useTransition 等），可以轻松地在函数式组件中实现复杂动画。官网：https://react-spring.io。
   - **Framer Motion** 是一个功能丰富且易于上手的 React 动画库，支持声明式语法和强大的 API 设计。除了常见的 CSS 过渡和变换外，还提供了诸如拖拽、SVG 路径等高级特性。官网：https://www.framer.com/api/motion。

6. **日期处理工具库**
   使用 `Day.js` 作为 React 工程的日期处理库。Day.js 是一个轻量级的日期处理库，它的目标是为开发者提供一种简单、易用且高效的方式来解析、操作和格式化日期与时间。相较于其他日期处理库（如 Moment.js），Day.js 文件体积更小，仅 2KB（压缩后）。同时，Day.js 提供了类似于 Moment.js 的链式语法和 API 设计，使得在项目中替换或迁移变得容易。Day.js 的全部功能详见官方文档：https://day.js.org。

7. **组件懒加载**
   使用组件懒加载可减少应用初次加载的 js 文件数量，缩减 bundle 的体积，优化首页加载性能。可以通过两种方式实现组件懒加载，在客户端渲染的场景下优先使用 React.lazy。

   1. **React.lazy**：使用 React 原生 API `React.lazy` 搭配 `React.Suspense` 即可实现组件懒加载，示例如下：

   ```tsx
   import React from "react";

   const Counter = React.lazy(() => import("./Counter"));

   const App = () => {
     return (
       <React.Suspense fallback={<div>{"Loading"}</div>}>
         <Counter />
       </React.Suspense>
     );
   };
   ```

   2. **loadable-components 和 react-loadable**：在 React 16 和 17 且需要 SSR 的场景下，懒加载可使用 loadable-components 和 react-loadable 实现组件懒加载。以 loadable-components 为例其使用示例如下：

   ```tsx
   import loadable from "@loadable/component";

   const Counter = loadable(() => import("./Counter"));

   const App = () => {
     return <Counter />;
   };
   ```

## 4.4 前端工程化规范

此章节规范了 React 工程在构建时的工程化配置，包括 React 工程的构建工具、代码格式化工具、代码质量检测工具、测试工具。

### 4.4.1【推荐】构建工具

**说明**：构建工具具根据团队实际情况、项目模式合理选择使用。本规范推荐内基础设施的情况，建议常规业务工程使用基于 `webpack + babel + TypeScript + eslint + prettier` 进行项目的初始化。webpack 具有一套相对非常完整的构建生态和解决方案。

若对初始化的工程有定制化配置需求，使用 craco，代替运行 `npm run eject`。

### 4.4.2【推荐】格式化工具

**说明**：使用 prettier 作为工程中代码格式化工具，安装 prettier 之后，prettier 会使用默认的配置规则，需要在工程中配置 prettier 的配置文件以自定义规则。在项目工程根目录下编写 `prettier.config.js` 文件作为项目的相关格式化配置。搭配 prettier 格式化工具，规范在第三章节的 React 编码规范的要求。

在使用 prettier 的过程中，优先在代码开发过程中实时进行代码的格式化，而不是在代码提交时的时候。

```js
module.exports = {
  printWidth: 120,
  tabWidth: 2,
  useTabs: false,
  semi: true,
  singleQuote: true,
  quoteProps: "as-needed",
  jsxSingleQuote: true,
  trailingComma: "all",
  bracketSpacing: true,
  jsxBracketSameLine: true,
  arrowParens: "always",
  rangeStart: 0,
  rangeEnd: Infinity,
  requirePragma: false,
  insertPragma: false,
  proseWrap: "preserve",
  htmlWhitespaceSensitivity: "css",
  vueIndentScriptAndStyle: false,
  endOfLine: "lf",
  embeddedLanguageFormatting: "auto",
  bracketSameLine: true,
};
```

### 4.4.3【强制】代码校验工具

**说明**：使用 eslint 作为工程中的代码错误自动检测工具。

### 4.4.4【推荐】样式校验工具

**说明**：在 React 工程中，使用 stylelint 作为样式的校验工具，与 eslint 类似，stylelint 是一个针对样式文件的代码规范检测工具，支持 Less、Sass 这类预处理器，并且有非常多的第三方插件，搭配 prettier 使用可对样式进行校验及格式化。

使用 stylelint 需要在工程中安装 stylelint 的配置文件 `stylelint-config-standard` 和 `stylelint-config-prettier`，并在工程内新建 `stylelint.config.js` 文件进行配置：

```js
module.exports = {
  extends: ["stylelint-config-standard", "stylelint-config-prettier"],
};
```

### 4.4.5【推荐】提交校验工具

**说明**：husky 是一个 git 钩子工具，主要使用其 pre-commit 钩子，在代码提交时进行一些操作，推荐搭配 lint-staged 使用，在提交的文件中进行过滤，针对性的执行特定的指令。

### 4.4.6【推荐】测试工具

**说明**：jest 是由 Facebook 维护的 JavaScript 测试框架，使用此测试框架可对 React 组件进行单元测试。还可以搭配 husky 在代码推送或提交前进行单元测试的校验，测试通过了才能进行代码的提交/推送。

与 jest 相关的工具库：

1. **ts-jest**：如果需要 Jest 在运行时对测试用例做类型检查可以安装 ts-jest，ts-jest 是一个让 Jest 支持 TypeScript 的预处理器，ts-jest 需要与 typescript 一起使用。

2. **@testing-library/react**：是一个用于测试 React 组件的轻量级库。它提供了一系列实用工具，使你能够以用户为中心的方式编写和执行测试。

3. **@testing-library/jest-dom**：是一个用于 Jest 的自定义匹配器集合，它提供了一系列有用的断言方法来测试 DOM 元素，与 @testing-library/react 等其他库结合使用。

4. **jest-environment-jsdom**：是一个提供 JSDOM 环境的 Jest 测试环境。JSDOM 是一个用于模拟浏览器 DOM API 的 JavaScript 实现，它允许你在 Node.js 中运行和测试与浏览器相关的代码。

5. **@testing-library/react-hooks**：提供了一种简单且直观的方式来测试这些自定义 hooks。

### 4.4.7【推荐】调试工具

**说明**：推荐使用相关调试工具，如 storybook、react-devtool、redux-devtool 等。
