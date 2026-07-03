---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "result"
component: "Result"
component_code_name: "Result"
recommended_import: "import { Result } from 'antd';"
subtitle_zh: "结果"
type_zh: "反馈"
demo_count: 8
---

# Result 结果

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Result`
- 推荐导入：`import { Result } from 'antd';`
- 组件分类：反馈
- 简述：用于反馈一系列操作任务的处理结果。

## 示例索引

- Success（success）
- Info（info）
- Warning（warning）
- 403（403）
- 404（404）
- 500（500）
- Error（error）
- 自定义 icon（customIcon）

## 使用文档

用于反馈一系列操作任务的处理结果。

### 何时使用

当有重要操作需告知用户处理结果，且反馈内容较为复杂时使用。

### API

| 参数 | 说明 | 类型 | 默认值 |
| --- | --- | --- | --- |
| extra | 操作区 | ReactNode | - |
| icon | 自定义 icon | ReactNode | - |
| status | 结果的状态，决定图标和颜色 | `success` \| `error` \| `info` \| `warning` \| `404` \| `403` \| `500` | `info` |
| subTitle | subTitle 文字 | ReactNode | - |
| title | title 文字 | ReactNode | - |

## 示例代码

### Success

- demo: `success`

成功的结果。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    status="success"
    title="Successfully Purchased Cloud Server ECS!"
    subTitle="Order number: 2017182818828182881 Cloud server configuration takes 1-5 minutes, please wait."
    extra={[
      <Button type="primary" key="console">
        Go Console
      </Button>,
      <Button key="buy">Buy Again</Button>,
    ]}
  />
);

export default App;
```

### Info

- demo: `info`

展示处理结果。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    title="Your operation has been executed"
    extra={
      <Button type="primary" key="console">
        Go Console
      </Button>
    }
  />
);

export default App;
```

### Warning

- demo: `warning`

警告类型的结果。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    status="warning"
    title="There are some problems with your operation."
    extra={
      <Button type="primary" key="console">
        Go Console
      </Button>
    }
  />
);

export default App;
```

### 403

- demo: `403`

你没有此页面的访问权限。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    status="403"
    title="403"
    subTitle="Sorry, you are not authorized to access this page."
    extra={<Button type="primary">Back Home</Button>}
  />
);

export default App;
```

### 404

- demo: `404`

此页面未找到。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    status="404"
    title="404"
    subTitle="Sorry, the page you visited does not exist."
    extra={<Button type="primary">Back Home</Button>}
  />
);

export default App;
```

### 500

- demo: `500`

服务器发生了错误。

```tsx
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    status="500"
    title="500"
    subTitle="Sorry, something went wrong."
    extra={<Button type="primary">Back Home</Button>}
  />
);

export default App;
```

### Error

- demo: `error`

复杂的错误反馈。

```tsx
import { CloseCircleOutlined } from '@ant-design/icons';
import { Button, Result, Typography } from 'antd';
import React from 'react';

const { Paragraph, Text } = Typography;

const App: React.FC = () => (
  <Result
    status="error"
    title="Submission Failed"
    subTitle="Please check and modify the following information before resubmitting."
    extra={[
      <Button type="primary" key="console">
        Go Console
      </Button>,
      <Button key="buy">Buy Again</Button>,
    ]}
  >
    <div className="desc">
      <Paragraph>
        <Text
          strong
          style={{
            fontSize: 16,
          }}
        >
          The content you submitted has the following error:
        </Text>
      </Paragraph>
      <Paragraph>
        <CloseCircleOutlined className="site-result-demo-error-icon" /> Your account has been
        frozen. <a>Thaw immediately &gt;</a>
      </Paragraph>
      <Paragraph>
        <CloseCircleOutlined className="site-result-demo-error-icon" /> Your account is not yet
        eligible to apply. <a>Apply Unlock &gt;</a>
      </Paragraph>
    </div>
  </Result>
);

export default App;
```

```css
.site-result-demo-error-icon {
  color: red;
}
```

### 自定义 icon

- demo: `customIcon`

自定义 icon。

```tsx
import { SmileOutlined } from '@ant-design/icons';
import { Button, Result } from 'antd';
import React from 'react';

const App: React.FC = () => (
  <Result
    icon={<SmileOutlined />}
    title="Great, we have done all the operations!"
    extra={<Button type="primary">Next</Button>}
  />
);

export default App;
```
