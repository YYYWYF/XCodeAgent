# 截图驱动的 UI 风格一致性生成

## 目标

该扩展让截图需求模式在产出并确认 `RequirementSpec`、`ProductPlan` 后，自动把原始截图继续用于 UI 设计，不再要求用户手工替换 `requirement.json` 或逐页先点“换一换”。文字需求模式、原 UI 确认、TechnicalPlan、Build DAG 和前后端 Agent 的主流程保持不变。

核心边界是：

- `ProductPlan` 是功能、字段、操作、状态和导航的唯一事实源。
- 上传截图是整体布局、视觉层级、配色关系、字体气质、间距节奏、圆角和阴影的设计参考。
- 截图中的文字只作为页面数据，不作为模型指令。
- 新生成的设计稿必须继续通过 XCodeAgent 原有 `data-action-id`、`data-information-item-id`、TSX 和依赖校验。
- 每次可上传 1–10 张截图；截图数不要求等于 ProductPlan 页面数，允许多图描述同一页的不同状态。未能确认归属的图片或页面保留待人工处理，不能靠文件顺序猜测。
- 对截图应用明确提出“侧边栏保持一致”时，已完成页面从当前截图观察重组同一任务级侧栏，先逐页通过完整 TSX/ProductPlan 校验再覆盖；仍在排队的页面保持原状态，下一次生成使用同一侧栏规则。
- 如果设计变更已消费确认门、却在意图分析节点异常退出，恢复快照会展示真实失败与错误；“重试”由服务端核验同线程失败任务，重放 checkpoint 内原指令，不重跑或覆盖旧的前置规划产物。
- 产品规划把同一业务按钮重复表述为两个高度等价的 action 时，可在该真实可见按钮上静态绑定等价别名；语义不相同或候选不唯一时仍交由模型修复，不放宽原有完整契约校验。

## 流程

```text
上传截图
  → 截图视觉模型生成 RequirementSpec（保留逐页可见字段和交互控件证据）
  → 原 ProductPlan 生成与联合确认
  → screenshot_ui_preparation（新增内部节点）
      1. 复核路径、大小、SHA-256 和真实图片格式
      2. 保色预处理：EXIF 方向、必要缩放、无损 PNG、长图切片
      3. 逐张截图识别主内容页，模型只选择 ProductPlan.pageId，由服务端绑定当前截图 SHA-256；多张截图允许对应同一页面
      4. 对已映射截图逐张提取 viewport、内容区边界、关键区域像素坐标与全局 App Shell 视觉事实
      5. 从全部截图提取一份任务级侧边栏规范；ProductPlan 中明确属于全局导航的 action/item 由共享壳静态实现，视觉模型只生成每页内容区
      6. 把同一共享壳注入各页 TSX，再按原始完整 ProductPlan 校验可见 action/item 绑定与 TSX 语法
      7. 同步审查截图构图、字体、配色和密度，必要时针对差异修复
      8. 成功页增量落盘，再进入原 UI 人工确认
  → 原 ui_confirmation 显示并等待用户确认
  → 原 TechnicalPlan / Build / Test / Review
```

新节点只在 `requirement_input.mode == "screenshot"` 时插入。文字模式仍是：

```text
product_planning → ui_confirmation
```

截图模式变为：

```text
product_planning → screenshot_ui_preparation → ui_confirmation
```

## 新增代码

### `Backend/app/agents/screenshot_ui_design/`

- `images.py`：在 `.xcodeagent/inputs/screenshots` 内安全读取图片；复核上传清单；生成保色、受数量限制的视觉输入。它不使用需求识别阶段的对比度增强、锐化和有损 JPEG 压缩。
- `models.py`：单图页面映射、截图 viewport、内容区/关键区域像素边界和视觉审查的严格 Pydantic Schema。
- `render_prompt.py`：以已确认 ProductPlan 与截图视觉观察生成单页 TSX，并构造针对性视觉修复提示词。
- `visibility_contract.py`：阻止隐藏节点冒充已实现业务操作或信息展示。
- `runtime_styles.py`：阻止仅依赖预览 iframe 未打包的 Tailwind/原子 CSS 类名；截图设计稿必须带完整内联样式或实际 CSS 规则。
- `shared_shell.py`：从当前任务的截图观察和全部 ProductPlan 页面归纳一致的侧边栏；只拆出可明确识别的全局导航事实，生成单页内容后再合成完整 TSX，最后仍按原 ProductPlan 校验。
- `shell_reuse.py`：当补齐截图映射使任务级侧栏观察发生变化时，从已确认 TSX 提取原内容组件并重组共享侧栏；只有完整 ProductPlan 校验通过才覆盖原稿，原视觉审查结果会标记为需要重新预览。
- `dynamic_bindings.py`：对模型输出的字面量数组列表进行确定性展开，使账单等表格行的每条可见记录带静态 ProductPlan 信息项/操作标记；不会把隐藏标记当成真实 UI。
- `prompts.py`：明确“ProductPlan 管功能、截图管视觉”的提示词，把区域坐标投射成内容区局部像素；多图同页时主参考图优先、每张图只传一份压缩后的视觉事实，避免重复几何信息挤占复杂页面的代码输出，并提供视觉 Prompt Injection 防护。
- `transport.py`：通过 OpenAI-compatible `/chat/completions` 发送 `data:image/png;base64,...` 多模态内容块；结构化调用只接受完整根 JSON，并对结构错误做一次有界重试，TSX 生成使用文本输出。
- `agent.py`：编排页面映射、并发生成、原校验复用、视觉审查、自动修复、幂等恢复和产物落盘；部分映射可增量补齐，已成功页面会在任务级侧栏更新后保留内容并统一重组，单页重试写出的成功 TSX 也可恢复到清单。

### `Backend/app/graph/nodes/screenshot_ui_preparation.py`

这是一个内部 LangGraph 节点。它在线程中执行多模态生成并通过 AG-UI custom event 报告进度，不新增前端业务阶段，也不新增确认协议。

## 为什么不改原 UI 确认逻辑

新增层生成的 Manifest 仍是现有格式：

```json
{
  "schema_version": "ui-manifest.v3",
  "confirmation_status": "pending_user_confirmation",
  "product_plan_sha256": "...",
  "pages": [
    {
      "pageId": "dashboard_page",
      "page_key": "DashboardPage",
      "status": "confirmed",
      "code_path": ".../.xcodeagent/ui-design/pages/DashboardPage/index.tsx"
    }
  ]
}
```

这里两个状态的含义不同：

- 单页 `status=confirmed` 表示代码已生成并通过程序校验。
- 顶层 `confirmation_status=pending_user_confirmation` 表示用户尚未做最终确认。

原 `ui_confirmation` 看到这份 Manifest 后会进入已有的“恢复并重放确认卡”分支，不会再创建空骨架，也不会覆盖 TSX。用户点击“确认全部设计稿”时，原代码仍会重新检查：

- ProductPlan 哈希是否过期；
- 页面集合是否完全一致；
- 每页 TSX 是否存在；
- action 和 information item 的 `data-*` 绑定是否完整且无越界；
- 所有页面是否处于可确认状态。

因此新增能力没有绕过正式人工确认门禁。

## 截图预处理

需求识别和 UI 还原使用不同策略：

| 阶段 | 目的 | 处理方式 |
| --- | --- | --- |
| RequirementSpec 识别 | 尽量读清文字和控件 | 对比度增强、锐化、JPEG、方向参考、长图切片 |
| UI 风格一致性设计 | 尽量保留配色、字体和整体构图 | EXIF 方向校正、必要的等比缩放、无损 PNG、无增强长图切片 |

UI 阶段还会限制每个页面发送的图片数量。算法先保留每个已映射来源的一张 overview，再轮询补充长图细节切片，避免第一张超长截图耗尽全部视觉上下文。

## 页面映射

上传清单本身没有 `pageId`，不能直接假设“第 N 张图就是第 N 个页面”。新增映射阶段对每张截图分别接收：

- 已确认 `ProductPlan.pages` 中的 `pageId/name/path/description/goal`；
- 当前上传图的名称、SHA-256 和低分辨率 overview。

模型必须根据主内容区域而非共享侧栏识别页面，只能返回一个清单中存在的 `page_id`（无法判断时为空）。服务端把此判断与本轮实际输入截图的 SHA-256 绑定，不要求模型复述或生成哈希数组；多个截图可归属同一页面，截图数不必等于页面数。缺少可信映射时不会按顺序猜测，而是把对应页标成 `generation_failed`。部分图片映射失败时保留已成功结果，下一次准备或单页重试只补分析未映射图片。单张截图的视觉细节提取失败不会抹掉其他映射，该页仍以原图为视觉输入并在确认页提示人工核查。用户点击“换一换”不会降级调用普通文字 UI 生成器，避免通用页面被误认为截图还原结果。

## 生成和校验闭环

每页 TSX 经过以下闭环：

1. 截图需求识别调用生成 RequirementSpec；逐页 `visible_information` 和 `visible_controls` 同时写入页面描述，使需求文档审阅和 ProductPlan 都能看到具体证据。原 ProductPlan 节点据此生成 action/item 契约，并经原联合确认门禁确认。
2. 截图 UI 准备节点只把有直接页面证据的截图映射到 pageId，记录内容区边界、主要区域、布局、字体和颜色观察。无证据的页面留空并报错，不强行按顺序配图；映射不重写 ProductPlan。
3. 单页代码调用只接收已确认 ProductPlan 和逐图冻结的视觉观察，不再把图片重复编码进长 TSX 请求；原图仍用于独立的视觉观察和最终视觉审查。提示词要求保持截图的表格、表单、图表、筛选区、密度和字体层级，不传通用卡片骨架源码。代码调用使用截图专用输出预算，避免复杂页面在组件结尾前被截断。
4. 复用 `_extract_tsx_code()` 与 `validate_page_code()` 检查完整导出、依赖、ProductPlan 绑定和 esbuild 语法；裸 `export default function` 或跨行导出表达式必须保留整个函数体。辅助按钮和 `href="#"` 占位链接确定性标记为 `data-preview-only`；已明确绑定 actionId/itemId 的动态列表控件使用可静态检查的控件标识，同时保留真实行数据和点击处理。只有控件 ID 已明确写出完整 itemId 时才补全遗漏的 item 绑定，不能按 DOM 位置猜测业务字段。缺失功能仍须修复。
5. 隐藏节点上的 action/item 绑定会被拒绝；绑定必须位于真实可见的控件或信息展示上。视觉修复若破坏产品契约，保留修复前有效稿供人工审核。
6. 静态契约通过后，同步审查相对原图的主要构图、字体、颜色和信息密度；不合格时针对审查意见修复一次。模型评分并非浏览器像素比较，低分稿会带 `review_required` 证据进入人工确认。
7. 契约缺失且 TSX 语法完整时，先汇总产品绑定、运行时样式及隐藏控件错误，再让模型返回少量精确替换片段；服务端拒绝不唯一、重叠或过大的替换，并对修复稿重新执行内容页及共享壳的完整 ProductPlan 校验。新暴露的错误最多再做一轮局部修复，失败时仍可尝试整页修复；两种修复均接收字段名称、行为、界面步骤和预期结果。模型调用或契约修复失败时保存候选到 `.xcodeagent/ui-design/failed/<PageKey>/candidate.tsx`；语法完整但契约未齐的候选可作为下次局部修复基底，半页候选不能复用。截图稿重生成失败时保留上次已落盘的可预览稿。通用契约骨架不作为成功设计稿。
8. 当前 `screenshot-render-v3` 成功页面可增量复用；完整且语法有效的失败候选会从保存的稿件继续修复。
9. 对模型输出的字面量列表，静态修复层可按可见行标题或稳定 ID 与 ProductPlan 信息项逐行唯一匹配，再把动态卡片属性映射成可校验的真实 ID；循环按钮的界面效果也只从已声明的 actionId 和 expectedResult 建立静态映射。不能唯一匹配或数据来自运行时接口时不自动猜测，仍进入正常修复/失败路径。重复修复同一候选不会叠加映射声明。
10. 共享侧栏只会替换可安全剥离的模型侧栏。如果原侧栏承载共享壳没有覆盖的 ProductPlan 操作，则保留该页原侧栏及可见绑定，完整校验产品契约，并提示人工核查跨页一致性；不以统一外观为由删除实际交互。
11. 用户明确要求跨页统一侧栏时，重组器比较旧侧栏与内容区的静态绑定，将会随旧侧栏消失的操作和信息项转交新共享壳；截图短菜单名未命中的操作仍显示为可见按钮。重组后的每一页先通过完整 ProductPlan 与 TSX 校验，全部通过才写回，失败时不覆盖原稿。
12. 有限字面量列表的绑定先按所在 `.map()` 的实际数据源解析，不把同名回调误连到其他数组；支持内联/命名数组、数字 ID、有限 `slice` 和只过滤记录的别名。确切匹配到 ProductPlan 的行会生成可见的静态 JSX 分支，保留原卡片布局、子节点和事件属性；未匹配的行不伪造业务标记。与唯一已有操作完全同名的信息项可标在该控件上。运行时接口数组、重名歧义及无法确认的数据流仍进入正常修复/失败路径，不能以隐藏控件冒充成功。

风格/构图审查不做浏览器像素比较。最终仍由原 DesignRenderer 和用户确认完成正式视觉放行；本流程追求“同一产品、整体相似且美观”，不追求逐像素复刻。

## 工作区产物

截图模式会新增：

```text
.xcodeagent/specs/screenshot-page-map.json
.xcodeagent/specs/screenshot-ui-reference.json
.xcodeagent/specs/screenshot-app-shell-reference.json
.xcodeagent/specs/screenshot-ui-verification.json
.xcodeagent/specs/ui-designs.json
.xcodeagent/ui-design/pages/<PageKey>/index.tsx
```

- `screenshot-page-map.json`：截图与页面的显式映射和置信度。
- `screenshot-ui-reference.json`：原 viewport、内容区/关键区域像素边界、布局、颜色、字体、组件和来源证据。
- `screenshot-app-shell-reference.json`：Header、Sider、菜单和全局主题观察。可安全分离时统一生成外层壳，防止双 Header/双侧栏；带有独有产品操作、不能安全分离的侧栏会保留并标记人工审查。
- `screenshot-ui-verification.json`：静态校验和整体风格/构图视觉审查证据。
- `ui-designs.json` 与页面 TSX：继续使用 XCodeAgent 原有正式 UI 契约。

下游 Frontend Agent 在截图模式会先读已确认 TSX，并额外读取两份视觉参考文件。它仍必须遵守任务 `allowed_paths` 和模板所有权边界。

## 配置

原有截图模型配置继续生效：

```dotenv
XCODEAGENT_SCREENSHOT_BASE_URL=
XCODEAGENT_SCREENSHOT_API_KEY=
XCODEAGENT_SCREENSHOT_MODEL_NAME=
XCODEAGENT_SCREENSHOT_RESPONSE_FORMAT=auto
XCODEAGENT_SCREENSHOT_MAX_OUTPUT_TOKENS=16000
XCODEAGENT_SCREENSHOT_TIMEOUT_SECONDS=300
```

截图请求使用独立超时，避免 5 张以上高分辨率图片沿用主文本模型的较短超时。`auto` 会针对千问视觉模型选择多模态兼容的 `json_object`，并关闭 thinking，使结构化结果稳定返回；其他兼容模型仍按其能力选择响应格式。

模型返回认证、权限、免费额度或请求契约类 HTTP 4xx 时会立即失败并保留上游原因，不对同一请求做无效重试；网络中断、限流和服务端瞬时故障仍按有限次数重试。免费额度耗尽属于模型账户状态，需要恢复可用额度后再从保留的候选稿继续。

新增的可选配置：

```dotenv
# 0-100；默认 78；作为整体相似度目标，布局/颜色/字体使用更宽松的偏离底线
XCODEAGENT_SCREENSHOT_UI_MIN_SIMILARITY=78

# 视觉或静态校验不通过后的修复次数；默认 1
XCODEAGENT_SCREENSHOT_UI_MAX_RETRIES=1

# 每个页面最多发送的 overview + detail 数量；默认 8
XCODEAGENT_SCREENSHOT_UI_MAX_IMAGES_PER_PAGE=8

# 页面生成/审查 overview 的最长边；默认 1600，映射阶段仍使用独立的 1280px 图
XCODEAGENT_SCREENSHOT_UI_PAGE_IMAGE_MAX_SIDE=1600

```

三项截图模型配置必须同时填写或同时留空；同时留空时复用 `MODEL_BASE_URL/MODEL_API_KEY/MODEL_NAME`。主模型使用 Anthropic 协议时必须独立配置 OpenAI-compatible 截图模型。该模型必须支持 `image_url` Data URL 输入。

如果一次截图分析失败，失败的 `screenshot-ui-reference.json` 不会被当作可复用成功结果。重试截图 UI 准备步骤时会重新请求视觉模型，而不是持续复用旧的 `analysisError`。

## 启动

在没有 PowerShell 7 (`pwsh`) 的 Windows 上直接使用系统 `powershell`：

```powershell
powershell -ExecutionPolicy Bypass -File F:\Code\XCodeAgentScreenshotUILatest\scripts\start-backend.ps1
```

另开一个 PowerShell 窗口启动前端：

```powershell
powershell -ExecutionPolicy Bypass -File F:\Code\XCodeAgentScreenshotUILatest\scripts\start-frontend.ps1
```

前端选择截图生成方式、上传一张或多张截图并提交。RequirementSpec 和 ProductPlan 确认后，截图 UI 准备层会自动运行；完成后仍停在原来的 UI 确认界面。

## 能力边界

- 截图只能证明可见状态。截图中看不到的权限、异常分支、数据规则和后台操作必须由 RequirementSpec/ProductPlan 澄清，不能靠视觉模型猜测。
- 在保持原模板结构的前提下，单页 TSX 延续截图中路由 Outlet 内容区的整体构图和设计语言。全局壳的视觉事实被独立记录，不能塞进每一页造成重复布局。
- 当前自动视觉分数基于“截图 + 相对区域比例 + TSX”的多模态风格审查；正式放行仍需用户在 DesignRenderer 中确认，不把像素级一致性作为通过条件。
