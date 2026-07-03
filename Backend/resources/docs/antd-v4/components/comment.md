---
library: "antd"
major_version: 4
version: "4.24.16"
component_slug: "comment"
component: "Comment"
component_code_name: "Comment"
recommended_import: "import { Comment } from 'antd';"
subtitle_zh: "评论"
type_zh: "数据展示"
demo_count: 4
---

# Comment 评论

## 离线事实

- 适用版本：`antd@4.24.16`（v4）
- 文档定位：离线知识库；不要假设 v5/v6 API 可用。
- 组件名/导入对象：`Comment`
- 推荐导入：`import { Comment } from 'antd';`
- 组件分类：数据展示
- 简述：对网站内容的反馈、评价和讨论。

## 示例索引

- 基本评论（basic）
- 配合 List 组件（list）
- 嵌套评论（nested）
- 回复框（editor）

## 使用文档

对网站内容的反馈、评价和讨论。

### 何时使用

评论组件可用于对事物的讨论，例如页面、博客文章、问题等等。

### API

| 参数 | 说明 | 类型 | 默认值 | 版本 |
| --- | --- | --- | --- | --- |
| actions | 在评论内容下面呈现的操作项列表 | Array&lt;ReactNode> | - |  |
| author | 要显示为注释作者的元素 | ReactNode | - |  |
| avatar | 要显示为评论头像的元素 - 通常是 antd Avatar 或者 src | ReactNode | - |  |
| children | 嵌套注释应作为注释的子项提供 | ReactNode | - |  |
| content | 评论的主要内容 | ReactNode | - |  |
| datetime | 展示时间描述 | ReactNode | - |  |

## 示例代码

### 基本评论

- demo: `basic`

一个基本的评论组件，带有作者、头像、时间和操作。

```tsx
import { DislikeFilled, DislikeOutlined, LikeFilled, LikeOutlined } from '@ant-design/icons';
import { Avatar, Comment, Tooltip } from 'antd';
import React, { createElement, useState } from 'react';

const App: React.FC = () => {
  const [likes, setLikes] = useState(0);
  const [dislikes, setDislikes] = useState(0);
  const [action, setAction] = useState<string | null>(null);

  const like = () => {
    setLikes(1);
    setDislikes(0);
    setAction('liked');
  };

  const dislike = () => {
    setLikes(0);
    setDislikes(1);
    setAction('disliked');
  };

  const actions = [
    <Tooltip key="comment-basic-like" title="Like">
      <span onClick={like}>
        {createElement(action === 'liked' ? LikeFilled : LikeOutlined)}
        <span className="comment-action">{likes}</span>
      </span>
    </Tooltip>,
    <Tooltip key="comment-basic-dislike" title="Dislike">
      <span onClick={dislike}>
        {React.createElement(action === 'disliked' ? DislikeFilled : DislikeOutlined)}
        <span className="comment-action">{dislikes}</span>
      </span>
    </Tooltip>,
    <span key="comment-basic-reply-to">Reply to</span>,
  ];

  return (
    <Comment
      actions={actions}
      author={<a>Han Solo</a>}
      avatar={<Avatar src="https://joeschmoe.io/api/v1/random" alt="Han Solo" />}
      content={
        <p>
          We supply a series of design principles, practical patterns and high quality design
          resources (Sketch and Axure), to help people create their product prototypes beautifully
          and efficiently.
        </p>
      }
      datetime={
        <Tooltip title="2016-11-22 11:22:33">
          <span>8 hours ago</span>
        </Tooltip>
      }
    />
  );
};

export default App;
```

```css
/* tile uploaded pictures */
.comment-action {
  padding-left: 8px;
  cursor: 'auto';
}

[class*='-col-rtl'] .comment-action {
  padding-right: 8px;
  padding-left: 0;
}
```

### 配合 List 组件

- demo: `list`

配合 List 组件展现评论列表。

```tsx
import { Comment, List, Tooltip } from 'antd';
import React from 'react';

const data = [
  {
    actions: [<span key="comment-list-reply-to-0">Reply to</span>],
    author: 'Han Solo',
    avatar: 'https://joeschmoe.io/api/v1/random',
    content: (
      <p>
        We supply a series of design principles, practical patterns and high quality design
        resources (Sketch and Axure), to help people create their product prototypes beautifully and
        efficiently.
      </p>
    ),
    datetime: (
      <Tooltip title="2016-11-22 11:22:33">
        <span>8 hours ago</span>
      </Tooltip>
    ),
  },
  {
    actions: [<span key="comment-list-reply-to-0">Reply to</span>],
    author: 'Han Solo',
    avatar: 'https://joeschmoe.io/api/v1/random',
    content: (
      <p>
        We supply a series of design principles, practical patterns and high quality design
        resources (Sketch and Axure), to help people create their product prototypes beautifully and
        efficiently.
      </p>
    ),
    datetime: (
      <Tooltip title="2016-11-22 10:22:33">
        <span>9 hours ago</span>
      </Tooltip>
    ),
  },
];

const App: React.FC = () => (
  <List
    className="comment-list"
    header={`${data.length} replies`}
    itemLayout="horizontal"
    dataSource={data}
    renderItem={item => (
      <li>
        <Comment
          actions={item.actions}
          author={item.author}
          avatar={item.avatar}
          content={item.content}
          datetime={item.datetime}
        />
      </li>
    )}
  />
);

export default App;
```

### 嵌套评论

- demo: `nested`

评论可以嵌套。

```tsx
import { Avatar, Comment } from 'antd';
import React from 'react';

const ExampleComment: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <Comment
    actions={[<span key="comment-nested-reply-to">Reply to</span>]}
    author={<a>Han Solo</a>}
    avatar={<Avatar src="https://joeschmoe.io/api/v1/random" alt="Han Solo" />}
    content={
      <p>
        We supply a series of design principles, practical patterns and high quality design
        resources (Sketch and Axure).
      </p>
    }
  >
    {children}
  </Comment>
);

const App: React.FC = () => (
  <ExampleComment>
    <ExampleComment>
      <ExampleComment />
      <ExampleComment />
    </ExampleComment>
  </ExampleComment>
);

export default App;
```

### 回复框

- demo: `editor`

评论编辑器组件提供了相同样式的封装以支持自定义评论编辑器。

```tsx
import { Avatar, Button, Comment, Form, Input, List } from 'antd';
import moment from 'moment';
import React, { useState } from 'react';

const { TextArea } = Input;

interface CommentItem {
  author: string;
  avatar: string;
  content: React.ReactNode;
  datetime: string;
}

interface EditorProps {
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onSubmit: () => void;
  submitting: boolean;
  value: string;
}

const CommentList = ({ comments }: { comments: CommentItem[] }) => (
  <List
    dataSource={comments}
    header={`${comments.length} ${comments.length > 1 ? 'replies' : 'reply'}`}
    itemLayout="horizontal"
    renderItem={props => <Comment {...props} />}
  />
);

const Editor = ({ onChange, onSubmit, submitting, value }: EditorProps) => (
  <>
    <Form.Item>
      <TextArea rows={4} onChange={onChange} value={value} />
    </Form.Item>
    <Form.Item>
      <Button htmlType="submit" loading={submitting} onClick={onSubmit} type="primary">
        Add Comment
      </Button>
    </Form.Item>
  </>
);

const App: React.FC = () => {
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [value, setValue] = useState('');

  const handleSubmit = () => {
    if (!value) return;

    setSubmitting(true);

    setTimeout(() => {
      setSubmitting(false);
      setValue('');
      setComments([
        ...comments,
        {
          author: 'Han Solo',
          avatar: 'https://joeschmoe.io/api/v1/random',
          content: <p>{value}</p>,
          datetime: moment('2016-11-22').fromNow(),
        },
      ]);
    }, 1000);
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
  };

  return (
    <>
      {comments.length > 0 && <CommentList comments={comments} />}
      <Comment
        avatar={<Avatar src="https://joeschmoe.io/api/v1/random" alt="Han Solo" />}
        content={
          <Editor
            onChange={handleChange}
            onSubmit={handleSubmit}
            submitting={submitting}
            value={value}
          />
        }
      />
    </>
  );
};

export default App;
```
