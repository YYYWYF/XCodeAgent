# 需求描述编辑器

公共组件只处理前端文本编辑，不依赖应用草稿、FormInstance、后端接口或持久化服务。

| Props | 类型 | 默认值 / 用途 |
| --- | --- | --- |
| value | string | 空字符串；唯一正式内容来源 |
| onChange | (value: string) => void | 回填手动输入、模板、页面及确认后的放大编辑结果 |
| disabled | boolean | false；禁用输入并隐藏辅助操作 |
| readOnly | boolean | false；只读查看、复制和滚动，隐藏全部辅助操作 |
| placeholder | string | 常量文件中的完整填写指引 |
| rows | number | 18；主输入框初始行数 |
| id | string | 由 Form.Item 透传，用于标签与输入框关联 |

```tsx
const [description, setDescription] = useState('')

<RequirementDescriptionEditor value={description} onChange={setDescription} />
<RequirementDescriptionEditor value={description} readOnly />
```

创建表单通过原有字段绑定，无需手动拼装值或调用表单方法：

```tsx
<Form.Item
  name="senario"
  label="应用需求描述"
  rules={[{ required: true, whitespace: true, message: '请输入应用需求描述' }]}
>
  <RequirementDescriptionEditor readOnly={mode === 'settings'} rows={18} />
</Form.Item>
```

模板和详细指引位于 `../../constants/applicationRequirements.ts`。非空输入插入模板时必须选择追加或替换；取消不改变值。追加页面保留已有文本和换行。放大编辑持有临时草稿，确认后才通知父层，取消或关闭则丢弃；不提供跨应用或跨重启草稿持久化。

编辑器使用整体边框包裹输入及按钮，下方操作与输入框保持紧凑间距。“填写指引”通过鼠标悬停或键盘聚焦展示可滚动的指引卡片；“插入需求模板”和“添加页面”通过悬浮提示解释操作行为。

样式优先继承宿主 `--modal-*` 弹窗变量，设置页等工作台场景继承 `--wb-*` 变量；独立使用时提供紫色主题的默认暗色及 `xa-theme-light` 祖先类对应的亮色配色（类名前缀由 `config/style.json` 统一控制）。只读模式仅保留外层边框，不显示内层输入框边框和缩放手柄；设置页不对只读需求显示必填标记或执行必填校验。

从 `Frontend` 执行 `pnpm test:requirement-editor`。组件行为测试使用真实 React 状态与 Ant Design Form，仅替换依赖 DOM 布局的浮层和输入控件；另有真实 ApplicationForm 静态渲染检查。测试不替代 Electron 中的焦点、滚动、浮层布局和明暗主题验收。
