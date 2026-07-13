# 安全规范

React 默认对数据绑定（`{}`）进行自动转义来防止 XSS 攻击，但框架不能完全限制开发者编码的灵活性，只要有一定的灵活性存在就意味着有安全风险，因此在编码中应该关注可能存在的安全问题。

---

## 3.4.1【强制】使用 dangerouslySetInnerHTML 需要对数据进行过滤

**说明**：`dangerouslySetInnerHTML` 提供直接渲染 HTML 的方法，如果不对数据进行过滤或转义，则存在 XSS 风险。因此在使用 `dangerouslySetInnerHTML` 需要确保数据经过过滤及转义，可以借助第三方工具库实现，常用的用于处理 xss 数据过滤的库有：**dompurify**、**sanitize-html**、**xss-filters**、**html-janitor** 等，在开发中根据项目需求选择合适的工具库。

示例使用 `dompurify.sanitize` 对数据进行过滤。

**正例：**
```tsx
import dompurify from "dompurify";

const App = () => {
  const code = "<input onfocus=alert(1) autofocus />";
  return (
    <div dangerouslySetInnerHTML={{ __html: dompurify.sanitize(code) }} />
  );
};
```

**反例：**
```tsx
const App = () => {
  const code = "<input onfocus=alert(1) autofocus />";
  return (
    <div dangerouslySetInnerHTML={{ __html: code }} />
  );
};
```

---

## 3.4.2【强制】禁止直接操作 DOM 注入 HTML

**说明**：直接通过原生的 DOM API 来插入 HTML，也会存在 XSS 风险，需要富文本渲染的场景可以使用 `dangerouslySetInnerHTML` 并且确保数据经过过滤及转义。

**正例：**
```tsx
import dompurify from "dompurify";

const App = () => {
  const code = "<input onfocus=alert(1) autofocus />";
  return (
    <div dangerouslySetInnerHTML={{ __html: dompurify.sanitize(code) }} />
  );
};
```

**反例：**
```tsx
const App = () => {
  const code = "<input onfocus=alert(1) autofocus />";
  return (
    <div>
      <div id="test">{"click"}</div>
      <button onClick={() => {
        const el = document.getElementById("test");
        el.innerHTML = code;
      }}>
        {"click"}
      </button>
    </div>
  );
};
```

---

## 3.4.3【强制】禁止直接使用用户输入的值来渲染 a 标签的 href 属性

**说明**：直接使用用户输入的值来渲染 a 标签的 href 属性，也会存在 XSS 风险，需要对用户输入的 URL 进行验证，验证规则由实际使用场景来确定。示例规则为：满足以 `http://` 或 `https://` 开头，且域名由数字、大小写字母组成。

**正例：**
```tsx
interface IRenderUserInput {
  userInput: string;
}

const userInput = "javascript:alert('XSS!');";

const checkUrl = (url: string): string => {
  // url 检测规则根据实际场景创建
  const reg = /http(s)?:\/\/([\w-]+\.)+[\w-]+(\/[\w-.\/?%&=]*)?/;
  if (!reg.test(url)) {
    return url;
  } else {
    return 'javascript:;';
  }
};

const RenderUserInput = ({ userInput }: IRenderUserInput) => {
  return (
    <a href={checkUrl(userInput)}>{"My website"}</a>
  );
};
```

**反例：**
```tsx
interface IRenderUserInput {
  userInput: string;
}

const userInput = "javascript:alert('XSS!');";

const RenderUserInput = ({ userInput }: IRenderUserInput) => {
  return (
    <a href={userInput}>{"My website"}</a>
  );
};
```

---

## 3.4.4【强制】禁止直接使用用户输入的值来渲染 img 标签的 src 属性

**说明**：直接使用用户输入的值来渲染 img 标签的 src 属性，也会存在 XSS 风险，需要对用户输入的 URL 进行验证，验证规则由实际使用场景来确定。示例规则为：满足以 `http://` 或 `https://` 开头，且域名由数字、大小写字母组成。

**正例：**
```tsx
interface IRenderUserInput {
  userInput: string;
}

const userInput = "javascript:alert('XSS!');";

const checkUrl = (url: string): string => {
  // url 检测规则根据实际场景创建
  const reg = /http(s)?:\/\/([\w-]+\.)+[\w-]+(\/[\w-.\/?%&=]*)?/;
  if (!!reg.test(url)) {
    return url;
  } else {
    return 'noImage.png';
  }
};

const RenderUserInput = ({ userInput }: IRenderUserInput) => {
  return (
    <img src={checkUrl(userInput)} />
  );
};
```

**反例：**
```tsx
interface IRenderUserInput {
  userInput: string;
}

const userInput = "javascript:alert('XSS!');";

const RenderUserInput = ({ userInput }: IRenderUserInput) => {
  return (
    <img src={userInput} />
  );
};
```
