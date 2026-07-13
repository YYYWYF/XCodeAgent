# 代码规范（3.3）

本章共 45 条规范，涵盖导入、类型、JSX、属性、Fragment、层级、废弃 API、setState/state、key、生命周期、事件、hook、ref、DOM 属性、a11y、注释等。

---

## 3.3.1【强制】导入语句写在其他语句之前

**说明**：导入语句写在其他语句之前。

**正例：**
```tsx
import Foo from "./Foo";

const FooWrap = () => {
  return (
    <Foo>
      <div>innerFoo</div>
    </Foo>
  );
};
```

**反例：**
```tsx
const FooWrap = () => {
  return (
    <Foo>
      <div>innerFoo</div>
    </Foo>
  );
};

import Foo from "./Foo";
```

---

## 3.3.2【推荐】除特殊场景外，不允许代码中使用 any 类型

**说明**：除访问第三方库、定义不确定场景类型（如接口返回类型）之外，不允许代码中使用 any 类型，且在使用处不滥用 any 类型。

**正例：**
```ts
interface IHello {
  name: string;
  age: number;
}
```

**反例：**
```ts
interface IHello {
  name: any;
  age: any;
}
```

---

## 3.3.3【强制】JSX 属性值使用单引号和模板字符串

**说明**：JSX 属性值使用单引号和模板字符串，以格式化工具进行规范，可通过配置 prettier 的 `jsxSingleQuote` 和 `singleQuote` 的值为 true 进行约束。

**正例：**
```tsx
<Route path={`/${name}`} />
```

**反例：**
```tsx
<Route path={"/" + name} />
```

---

## 3.3.4【强制】禁止将 children 属性作为组件属性进行传递

**说明**：参考 eslint-plugin-react 中 `react/no-children-prop` 规则：在 JSX 中，children 属性不应该将其定义为可传递的属性 props，而应当是开始标签和结束标签间的内容，或使用 render props 模式。

**正例：**
```tsx
import Foo from "./Foo";

const FooWrap = () => {
  return (
    <Foo>
      <div>innerFoo</div>
    </Foo>
  );
};
```

**反例：**
```tsx
import Foo from "./Foo";

const FooWrap = () => {
  return (
    <Foo children={<div children={"innerFoo"} />} />
  );
};
```

---

## 3.3.5【推荐】对组件属性进行解构

**说明**：参考 eslint-plugin-react 中 `react/destructuring-assignment` 规则：函数式组件和类组件中，对 props、context 进行解构，保证代码清晰。

**正例：**
```tsx
interface IApp {
  id: number;
  text: string;
}

export const App = ({ id, text }: IApp) => {
  return (
    <div id={id}>{text}</div>
  );
};
```

**反例：**
```tsx
interface IApp {
  id: number;
  text: string;
}

export const App = (props: IApp) => {
  return (
    <div id={props.id}>{props.text}</div>
  );
};
```

---

## 3.3.6【推荐】建议给可选属性设置默认值

**说明**：参考 eslint-plugin-react 中 `react/require-default-props` 规则：建议给组件的可选属性设置默认值，对传入变量都进行是否为空的判断，防止出现属性未定义或为空时程序报错。

**正例：**
```tsx
interface IApp {
  id: number;
  textList?: string[];
}

export const App = ({ id, textList = [] }: IApp) => {
  return (
    <div id={id}>{textList?.map(text => text)}</div>
  );
};
```

**反例：**
```tsx
interface IApp {
  id: number;
  textList?: string[];
}

export const App = ({ id, textList }: IApp) => {
  return (
    <div id={id}>{textList.map(text => text)}</div>
  );
};
```

---

## 3.3.7【推荐】避免较大范围的使用属性扩展运算符

**说明**：尽量避免较大范围的使用属性扩展运算符。在 React 工程中，需要由 TypeScript 详细的定义组件的属性。

**正例：**
```tsx
import MyComponent from "./MyComponent";

interface IApp {
  id: number;
  text: string;
}

export const App = ({ id, text }: IApp) => {
  return (
    <MyComponent id={id} text={text} />
  );
};
```

**反例：**
```tsx
import MyComponent from "./MyComponent";

export const App = (props) => {
  return (
    <MyComponent {...props} />
  );
};
```

---

## 3.3.8【推荐】避免滥用 Fragment

**说明**：参考 eslint-plugin-react 中 `react/jsx-fragments` 规则：尽量避免滥用 Fragment。需要使用 Fragment 的时候，使用 `<></>` 代替；必须使用 Fragment 的场景需要给 Fragment 增加 key 值。

**正例：**
```tsx
<div>hello</div>

<>
  <div>hello</div>
  <div>world</div>
</>

<React.Fragment key={item.id}>
  <div>hello</div>
</React.Fragment>
```

**反例：**
```tsx
<><div>hello</div></>

<div>
  <>
    <div>hello</div>
    <div>world</div>
  </>
</div>

<React.Fragment>
  <div>hello</div>
</React.Fragment>
```

---

## 3.3.9【强制】JSX 的层级数量限制不超过 15 层

**说明**：参考 eslint-plugin-react 中 `react/jsx-max-depth` 规则：一个组件的 JSX 层级数量不要过多，开发团队可根据自身代码水平情况选择合适标准：【高】6 层，【中】10 层，【低】15 层。若层级过多则需要进行组件封装。示例以【高】6 层为标准。

**正例：**
```tsx
const User = () => {
  return (
    <Age>
      <Sex>
        <FirstName>
          <LastName />
        </FirstName>
      </Sex>
    </Age>
  );
};

const MyComponent = () => {
  return (
    <App>
      <Home>
        <Address>
          <User />
        </Address>
      </Home>
    </App>
  );
};
```

**反例：**
```tsx
const MyComponent = () => {
  return (
    <App>
      <Home>
        <Address>
          <Age>
            <Sex>
              <FirstName>
                <LastName />
              </FirstName>
            </Sex>
          </Age>
        </Address>
      </Home>
    </App>
  );
};
```

---

## 3.3.10【强制】dangerouslySetInnerHTML 元素内不能添加 children

**说明**：参考 eslint-plugin-react 中 `react/no-danger-with-children` 规则：当必须要使用 dangerouslySetInnerHTML 的时候，不要在元素内添加 children。

**正例：**
```tsx
<App dangerouslySetInnerHTML={{ __html: "HTML" }} />
```

**反例：**
```tsx
<App dangerouslySetInnerHTML={{ __html: "HTML" }}>
  Children
</App>
```

---

## 3.3.11【强制】不再使用已经废弃的 API

**说明**：随着版本的更新，React 的某些 API 已经不在推荐使用，因此需根据 React 所用版本的不同，在对应版本的项目工程中不要使用已经废弃的 API，替换方案参考附录 6.1。

**已废弃 API：**

1. 从 React 迁移到 ReactDOM、ReactDOMServer 的 api：
```
React.render()
React.unmountComponentAtNode()
React.findDOMNode()
React.renderToString()
React.renderToStaticMarkup()
```
更改为：
```
ReactDOM.render()
ReactDOM.unmountComponentAtNode()
ReactDOM.findDOMNode()
ReactDOMServer.renderToString()
ReactDOMServer.renderToStaticMarkup()
```

2. 16 版本废弃 API，16+ 版本不再使用：
```
React.PropTypes
React.createClass()
React.DOM()
```

3. 17 版本废弃 API，17+ 版本不再使用：
```
componentWillMount() {}
componentWillReceiveProps() {}
componentWillUpdate() {}
```

4. 18 版本废弃 API，18+ 版本不再使用：
```
ReactDOM.render()
ReactDOM.hydrate()
ReactDOM.unmountComponentAtNode()
ReactDOM.renderSubtreeIntoContainer()
ReactDOMServer.renderToNodeStream()
```

---

## 3.3.12【强制】组件的 render 必须有 return

**说明**：参考 eslint-plugin-react 中 `react/require-render-return` 规则：组件的 render 函数必须有 return 值。

**正例：**
```tsx
interface IApp {
  isMobile: boolean;
}

const App = ({ isMobile }: IApp) => {
  if (isMobile) {
    return <div>{"Mobile"}</div>;
  }
  return null;
};
```

**反例：**
```tsx
interface IApp {
  isMobile: boolean;
}

const App = ({ isMobile }: IApp) => {
  if (isMobile) {
    return <div>{"Mobile"}</div>;
  }
};
```

---

## 3.3.13【强制】使用 setState 或者 useState 进行 state 的更新

**说明**：虽然 state 是普通的 JavaScript 对象，但 React 使用 state 来控制组件的渲染输出，所以为了避免渲染异常，开发者不要直接改变 state。

- 在类组件中，开发者须使用 `setState` 来更新 state；
- 在函数式组件中，开发者须使用对应 hooks 的更新方式，如 useState 返回的更新函数进行状态的更新。

React 官方文档对 "正确使用 state" 进行了说明。

**正例：**
```tsx
const App = () => {
  const [count, setCount] = useState(1);
  return (
    <button onClick={() => setCount(count + 1)}>
      {"button"}
    </button>
  );
};

class App extends React.Component {
  state = { count: 1 };
  render() {
    return (
      <button onClick={() => this.setState({ count: this.state.count + 1 })}>
        {"button"}
      </button>
    );
  }
}
```

**反例：**
```tsx
const App = () => {
  const [count] = useState(1);
  return <button onClick={() => count += 1}>{"button"}</button>;
};

class App extends React.Component {
  state = { count: 1 };
  render() {
    return (
      <button onClick={() => { this.state.count = this.state.count + 1; }}>
        {"button"}
      </button>
    );
  }
}
```

---

## 3.3.14【强制】使用含有特定意义的唯一 id 作为组件的 key

**说明**：参考 eslint-plugin-react 中 `react/no-array-index-key` 规则：在 JSX 的数组循环中，必须给每个元素添加一个 key 值，且需要使用含有特定意义的唯一 id 作为组件的 key，而不是数组的索引 index。

**正例：**
```tsx
<div>
  {list.map((item) => {
    return <div key={item.id}>{item.name}</div>;
  })}
</div>
```

**反例：**
```tsx
<div>
  {list.map((item, index) => {
    return <div key={index}>{item.name}</div>;
  })}
</div>
```

---

## 3.3.15【强制】禁止在 render 的时候修改状态

**说明**：React 的 render 应该是纯函数，在 render 里运行 setState 会导致重复渲染，或者死循环，因此禁止在 render 的时候修改状态。

**正例：**
```tsx
const App = () => {
  const [type, setType] = useState('http');

  useEffect(() => {
    if (type === 'http') {
      setType('https');
    }
  }, [type]);

  return <label>{type}</label>;
};

class App extends React.Component {
  get type() {
    const { type } = this.state;
    if (type === 'http') return 'https';
    return type;
  }
  render() {
    const type = this.type;
    return <label>{type}</label>;
  }
}
```

**反例：**
```tsx
const App = () => {
  const [type, setType] = useState('http');

  if (type === 'http') {
    setType('https');
  }

  return <label>{type}</label>;
};

class App extends React.Component {
  render() {
    const { type } = this.state;
    if (type === 'http') {
      this.setState({ type: 'https' });
    }
    return <label>{type}</label>;
  }
}
```

---

## 3.3.16【推荐】建议类组件使用箭头函数来定义事件

**说明**：类组件推荐使用箭头函数定义事件，使用箭头函数会自动绑定当前组件实例，无需手动绑定 this，保持代码简洁。

**正例：**
```tsx
class App extends React.Component {
  handleClick = () => {
    // 其他代码
  };
  render() {
    return <div onClick={this.handleClick}>app</div>;
  }
}
```

**反例：**
```tsx
class App extends React.Component {
  constructor(props) {
    super(props);
    this.handleClick = this.handleClick.bind(this);
  }
  handleClick() {
    // do something
  }
  render() {
    return <div onClick={this.handleClick}>app</div>;
  }
}
```

---

## 3.3.17【推荐】建议布尔属性的命名以 is/has/can/should 等作为前缀

**说明**：建议在定义布尔属性时，使用 is/has/can/should 等表意词作为前缀。

**正例：**
```tsx
const [isOnline, setIsOnline] = useState(false);
```

**反例：**
```tsx
const [online, setOnline] = useState(false);
```

---

## 3.3.18【强制】禁止在 componentDidMount 中同步调用 setState

**说明**：参考 eslint-plugin-react 中 `react/no-did-mount-set-state` 规则：在组件挂载后直接更新状态将触发第二次 render 调用，可能会出现渲染错误问题，通过异步请求获取数据在 componentDidMount 中更新状态不受此限制。

**正例：**
```tsx
class Hello extends React.Component {
  componentDidMount() {
    this.props.onMount();
  }
  render() {
    return <div>{`Hello ${this.props.name}`}</div>;
  }
}
```

**反例：**
```tsx
class Hello extends React.Component {
  componentDidMount() {
    this.setState({
      name: this.props.name.toUpperCase()
    });
  }
  render() {
    return <div>{`Hello ${this.state.name}`}</div>;
  }
}
```

---

## 3.3.19【强制】禁止在 componentDidUpdate 中同步调用 setState

**说明**：参考 eslint-plugin-react 中 `react/no-did-update-set-state` 规则：在组件更新后直接更新状态将触发第二次 render 调用，可能会出现渲染错误问题，通过异步请求获取数据在 componentDidUpdate 中更新状态不受此限制。

**正例：**
```tsx
class Hello extends React.Component {
  componentDidUpdate() {
    this.props.onUpdate();
  }
  render() {
    return <div>{`Hello ${this.props.name}`}</div>;
  }
}
```

**反例：**
```tsx
class Hello extends React.Component {
  componentDidUpdate() {
    this.setState({
      name: this.props.name.toUpperCase()
    });
  }
  render() {
    return <div>{`Hello ${this.state.name}`}</div>;
  }
}
```

---

## 3.3.20【强制】禁止在 componentWillUpdate 中同步调用 setState

**说明**：参考 eslint-plugin-react 中 `react/no-will-update-set-state` 规则：在组件将更新时更新状态将触发第二次 render 调用，可能会出现渲染错误问题，通过异步请求获取数据在 componentWillUpdate 中更新状态不受此限制。

**正例：**
```tsx
class Hello extends React.Component {
  componentWillUpdate() {
    this.props.onUpdate();
  }
  render() {
    return <div>{`Hello ${this.props.name}`}</div>;
  }
}
```

**反例：**
```tsx
class Hello extends React.Component {
  componentWillUpdate() {
    this.setState({
      name: this.props.name.toUpperCase()
    });
  }
  render() {
    return <div>{`Hello ${this.state.name}`}</div>;
  }
}
```

---

## 3.3.21【推荐】不在 PureComponent 中使用 shouldComponentUpdate

**说明**：参考 eslint-plugin-react 中 `react/no-redundant-should-component-update` 规则：PureComponent 组件已经默认实现了 shouldComponentUpdate 方法，用于判断组件是否需要重新渲染，大多数情况下可以不用写 shouldComponentUpdate 方法。

**正例：**
```tsx
class Foo extends React.PureComponent {
  render() {
    return <div>{"Radical"}</div>;
  }
}
```

**反例：**
```tsx
class Foo extends React.PureComponent {
  shouldComponentUpdate() {
    // 其他代码
  }
  render() {
    return <div>{"Radical"}</div>;
  }
}
```

---

## 3.3.22【强制】无状态组件不能使用 this

**说明**：参考 eslint-plugin-react 中 `react/no-this-in-sfc` 规则：在无状态组件中使用 this 可能存在潜在错误。

**正例：**
```tsx
interface IApp {
  id: number;
  text: string;
}

export const App = ({ id, text }: IApp) => {
  return (
    <div id={id}>{text}</div>
  );
};
```

**反例：**
```tsx
interface IApp {
  id: number;
  text: string;
}

export const App = (props: IApp) => {
  return (
    <div id={this.props.id}>{this.props.text}</div>
  );
};
```

---

## 3.3.23【强制】禁止使用字符串作为 ref 属性的值

**说明**：参考 eslint-plugin-react 中 `react/no-string-refs` 规则：采用回调函数、useRef、React.createRef 的方式定义 ref。

**正例：**
```tsx
const App = () => {
  const ref = useRef(null);
  return <div ref={ref}>hello</div>;
};

class App extends React.Component {
  constructor(props) {
    super(props);
    this.ref = React.createRef();
  }
  render() {
    return <div ref={this.ref}>hello</div>;
  }
}

class Hello extends React.Component {
  render() {
    return (
      <div ref={(c) => { this.hello = c; }}>
        {"Hello, world."}
      </div>
    );
  }
}
```

**反例：**
```tsx
class Hello extends React.Component {
  render() {
    return <div ref="hello">{"Hello, world."}</div>;
  }
}
```

---

## 3.3.24【强制】禁止使用未知或不支持的 DOM 属性

**说明**：参考 eslint-plugin-react 中 `react/no-unknown-property` 规则：在 JSX 中，所有 DOM 属性和属性都应该是 camelCased 格式而非全小写编写，禁止使用未知或不支持的 DOM/SVG/ARIA 属性以及已废弃或更改为其他名称的属性。

**正例：**
```tsx
const Hello = () => {
  return (
    <div className="hello">{"Hello World"}</div>
  );
};
```

**反例：**
```tsx
const Hello = () => {
  return (
    <div class="hello">{"Hello World"}</div>
  );
};
```

---

## 3.3.25【强制】style 属性必须为对象

**说明**：参考 eslint-plugin-react 中 `react/style-prop-object` 规则：style 属性应定义为对象。

**正例：**
```tsx
<div style={{ color: "red" }}>{"test"}</div>
```

**反例：**
```tsx
<div style="color: 'red'">{"test"}</div>
```

---

## 3.3.26【推荐】没有 children 的组件使用自闭合标签

**说明**：参考 eslint-plugin-react 中 `react/self-closing-comp` 规则：没有 children 的组件可以自闭，以避免不必要的额外关闭标签。

**正例：**
```tsx
const HelloJohn = () => {
  return <Hello firstName="John" />;
};

const Profile = () => {
  return (
    <HelloJohn>
      <img src="picture.png" />
    </HelloJohn>
  );
};
```

**反例：**
```tsx
const HelloJohn = () => {
  return <Hello firstName="John"></Hello>;
};
```

---

## 3.3.27【推荐】自闭合标签前添加一个空格

**说明**：参考 eslint-plugin-react 中 `react/jsx-tag-spacing` 规则：保证自闭合标签前有一个空格。

**正例：**
```tsx
<Foo />;
```

**反例：**
```tsx
<Foo/>;
<Foo                    />;
```

---

## 3.3.28【推荐】闭合标签与标签对齐

**说明**：参考 eslint-plugin-react 中 `react/jsx-closing-bracket-location`、`react/jsx-closing-tag-location` 规则：保证闭合标签与标签对齐。

**正例：**
```tsx
<Hello firstName="John" lastName="Smith" />;

<Hello
  firstName="John"
  lastName="Smith"
/>;

<Hello>
  hello
</Hello>
```

**反例：**
```tsx
<Hello
  lastName="Smith"
  firstName="John" />;

<Hello
  lastName="Smith"
  firstName="John"
  />;

<Hello>
  hello
  </Hello>
```

---

## 3.3.29【推荐】花括号内使用一致的换行符

**说明**：参考 eslint-plugin-react 中 `react/jsx-curly-newline` 规则：在 jsx 和表达式中花括号中使用一致的换行符。

**正例：**
```ts
const { john, smith } = person;
```

**反例：**
```ts
const {
john, smith} = person;
```

---

## 3.3.30【推荐】表达式等号两边留空格

**说明**：参考 eslint-plugin-react 中 `react/jsx-equals-spacing` 规则：表达式等号两边留空格；jsx 属性等号两边不保留空格。

**正例：**
```tsx
const { john, smith } = person;
<Hello firstName="John" lastName="Smith" />;
```

**反例：**
```tsx
const { john, smith }=person;
<Hello firstName ="John" lastName = "Smith" />;
```

---

## 3.3.31【强制】不能定义重复的属性

**说明**：参考 eslint-plugin-react 中 `react/jsx-no-duplicate-props` 规则：重复定义 props 可能会导致意外行为。

**正例：**
```tsx
<Hello firstName="John" lastName="Doe" />
```

**反例：**
```tsx
<Hello firstName="John" firstName="Doe" />
```

---

## 3.3.32【强制】不允许使用未声明的组件

**说明**：参考 eslint-plugin-react 中 `react/jsx-no-undef` 规则：不使用未声明的组件。

**正例：**
```tsx
const Hello = <div>hello</div>;
<Hello />;
```

**反例：**
```tsx
<Hello />;
```

---

## 3.3.33【推荐】多行 JSX 需要使用圆括号括起来

**说明**：参考 eslint-plugin-react 中 `react/jsx-wrap-multilines` 规则：多行 JSX 需要用圆括号 `()` 包裹。

**正例：**
```tsx
const Hello = () => {
  const name = 'john';
  return (
    <>
      <div>hello</div>
      <div>{name}</div>
    </>
  );
};
```

**反例：**
```tsx
const Hello = () => {
  const name = 'john';
  return <>
    <div>hello</div>
    <div>{name}</div>
  </>;
};
```

---

## 3.3.34【推荐】建议 a 标签添加 rel="noreferrer noopener"

**说明**：参考 eslint-plugin-react 中 `react/jsx-no-target-blank` 规则：当在 a 标签上设置 `target="_blank"` 属性时，新打开的页面将获得对原始窗口对象的访问权限，新页面可以操纵原始页面的内容和 URL，存在一定安全风险，建议同时设置 `rel="noopener noreferrer"` 属性，避免风险。

**正例：**
```tsx
<a href="https://example.com" target="_blank" rel="noopener noreferrer">
  External Link
</a>
```

**反例：**
```tsx
<a href="https://example.com" target="_blank">External Link</a>
```

---

## 3.3.35【强制】`<html>` 标签必须设置 lang 属性

**说明**：参考 eslint-plugin-jsx-a11y 中 `html-has-lang` 规则：`<html>` 标签必须设置 lang 属性。

**正例：**
```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Document</title>
  </head>
  <body></body>
</html>
```

**反例：**
```html
<!DOCTYPE html>
<html>
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Document</title>
  </head>
  <body></body>
</html>
```

---

## 3.3.36【强制】iframe 标签必须有 title 属性

**说明**：参考 eslint-plugin-jsx-a11y 中 `iframe-has-title` 规则：iframe 标签必须有 title 属性。

**正例：**
```tsx
<iframe title="myIframe" src="url" />
```

**反例：**
```tsx
<iframe src="url" />
```

---

## 3.3.37【强制】img 标签必须有 alt 属性

**说明**：参考 eslint-plugin-jsx-a11y 中 `alt-text` 规则：img 标签必须有 alt 属性。

**正例：**
```tsx
<img alt="myImage" src="url" />
```

**反例：**
```tsx
<img src="url" />
```

---

## 3.3.38【强制】禁止在循环、条件或嵌套函数中调用 hook

**说明**：参考 eslint-plugin-react-hooks 中 `react-hooks/rules-of-hooks` 规则：在使用 React hooks 的过程中，要遵从只在最顶层使用 hook，不在循环、条件或嵌套函数中调用 hook。

**正例：**
```tsx
interface IHello {
  name: string;
}

const Hello = (props: IHello) => {
  const { name } = props;
  const [isJohn, setIsJohn] = useState(false);
  useEffect(() => {
    setIsJohn(name === 'John');
  }, [name, setIsJohn]);

  return (
    <div>{`Hello ${isJohn ? 'John' : 'someone'}`}</div>
  );
};
```

**反例：**
```tsx
interface IHello {
  name: string;
}

const Hello = (props: IHello) => {
  const { name } = props;
  const [isJohn, setIsJohn] = useState(false);

  if (name) {
    useEffect(() => {
      setIsJohn(name === 'John');
    }, [name, setIsJohn]);
  }

  return (
    <div>{`Hello ${isJohn ? 'John' : 'someone'}`}</div>
  );
};
```

---

## 3.3.39【强制】在 React 的函数组件或自定义 Hook 中调用 Hook

**说明**：参考 eslint-plugin-react-hooks 中 `react-hooks/rules-of-hooks` 规则：在使用 React hooks 的过程中，要遵从只在 React 的函数组件或自定义 Hook 中调用 Hook，不在普通函数中调用 hook。自定义 hook 必须以 use 命名开头。

**正例：**
```tsx
interface IHello {
  name: string;
}

const Hello = (props: IHello) => {
  const { name } = props;
  const [isJohn, setIsJohn] = useState(false);
  useEffect(() => {
    setIsJohn(name === 'John');
  }, [name, setIsJohn]);

  return (
    <div>{`Hello ${isJohn ? 'John' : 'someone'}`}</div>
  );
};
```

**反例：**
```tsx
interface IHello {
  name: string;
}

const Hello = ({ name }: IHello) => {
  const [isJohn, setIsJohn] = useState(false);

  useEffect(() => {
    setIsJohn(name === 'John');
  }, [name, setIsJohn]);

  return isJohn;
};
```

---

## 3.3.40【推荐】补全 hooks 依赖列表

**说明**：参考 eslint-plugin-react-hooks 中 `react-hooks/exhaustive-deps` 规则：补全 hooks 所有依赖项。

**正例：**
```tsx
interface IHello {
  name: string;
}

const Hello = (props: IHello) => {
  const { name } = props;
  const [isJohn, setIsJohn] = useState(false);
  useEffect(() => {
    setIsJohn(name === 'John');
  }, [name, setIsJohn]);

  return (
    <div>{`Hello ${isJohn ? 'John' : 'someone'}`}</div>
  );
};
```

**反例：**
```tsx
interface IHello {
  name: string;
}

const Hello = (props: IHello) => {
  const { name } = props;
  const [isJohn, setIsJohn] = useState(false);
  useEffect(() => {
    setIsJohn(name === 'John');
  }, []);

  return (
    <div>{`Hello ${isJohn ? 'John' : 'someone'}`}</div>
  );
};
```

---

## 3.3.41【推荐】建议使用 useMemo 进行缓存

**说明**：复杂计算可以使用 useMemo 进行缓存，只有当设置的依赖项变更时，才会重新计算，从而提升渲染速度，在开发中应根据实际情况使用，避免滥用。

**正例：**
```tsx
const memoizedValue = useMemo(() => computeExpensiveValue(a, b), [a, b]);
```

**反例：**
```tsx
const expensiveValue = computeExpensiveValue(a, b);
```

---

## 3.3.42【推荐】建议使用 useCallback 进行缓存

**说明**：回调函数可以使用 useCallback 来进行缓存，避免子组件的重复渲染，在开发中应根据实际情况使用，避免滥用。

**正例：**
```tsx
const memoizedCallback = useCallback(() => doSomething(a, b), [a, b]);
```

**反例：**
```tsx
const callback = () => doSomething(a, b);
```

---

## 3.3.43【推荐】可复用的逻辑抽离成自定义 hook

**说明**：组件中可复用的逻辑，可以抽离成一个自定义 hook，方便在多个组件中进行逻辑复用。自定义 hook 必须以 use 命名开头。

**正例：**
```tsx
const useOnline = () => {
  const [isOnline, setIsOnline] = useState(false);

  useEffect(() => {
    fetch('/online')
      .then((res) => res.json())
      .then((res) => {
        setIsOnline(res.online);
      });
  }, [setIsOnline]);

  return isOnline ? 'online' : 'offline';
};

const App = () => {
  const [onlineStatus] = useOnline();
  return (
    <div>{onlineStatus}</div>
  );
};
```

**反例：**
```tsx
const App = () => {
  const [isOnline, setIsOnline] = useState(false);

  useEffect(() => {
    fetch('/online')
      .then((res) => res.json())
      .then((res) => {
        setIsOnline(res.online);
      });
  }, [setIsOnline]);

  return (
    <div>{isOnline ? 'online' : 'offline'}</div>
  );
};
```

---

## 3.3.44【推荐】建议在文件头部增加注释

**说明**：建议在文件头部增加注释，包含：文件说明、作者、日期。

**正例：**
```ts
/**
 * @description xxxxxxxxx
 * @author xxx
 * @date xxxx/xx/xx
 */
```

---

## 3.3.45【推荐】建议给函数增加注释

**说明**：建议在函数顶部增加注释，包含：函数说明、入参、出参。

**正例：**
```ts
/**
 * @description xxxxxx
 * @param xxxx
 * @return xxxx
 */
```
