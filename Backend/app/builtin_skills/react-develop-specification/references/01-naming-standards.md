# 命名规范

## 语言规范

**使用 TypeScript 作为项目的开发语言**，包括工程项目和 npm 包项目。

## 3.2.1【强制】文件命名规范，使用 PascalCase 格式命名

**说明**：新增项目的 React 组件文件使用 tsx 扩展名。
- 单个 React 组件文件使用 `PascalCase` 格式命名（如 `TestComponent.tsx`）；
- 如果整个文件夹是一个 React 组件，文件夹使用 `PascalCase` 格式命名，文件命名为 `index.tsx`（如 `TestComponent/index.tsx`）。

存量项目建议修改为 PascalCase 格式命名（根据项目实际情况判断是否修改）。

## 3.2.2【强制】包含 jsx 的文件扩展为 tsx

**说明**：参考 eslint-plugin-react 中 `react/jsx-filename-extension` 规则：将定义 JSX 的文件后缀名定义为 tsx，该规则只限制使用 TypeScript 编码项目。

**正例：**
```tsx
// filename: MyComponent.tsx
export const MyComponent = () => {
  return <div />;
};
```

**反例：**
```ts
// filename: MyComponent.ts
export const MyComponent = () => {
  return <div />;
};
```

## 3.2.3【强制】组件命名规范，使用 PascalCase 格式命名

**说明**：参考 eslint-plugin-react 中 `react/jsx-pascal-case` 规则：需要为每一个定义的 React 组件以 PascalCase 的格式进行命名。

**正例：**
```tsx
// 函数组件
interface IApp {
  id: number;
  text: string;
}

const App = ({ id, text }: IApp) => {
  return (
    <div id={id}>{text}</div>
  );
};

export default App;

export const TestComponent = () => {
  // 其他代码
};

// 类组件
export default class TestComponent extends React.Component {
  // 其他代码
}
```

**反例：**
```tsx
// 函数组件
interface IApp {
  id: number;
  text: string;
}

export default ({ id, text }: IApp) => {
  return (
    <div id={id}>{text}</div>
  );
};

export const testComponent = () => {
  // 其他代码
};

// 类组件
export default class Test_Component extends React.Component {
  // 其他代码
}
```

## 3.2.4【强制】函数和属性命名，使用 camelCase 格式命名

**说明**：普通函数（不含：函数式组件、构造函数）、本地变量、属性的命名应使用 `camelCase` 拼写法。

**正例：**
```tsx
class App extends React.Component {
  bestFriend: string;
  // 其他代码
  getValue = () => {
    const lastIndex = 0;
    // 其他代码
  }
}
```

**反例：**
```tsx
class App extends React.Component {
  best_friend: string;
  // 其他代码
  Getvalue = () => {
    const last_index = 0;
    // 其他代码
  }
}
```

## 3.2.5【强制】类型命名规范，使用 PascalCase 格式命名

**说明**：文件中的类型定义，应使用 PascalCase 拼写法命名，类型定义应写在类型使用之前。

**正例：**
```tsx
interface ButtonProps {
  // 属性
}

const Button = ({}: ButtonProps) => {
  return <button>{"button"}</button>;
};
```

**反例：**
```tsx
const Button = ({}: buttonProps) => {
  return <button>{"button"}</button>;
};

interface buttonProps {
  // 属性
}
```

## 3.2.6【推荐】样式命名规范

**说明**：在大型项目中使用 CSS 时，需解决以下两个问题：

1. 原生 CSS 缺乏属性嵌套、函数、模块等，使得样式代码的可维护性和复用性较低；
2. CSS 属性的影响范围是全局的，在大型项目的多人协作过程中，容易出现两条 CSS 的选择器相同的情况，引起样式的相互覆盖或冲突。

为了解决大型项目协作中的问题，同时考虑 CSS 的可读性和复用性，因此建议首选使用类名（class）控制样式的方式，除少数特殊场景之外，尽量不使用内联 style 样式属性。同时，根据项目实际情况，选择是否使用预处理器 Less、Scss 和 Stylus。

此外，项目中应采用 Less/CSS/Scss module 的方式保证样式的隔离，项目工程中应同时支持 Less/CSS/Scss module 和全局样式。

如下示例以 Less 和 Less module 为示例，其他相关规范如下：

1. Less module 文件以 `[name].module.less` 格式命名，其中 name 为对应模块的名称，文件与对应模块 tsx 文件同级；
2. 在组件 tsx 文件中，以 styles 为引入 Less module 命名；
3. CSS 类名采用 BEM（block__element--modifier）方式，每一部分使用小写单词 + 中划线 `-` 组成，如 `search-form__button--state-success`；
4. 搭配 classnames 库进行一些类名的高级处理；
5. 全局样式的 Less 文件，以 `[name].less` 命名，或在 Less module 中使用 `:global` 语法书写全局样式；
6. 若开发独立的 npm 包，应使用全局样式，而不应使用 Less/CSS/Scss module，也可以通过添加包名级的方式对样式进行命名，实现样式隔离，如 `[packageName]-block__element--modifier`；
7. 开发团队可按需使用 CSS-in-JS 实现动态样式设置，可以使用 style-components 或 @emotion/react 进行组件样式的封装。但 CSS-in-JS 会带来一定的性能损耗，增加了运行时开销，在服务端渲染中，CSS-in-JS 还会出现渲染出错的问题，在使用中应关注这些问题。

**正例：**
```tsx
import styles from './App.module.less'; // less module
import './App.less';

const Button = () => {
  return (
    <button className={styles["button-primary"]}>
      {"button"}
    </button>
  );
};
```

**备注**：在 TypeScript 编码的项目中使用 CSS module 需手动添加 ts 声明，以 less module 为例：

```ts
declare module '*.module.less' {
  const classes: { readonly [key: string]: string };
  export default classes;
}
```
