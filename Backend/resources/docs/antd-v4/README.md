# Ant Design v4.24.16 离线组件知识库

这是给无法联网的大模型使用的离线知识库。内容基于 Ant Design 官方 `antd@4.24.16` 组件文档整理，只保留组件使用、API、注意事项和正式示例代码。

重要约束：本文档只代表 Ant Design v4。生成代码时不要混用 v5/v6 的 token、主题 API、组件 API 或示例写法。

## 文件结构

- `components/*.md`：每个组件一个文档，包含离线事实、API、FAQ、示例代码。
- `manifest.json`：组件索引，适合程序读取。
- `chunks.jsonl`：按“组件文档”和“单个示例”拆分的检索语料。

## 统计

- antd 版本：`4.24.16`
- 组件文档：64
- 正式示例：598
- JSONL chunks：662

## 使用建议

- 查 API：优先读 `components/<slug>.md` 的“使用文档”。
- 写代码：优先读同一文件的“示例代码”。
- 做 RAG：优先导入 `chunks.jsonl`，每行都是独立可信 chunk。
- 整目录入库时，不要同时导入 `chunks.jsonl` 和 `components/*.md`，二者内容重复；RAG 场景优先选 `chunks.jsonl`。
- 遇到导入方式冲突时，以文档顶部“推荐导入”为准。

## 组件索引

| slug | 组件 | 导入名 | demos | 本地文档 |
| --- | --- | --- | ---: | --- |
| affix | Affix / 固钉 | `Affix` | 3 | [文档](components/affix.md) |
| alert | Alert / 警告提示 | `Alert` | 11 | [文档](components/alert.md) |
| anchor | Anchor / 锚点 | `Anchor` | 6 | [文档](components/anchor.md) |
| auto-complete | AutoComplete / 自动完成 | `AutoComplete` | 7 | [文档](components/auto-complete.md) |
| avatar | Avatar / 头像 | `Avatar` | 6 | [文档](components/avatar.md) |
| back-top | BackTop / 回到顶部 | `BackTop` | 2 | [文档](components/back-top.md) |
| badge | Badge / 徽标数 | `Badge` | 11 | [文档](components/badge.md) |
| breadcrumb | Breadcrumb / 面包屑 | `Breadcrumb` | 6 | [文档](components/breadcrumb.md) |
| button | Button / 按钮 | `Button` | 9 | [文档](components/button.md) |
| calendar | Calendar / 日历 | `Calendar` | 5 | [文档](components/calendar.md) |
| card | Card / 卡片 | `Card` | 10 | [文档](components/card.md) |
| carousel | Carousel / 走马灯 | `Carousel` | 4 | [文档](components/carousel.md) |
| cascader | Cascader / 级联选择 | `Cascader` | 16 | [文档](components/cascader.md) |
| checkbox | Checkbox / 多选框 | `Checkbox` | 6 | [文档](components/checkbox.md) |
| collapse | Collapse / 折叠面板 | `Collapse` | 9 | [文档](components/collapse.md) |
| comment | Comment / 评论 | `Comment` | 4 | [文档](components/comment.md) |
| config-provider | ConfigProvider / 全局化配置 | `ConfigProvider` | 4 | [文档](components/config-provider.md) |
| date-picker | DatePicker / 日期选择框 | `DatePicker` | 15 | [文档](components/date-picker.md) |
| descriptions | Descriptions / 描述列表 | `Descriptions` | 6 | [文档](components/descriptions.md) |
| divider | Divider / 分割线 | `Divider` | 4 | [文档](components/divider.md) |
| drawer | Drawer / 抽屉 | `Drawer` | 8 | [文档](components/drawer.md) |
| dropdown | Dropdown / 下拉菜单 | `Dropdown` | 15 | [文档](components/dropdown.md) |
| empty | Empty / 空状态 | `Empty` | 5 | [文档](components/empty.md) |
| form | Form / 表单 | `Form` | 28 | [文档](components/form.md) |
| grid | Grid / 栅格 | `Row / Col` | 12 | [文档](components/grid.md) |
| icon | Icon / 图标 | `@ant-design/icons` | 5 | [文档](components/icon.md) |
| image | Image / 图片 | `Image` | 7 | [文档](components/image.md) |
| input | Input / 输入框 | `Input` | 17 | [文档](components/input.md) |
| input-number | InputNumber / 数字输入框 | `InputNumber` | 11 | [文档](components/input-number.md) |
| layout | Layout / 布局 | `Layout` | 9 | [文档](components/layout.md) |
| list | List / 列表 | `List` | 8 | [文档](components/list.md) |
| mentions | Mentions / 提及 | `Mentions` | 8 | [文档](components/mentions.md) |
| menu | Menu / 导航菜单 | `Menu` | 9 | [文档](components/menu.md) |
| message | Message / 全局提示 | `message` | 8 | [文档](components/message.md) |
| modal | Modal / 对话框 | `Modal` | 13 | [文档](components/modal.md) |
| notification | Notification / 通知提醒框 | `notification` | 9 | [文档](components/notification.md) |
| page-header | PageHeader / 页头 | `PageHeader` | 6 | [文档](components/page-header.md) |
| pagination | Pagination / 分页 | `Pagination` | 10 | [文档](components/pagination.md) |
| popconfirm | Popconfirm / 气泡确认框 | `Popconfirm` | 7 | [文档](components/popconfirm.md) |
| popover | Popover / 气泡卡片 | `Popover` | 6 | [文档](components/popover.md) |
| progress | Progress / 进度条 | `Progress` | 12 | [文档](components/progress.md) |
| radio | Radio / 单选框 | `Radio` | 9 | [文档](components/radio.md) |
| rate | Rate / 评分 | `Rate` | 7 | [文档](components/rate.md) |
| result | Result / 结果 | `Result` | 8 | [文档](components/result.md) |
| segmented | Segmented / 分段控制器 | `Segmented` | 9 | [文档](components/segmented.md) |
| select | Select / 选择器 | `Select` | 21 | [文档](components/select.md) |
| skeleton | Skeleton / 骨架屏 | `Skeleton` | 6 | [文档](components/skeleton.md) |
| slider | Slider / 滑动输入条 | `Slider` | 10 | [文档](components/slider.md) |
| space | Space / 间距 | `Space` | 10 | [文档](components/space.md) |
| spin | Spin / 加载中 | `Spin` | 7 | [文档](components/spin.md) |
| statistic | Statistic / 统计数值 | `Statistic` | 4 | [文档](components/statistic.md) |
| steps | Steps / 步骤条 | `Steps` | 14 | [文档](components/steps.md) |
| switch | Switch / 开关 | `Switch` | 5 | [文档](components/switch.md) |
| table | Table / 表格 | `Table` | 35 | [文档](components/table.md) |
| tabs | Tabs / 标签页 | `Tabs` | 15 | [文档](components/tabs.md) |
| tag | Tag / 标签 | `Tag` | 7 | [文档](components/tag.md) |
| time-picker | TimePicker / 时间选择框 | `TimePicker` | 11 | [文档](components/time-picker.md) |
| timeline | Timeline / 时间轴 | `Timeline` | 7 | [文档](components/timeline.md) |
| tooltip | Tooltip / 文字提示 | `Tooltip` | 4 | [文档](components/tooltip.md) |
| transfer | Transfer / 穿梭框 | `Transfer` | 9 | [文档](components/transfer.md) |
| tree | Tree / 树形控件 | `Tree` | 10 | [文档](components/tree.md) |
| tree-select | TreeSelect / 树选择 | `TreeSelect` | 8 | [文档](components/tree-select.md) |
| typography | Typography / 排版 | `Typography` | 7 | [文档](components/typography.md) |
| upload | Upload / 上传 | `Upload` | 18 | [文档](components/upload.md) |
