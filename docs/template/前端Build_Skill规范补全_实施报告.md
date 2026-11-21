# 前端 Build Skill 规范补全实施报告

## 基线与结论

- 日期：2026-11-21。
- 已知事实：开始时在 `dev_frontend_agent`，缺少 Engineering Skill、task packet、execution scope 及相应测试。按实施方案切换到本地 `dev_agent`（`9f102195`，与本地 origin/dev_agent 一致），保留用户未跟踪的实施方案文档；未拉取远端、未提交。
- 历史审计基准：`73a20dbc^`，即 `b8bffd0b`。以下旧文件路径均相对 `Backend/app/builtin_skills/`，结论针对受控 Build，旧文件自身及其他流程不变。
- 已落实规范、复杂示例、任务规则映射与按需提示；没有新增必读文件、模型调用、DAG 节点、文件权限或强阻断校验。
- 验收尚未全部闭环：Python 完整加载回归和后端健康检查受本机环境阻碍，不能宣称通过；详见验证章节。

## 承接位置索引

| 简称 | 当前准确位置 |
| --- | --- |
| E | `Backend/app/builtin_skills/frontend-engineering-standards/SKILL.md`，以下以规则 ID 定位 |
| C | 同一 Skill 的 `references/component-patterns.md` |
| P | 同一 Skill 的 `references/page-structure-examples.md` |
| A | 同一 Skill 的 `references/api-request-patterns.md`（未修改） |
| T | 生成工程 `/frontend/AGENTS.md` 的“工程目录与文件职责”“核心架构与使用入口”“文件职责与受管边界”“权限与访问控制” |
| 权限 | `Backend/app/agents/frontend/task_packet.py` 的 `_business_path/_target_operations` 及受管路径集合；`execution_scope.py` 的 `readable/writable/authorize` |
| 顺序 | `task_packet.py::STEPS`、`generator.py::_scoped_generation_prompt` 及 `execution_scope.py::transition` |

## 历史规则覆盖矩阵

结论“已覆盖”表示基线已有承接，“已补齐”表示本次补充，“不适用”同时给出废弃或分流理由。没有留待解释的缺失项。位置与修改动作均列在表内；同一条包含多个编号时，列明各自承接或取舍。

### 旧模板修改边界

| 原来源/章节或规则 | 适用性 | 结论与承接/修复动作 |
| --- | --- | --- |
| frontend-template-modification-boundary/SKILL.md：虚拟路径前缀 | 现行适用 | 已覆盖：Prompt 明确虚拟绝对路径；权限校验 `virtual_file` 和宿主路径拒绝 |
| 工程根禁止创建文件；骨架/配置/全局样式/公共资源禁止修改 | 现行适用 | 已覆盖：精确 target/change_scope + `_business_path`、`PROTECTED/PROTECTED_DIRS`；T 说明工程归属，不把旧枚举复制成新事实 |
| 项目级验证、安装和临时脚本禁止在 Build Agent 执行 | 现行适用 | 已覆盖：受控 Agent `block_shell`、Prompt 禁止 Shell 与目录遍历；验证由外层执行 |
| TemplateState、路由与权限事实；不得手工注册菜单/资源/Provider | 现行适用 | 已覆盖：任务包注入有效能力与只读权限依赖，Prompt 消费 authorization_actions；T 与原路由投影边界 |
| 只能增量；已有 API/业务能力保留复用 | 现行适用 | 已覆盖：E 定位、FE-API-BOUNDARY、API 任务 Prompt 与既有文件 edit_file 规则；不是禁止修正已有授权业务实现 |
| 页面、API、公共类型/常量/Hook/工具/组件目录 | 现行适用 | 已覆盖：FE-CODE-LAYERING、FE-TYPES、FE-CONSTANTS、FE-NAMING + T；局部类型/常量不再为形式强制拆文件 |
| Static API 与真实 API 两条数据路径 | 分属受控页面/Static 流程 | 已覆盖：页面消费已完成 API；`frontend-static-data-generate/SKILL.md` 与 `static_prompt.py` 继续负责 Static 内存模块；不强加 HTTP |
| 旧 ProTable.request、页面 useEffect 手写请求示例 | 已失效 | 不适用：当前 FE-API-USE-REQUEST / FE-API-UI-DECOUPLING，C 以 useRequest + dataSource 替代 |
| 页面占位必然存在、旧 PageKey/menus.ts、Vite/AntD4 固定事实 | 已失效 | 不适用：以任务合同与 T 的真实目录、Rsbuild、AntD5 为准，不预判文件存在 |
| “所有类型必须公共文件”“API 只能新增”“菜单只能追加”等冲突表述 | 旧限制不再适用 | 不适用：精确任务权限及增量当前合同优先；不从旧文本授予菜单写入或禁止合法修改 |
| 依赖缺失报告、与 React Skill 的关系 | 现行适用 | 已覆盖：Prompt 的失败/change_request 协议及 E 最小实现；通用规范统一由 Engineering 承接，不恢复旧 Skill 必读 |

### 旧代码区块与页面模板

| 原来源/章节或规则 | 适用性 | 结论与承接/修复动作 |
| --- | --- | --- |
| code-block-template/SKILL.md：区块组合、决策树、组件导入 | 现行适用 | 已补齐：E FE-PRO-COMPONENTS，C 各场景；Pro 系列与 antd 导入分开 |
| 同入口：xlsx 导入、无条件补 Mock、多份 Skill 顺序读取 | 依赖/流程条件不成立 | 不适用：模板未声明 xlsx/mockjs；P“导入导出接入条件”、Static 专用入口；只保留两份核心文档 |
| blocks.md 2.1：查询、筛选、分页、工具栏 | 现行适用 | 已补齐：C“基础分页查询”，筛选/重置/改页容量回到首页，错误不展示旧行；禁用无业务查询绑定的内置 reload |
| 2.1：request 驱动查询、永远只能用 defaultPageSize | 已失效 | 不适用：当前受控分页必须传 pageSize + onChange；业务请求由 useRequest 负责，不原样复制旧 request |
| 2.1：rowSelection、批量操作、稳定 rowKey | 现行适用 | 已补齐：FE-SELECTION / FE-LIST-KEY，C 当前行派生提交 ID、禁用、成功清理刷新、失败保留 |
| blocks.md 2.2：ProForm/ModalForm/DrawerForm/StepsForm 选型 | 现行适用 | 已补齐：FE-PRO-COMPONENTS / FE-FORM；C 独立 Modal、Drawer、Steps 完整组件 |
| 2.2：创建/编辑回显、重建、关闭清理、提交校验/按钮 | 现行适用 | 已补齐：C 按打开状态挂载和 key 重建、initialValues、提交锁及异常反馈；成功返回 true 后由表单统一触发关闭回调，避免两次 onClose |
| 2.2：旧 visible/onVisibleChange | 已失效 | 不适用：当前示例使用 open/onOpenChange；保留与已安装 AntD5/Pro2 匹配的 destroyOnClose |
| 2.2：文本/密码/数字/选择/单选/复选/开关字段 | 现行适用 | 已覆盖/补齐：按已确认字段和当前组件类型选控件；FE-TYPES / FE-PRO-COMPONENTS / FE-FORM，C 给出最小字段与联动示例，不逐个复制教程 |
| 2.2：日期、上传、自定义字段及长标签布局 | 现行适用 | 已补齐：C 末段要求按实际组件值类型和契约转换，长标签采用足够宽栅格或垂直布局；P 定义上传状态边界；旧 Moment 类型不迁移 |
| 2.2：ProFormDependency | 现行适用 | 已补齐：C 拒绝原因联动、条件必填、preserve=false + 提交时排除隐藏旧值 |
| 2.2：分步校验/合并值、表单按钮布局 | 现行适用 | 已补齐：C StepsForm 自动阶段校验和最终合并，成功后父级卸载；提交按钮复用组件，不强制所有页面按钮右对齐 |
| blocks.md 2.3：ProList metas 和 ProTable 选择 | 现行适用 | 已补齐：C“ProList / ProCard / Row / Col”，消费父级已查询数据 |
| blocks.md 2.4：24 栅格、gutter、分栏/响应式 | 现行适用 | 已补齐：同 C 章节；不迁移随机色、索引 key 的演示数据 |
| blocks.md 2.5：ProCard 分组/指标/标签容器 | 现行适用 | 已补齐：FE-PRO-COMPONENTS，C 卡片与 Statistic；P 标签状态示例。ProCard 仍为分组首选，基础 Tabs 的选择不改写确认设计 |
| page-templates.md 3.1：字段元信息复用、筛选、批量、详情 | 现行适用 | 已覆盖/补齐：FE-CONSTANTS / FE-TYPES 管共享元信息；P“查询 + 批量操作 + 多弹窗组合”，只读 Drawer 消费已有字段，不强制简单表格建 FIELD_DEFS |
| 3.1：弹窗内聚导入文件/导出列、选择同步与反馈 | 现行适用 | 已补齐：P 弹窗状态可区分，C/FE-SELECTION 避免冗余旧行对象；P“导入导出接入条件”限定范围和资源释放 |
| 3.1：Excel xlsx 完整示例 | 当前未安装依赖 | 不适用：不新增 xlsx；仅在已有合同与依赖下实现导入/导出，不把示例伪装成已有 API |
| page-templates.md 3.2：标签筛选/分页/勾选隔离、动态列与工具栏 | 现行适用 | 已补齐：P 每个 OrderTab 的独立实例与惰性挂载；列/按钮随各实例，不共享 formRef/tableRef。共享筛选必须显式提升并规定切换行为 |
| 3.2：每次切换重置/清选/无条件 reload | 不是通用正确行为 | 不适用：按实际需求决定；示例保留已访问标签状态，不在 onChange 再请求 |
| codegen-strategy.md：通用流程、模板一/二的所有步骤 | 顺序能力现行适用 | 已覆盖：既有四步先理解/布局/接入/只读核验；API 独立任务。字段先行由正式合同保障，不让页面再生成 API，不另建七/十步流程 |
| mock-data.md：基本写法、Random 速查、完整示例、常见错误 | 属于其他流程 | 已覆盖于专用流程：Static Skill 按契约生成模块级记录、类型和异步函数。旧 mockjs 随机值、固定20条与方法教程对当前无依赖受控 Build 不适用；旧 Skill 资源未改 |

### 旧 React 规范

| 原来源/节号 | 适用性 | 结论与承接/修复动作 |
| --- | --- | --- |
| react-develop-specification/SKILL.md：原则、总览、索引、自检 | 通用质量现行适用 | 已覆盖/补齐：E 各规则 + 只读 Step 4；不恢复全部 references 默认读取 |
| 00-framework-intro.md 1、2.1–2.7：组件/JSX/Props/State/生命周期/Context/Effect | 语义现行适用 | 已补齐：FE-TYPES、FE-REACT-STATE、FE-HOOKS、FE-JSX；Context 复用 T 已有能力。通用入门教程无需迁移 |
| 同文件 2.8 CSR、2.9 SSR | 非当前任务职责 | 不适用：当前模板为客户端工程，不由页面任务选择渲染架构或创建 SSR |
| 01-naming 语言、3.2.1–3.2.5 | 现行适用 | 已覆盖/补齐：FE-TYPES / FE-NAMING，类型 PascalCase、JSX .tsx、语义命名；index.tsx 遵循 T，不强制全工程改名 |
| 01-naming 3.2.6 样式作用域 | 现行适用 | 已补齐：FE-PRO-COMPONENTS 局部作用域与主题；不硬装 Less/CSS Module/BEM/classnames/CSS-in-JS，不给受管样式扩权 |
| 02-coding 3.3.1 导入、.2 any | 现行适用 | 已覆盖/补齐：FE-NAMING 顶部导入、FE-TYPES 类型与 unknown |
| 3.3.3 引号/模板串，.26–.30 自闭合空格/对齐/换行/等号，.33 JSX 括号 | 风格规则 | 不作为新硬规则：沿用既有文件风格/实际格式化配置；不声称未安装的 ESLint/Prettier 已保障，不复制无关格式教程 |
| 3.3.4 children、.5 解构、.6 可选值默认、.7 spread | 语义部分现行适用 | 已覆盖/补齐：FE-TYPES 保证可选输入安全、FE-JSX 避免业务 Props 透传 DOM；嵌套 children/解构是可选可读性写法，不保留“children prop 一律非法”等过度约束，不凭默认值改业务契约 |
| 3.3.8 Fragment、.9 JSX 深度 | 现行适用 | 已补齐：FE-JSX 避免无意义 Fragment/深层结构，FE-CODE-LAYERING 按职责拆分；15层等固定阈值不是当前工具合同 |
| 3.3.10 HTML children | 现行适用 | 已覆盖：FE-RENDER-SECURITY 禁止 dangerouslySetInnerHTML 同时带 children |
| 3.3.11 废弃 API | 现行适用 | 已补齐：FE-JSX 使用当前 React API，不迁移历史版本迁移教程 |
| 3.3.12 return | 现行适用 | 已补齐：FE-JSX 显式返回 JSX/null |
| 3.3.13 state Setter、.15 render 纯净 | 现行适用 | 已覆盖/补齐：FE-REACT-STATE 不可变、派生值与禁止 render 副作用 |
| 3.3.14 key | 现行适用 | 已覆盖：FE-LIST-KEY，C/P 稳定业务 ID |
| 3.3.16 类事件 this、.18–.20 类生命周期 setState、.21 PureComponent、.22 无状态组件 this | 类专属方式不适用于新增函数组件 | 不迁移类教程：FE-HOOKS / FE-REACT-STATE 管函数组件同步与纯净，FE-JSX 不新增废弃生命周期；不把类规则套成禁止所有 Effect 更新 |
| 3.3.17 布尔命名 | 现行适用 | 已补齐：FE-NAMING 语义清晰，不硬性要求所有布尔值相同前缀 |
| 3.3.23 字符串 ref | 现行适用 | 已补齐：FE-JSX 当前 ref API |
| 3.3.24 DOM 属性、.25 style、.31 重复 Props、.32 未声明组件 | 现行适用 | 已补齐：FE-JSX 明确约束，TS 检查仅负责其实际可检查部分 |
| 3.3.34 外链 rel、.36 iframe title、.37 img alt | 现行适用 | 已补齐：FE-JSX 按场景约束及装饰图片空 alt |
| 3.3.35 html lang | 属于模板根文件 | 已覆盖于受管模板：base/index.html 有 lang；业务任务不修改根 HTML。FE-JSX 与 T 保持边界 |
| 3.3.38–.40 Hook 顶层/范围/依赖 | 现行适用 | 已覆盖/补齐：FE-HOOKS 含实际依赖及资源清理 |
| 3.3.41 useMemo、.42 useCallback、.43 自定义 Hook | 按需适用 | 已补齐：FE-HOOKS 仅有实际收益/复用才使用；废弃“简单列表必须抽 Hook”及冗余请求状态示例 |
| 3.3.44 文件头、.45 函数注释 | 目的说明现行适用 | 已补齐：FE-NAMING 中文函数说明和复杂决策注释；不强制作者/日期模板注释 |
| 03-security 3.4.1 富文本净化、.2 DOM 注入 | 现行适用 | 已覆盖：FE-RENDER-SECURITY；只复用已有净化机制，不盲加多个依赖 |
| 03-security 3.4.3 href、.4 src | 现行适用，旧正例有误 | 已覆盖：FE-RENDER-SECURITY 校验来源/协议。旧 href checkUrl 在正则不通过时返回原始恶意输入、通过时返回 javascript:，不能复制；旧未锚定正则也不能作为可信校验 |
| 04-engineering 4.1、4.2.1/4.2.2 版本与组件/Hooks typing | 现行类型能力适用 | 已覆盖/补齐：FE-TYPES / FE-HOOKS / FE-JSX；版本以 T/package 为准，不强迫使用类泛型、any 生命周期或把可派生状态复制进 Effect |
| 4.3.1 路由、.2 状态库、.3 组件库、.4 Axios/工具/Hook/图表/动画/日期/懒加载 | 复用原则适用；新增库不适用 | 已覆盖：E 最小实现、FE-API-BOUNDARY / FE-PRO-COMPONENTS / FE-HOOKS；T 管现有路由与懒加载，不新增 Redux/ECharts/动画/SSR 懒加载库，不把 axios/fetch 下放页面 |
| 4.4.1–4.4.7 构建/格式/代码/样式/提交/测试/调试工具 | 模板/外层流程职责 | 不迁移工具链安装教程：按 T 实际 package scripts；Build Agent 无 Shell，不假定 ESLint/Stylelint/Husky/Storybook 等已启用 |
| 05-project-example 全部工程目录与 webpack/Babel 构建示例 | 旧工程事实失效 | 不适用：T 当前真实树与 FE-CODE-LAYERING 承接职责，不复制 BuildScript/mock/旧目录 |
| 06-appendix 6.1 版本、6.2 tsconfig、6.3 过时 API、6.4 SSR | 当前 API 约束适用；历史配置/SSR 不适用 | FE-JSX 与 T 承接当前 API；不改受管 tsconfig，不复制过时 ReactDOM.render 迁移建议，不添加 SSR 或全局对象兼容层 |

## 实际文件改动与保留

| 文件 | 必要变更 |
| --- | --- |
| E | 复用现有规则补 Props、Effect 清理、样式主题、组件选择；仅新增 FE-JSX、FE-FORM、FE-SELECTION 三个职责明确的规则 ID |
| C | 保留 useRequest + dataSource + 受控分页结构；修复失败旧结果错配；补筛选、批量、Modal/Drawer/Steps、联动与卡片栅格；为示例明确最小 API 契约 |
| P | 替换 requestQuery 重复比对 Hook；提供独立标签、多弹窗组合、导入导出接入条件 |
| task_packet.py | API 要求5条通用规则，页面要求17条适用规则；不向 API 强加渲染/表单/选择规则，不增加验收 rule IDs |
| generator.py | 复杂页面按对应参考章节补读；不默认全读、不新增 Skill 子任务或业务权限 |
| test_frontend_skill_loading_prompt.py | 扩展 Prompt 断言；规则存在/职责/引用路径核对；示例请求边界与旧 API 防回退 |
| test_frontend_core_docs.py | 增加真实工具读取局部示例、核心文档去重及越权读取拒绝用例 |
| docs/CODEBASE_INDEX.md | 更新入口、规则与参考职责，链接本报告 |
| 本报告 | 保存历史覆盖矩阵及证据边界；不加入 REQUIRED_BUILTIN_SKILL_FILES 或 Prompt |

A 未改：service 的 returnCode/body 封装及泛型、GET params、路径编码条件均与模板实际一致。agent.py / execution_scope.py 未改：白名单、核心文档去重与按范围读取已具备所需能力。未改任何旧 Skill、DAG、后端 Agent、权限核心、路由投影、模板仓库或产品 API。

## 加载与非受控流程核对

- 已知源码事实：受控 Build 的内置白名单仍仅 Engineering；核心文档仍为 Engineering + /frontend/AGENTS.md。页面单独 begin 进入 Step 1 后读取，API 按任务级激活，无页面四步工具。成功读取标记与串行工具去重不变，最多每任务每份一次；既有跨任务上下文复用不变。
- references 仍非 required_docs，不注入正文；本次只新增场景/章节提示，局部 read_file 沿原只读挂载处理，不扩大业务源码权限。
- Static 的 static_prompt.py、frontend-static-data-generate 入口与无受控 scope 的 Agent 全 Skill 挂载不变。
- 自由修改 direct_modification.py 仍保留旧三份入口；UI 设计 services/ui_design_generator.py 仍使用 antd-ui-design；扫描 agents/code_analyze/analyzer.py 仍使用 frontend-code-scan；预览 services/preview_runtime_repair.py 仍保留 frontend-pnpm-install-repair 指令。
- 以上是源码与差异核对结论；完整真实 Agent 工具回归因环境导入失败而未通过，不能把静态不变推断成全部端到端已验证。

## 模板与示例校验

只读检查本机 springboot-template 的 dev 分支源码，未修改其已有 package.json 用户改动。base 声明 React18、AntD5、Pro2、ahooks3；既有 workspace/AGENTS.md 仍是旧稿，因此通过当前 assembly/agents-document.mjs 的 assembleAgents 在临时目录组装最终 AGENTS.md，核对实际目录、统一 service、权限组件和受管边界；没有把装配机制写入 Engineering 或最终工程文案。

示例类型检查使用本机已安装依赖：React 18.2.0、AntD 5.29.3、ProComponents 2.8.10、ahooks 3.10.0、TypeScript 5.3.2。模板实际 service.ts 与 constants/index.ts 仅复制到临时校验目录，无重新实现响应封装。安装树与 package 声明并非全部精确锁定一致，结果只证明这套已安装依赖，不代表所有未来版本。

ahooks 官方 v3.8.1 Fetch 源码和本机3.10实现均在 run 时设置 params/loading，成功更新 data/error，失败保留旧 data，并自行处理并发结果。这不要求以 params/run 代替页面查询 State。按用户手动优化后的示例，查询条件/分页使用页面 State + refreshDeps，加载可保留列表并显示 loading，失败用 Result 替换；关闭无请求绑定的 ProTable 内置 reload，不新增 query token/cache。参考：[ahooks 官方源码](https://github.com/alibaba/hooks/blob/v3.8.1/packages/hooks/src/useRequest/src/Fetch.ts)。

## 验证记录

### 手动优化示例对齐回检（2026-11-21）

- 将用户手动优化的 `component-patterns.md` 中 `src/pages/Orders/index.tsx` 作为基准，改前/改后 SHA256 一致，代码块原样保留。
- 修正外围文字和示例 API 模块，统一使用 `@/apis/order`、`PageParamsType/OrderListType` 与 current 分页字段；这些是本组示例合同，不据此改变其他正式 API 的分页命名。
- 多标签页和多弹窗示例同步改为页面查询 State + useRequest refreshDeps，失败 Result、提交 Spin/按钮禁用/事件守卫；不使用 fieldset，不要求加载时清空旧数据，也不强制从 useRequest.params 派生 UI 查询状态。
- Engineering 的查询规则与代码索引同步说明：查询 State 是必要交互状态，不是重复维护 loading/error/data。
- 最新样例行为回归首轮5项通过，剩余查询失败用例因测试查找“重试”未匹配 AntD 自动插入的中文空格而失败；测试选择器改为匹配空白后，该用例单独通过（7.374秒）。合计6个场景均在当前提取示例上通过，ProTable 仍是 Props 探针，不代表完整真实表格 UI 或模型生成验收。
- 当前临时目录为 `/private/tmp/xcodeagent-frontend-skill-review`，测试命令与上一轮相同但目录改为此处；单例复验追加 `--testNamePattern 'query failure'`。未将旧目录的测试结果冒充手动优化后的结果。

### Ant Design 4 语法回检（2026-11-21）

- 本仓库桌面实际安装 AntD 4.24.16；生成模板声明 AntD ^5.22.6、当前安装5.29.3。两者不可混称同一个工程技术栈。
- 本机4.24.16的 Modal/Drawer 类型已声明 open，Tabs 类型支持 items；Result、Spin、Descriptions.Item 可用。ModalForm/DrawerForm/StepsForm 的接口属于 ProComponents，须同时检查其安装版本，不能仅以 open 判定为 AntD5语法。
- 所有当前示例提取后分别运行 TypeScript 检查，两套均通过：`tsc --project /private/tmp/xcodeagent-frontend-skill-review/tsconfig.antd4.json` 和 `tsc --project /private/tmp/xcodeagent-frontend-skill-review/tsconfig.antd5.json`。执行器为模板 workspace/node_modules/.bin/tsc。
- AntD4检查显式解析桌面工程的 antd、ProComponents 和 React 类型；首次临时配置混入两份 @types/react 产生 JSX/Key 类型冲突，统一 paths/typeRoots 后通过，未修改产品依赖或以 any 掩盖错误。
- 结论仅覆盖 AntD4.24.16和上述AntD5安装树的类型兼容，不扩展为所有4.x版本或全部运行交互兼容。
- 本次跟进只修改 Markdown 规范、示例、索引和报告，未修改产品运行代码；未重复前端构建、后端健康及 Python 运行回归。Electron UI/明暗主题仍未进行人工验收，不以文档示例的单测代替。

### 静态映射与 Python 语法

- `git diff --check`：通过。
- `Backend/.venv/bin/python -m py_compile Backend/app/agents/frontend/generator.py Backend/app/agents/frontend/task_packet.py Backend/tests/test_frontend_skill_loading_prompt.py Backend/tests/test_frontend_core_docs.py`：通过。
- 两个新增纯静态断言：使用标准库 AST 提取生产 _standard_rules 和测试方法执行，2/2 通过；API 5条、页面17条均存在且适用，参考路径及示例边界有效。这是有限静态检查，不替代导入整套后端模块。

### Python 加载机制回归

- `Backend/.venv/bin/python -m pytest Backend/tests/test_frontend_skill_loading_prompt.py -q`：失败，No module named pytest；系统 python3 同样缺少 pytest。
- 临时目录安装 pytest：沙箱网络解析失败；沙箱外安装申请被用户拒绝，未修改项目依赖。
- `PYTHONPATH=Backend Backend/.venv/bin/python -m unittest Backend.tests.test_frontend_skill_loading_prompt -q`：导入失败。
- `PYTHONPATH=Backend Backend/.venv/bin/python -m unittest tests.test_frontend_core_docs tests.test_frontend_task_execution_scope tests.test_frontend_build_execution tests.test_frontend_api_execution tests.test_frontend_project_plan_context -q`：5个模块均导入失败。
- 共同阻碍：当前 deepagents 0.6.12 的 deepagents.backends.protocol 没有仓库 runtime_snapshot.py 导入的 DeleteResult。未新增兼容别名或修改环境绕过。新增真实工具回归尚不能宣称通过。

### TypeScript 与前端构建

- `pnpm build`（Frontend）：通过，包含 Node/Web TypeScript 与 Electron 主进程、preload、renderer 生产构建；仅有既有 AG-UI 同时静态/动态导入提示。
- `/Users/zhangrongrong/Documents/workspace/springboot-template/template-source/code/frontend/workspace/node_modules/.bin/tsc --project /private/tmp/xcodeagent-frontend-skill-check/tsconfig.json`：通过，所有新增可执行代码块作为实际 TS/TSX 文件检查。
- 临时样例 CJS 编译用于行为回归：相同 tsc 命令追加 `--noEmit false --outDir /private/tmp/xcodeagent-frontend-skill-check/compiled --module commonjs --moduleResolution node`，通过。
- 临时目录 `/private/tmp/xcodeagent-frontend-skill-check` 保存提取示例与校验 harness；不加入产品源码或运行时加载路径。

### 示例交互行为

运行命令：`node /private/tmp/xcodeagent-frontend-skill-check/node_modules/jest/bin/jest.js --config /private/tmp/xcodeagent-frontend-skill-check/jest.config.cjs --runInBand --silent`。

手动优化前的示例曾 6/6 通过（34.959秒）；该结果不代表用户修改后及本次对齐后的示例已验证。测试通过脚本化业务 API、实际 React/useRequest/Tabs/ModalForm 验证：失败隐藏旧行/筛选复位、批量有效 ID/清选刷新、并发结果顺序、标签切回不重查、表单失败保留/成功刷新关闭、编辑后重开新增清理。ProTable 替换为 Props 探针，不能据此声称真实表格布局、所有控件或完整模型生成行为已验证。DrawerForm/StepsForm 的属性与类型已编译核对，未宣称其全部运行交互经过验证。

首次回归发现 ModalForm 成功自动关闭与显式 onClose 重复调用，已修复示例；不是单纯放宽断言。表单使用真实组件的测试超出默认5秒，临时 harness 设30秒上限后复验。

### 后端健康与验证限制

- `curl -sS http://127.0.0.1:8000/health` 无响应；带 `--max-time 10` 的沙箱内/外请求均超时（exit 28），未通过。
- 已知事实：8000 由 Python 监听，进程快照中重载子进程退出；直接导入后端测试有 DeleteResult 导入错误。合理推断：依赖导入失败阻止服务正常运行；未读取到该进程原终端异常日志，不能断言这就是唯一健康问题。
- 未修改无关依赖/兼容实现、终止用户后端或重启已有服务。
- 未运行真实模型生成：没有为规范补全增加额外模型调用。权限/Static 只做源码不变核对；React/JSX 安全以规范与静态检查核对，未声称覆盖所有模型输出。
- 未进行 Electron UI 和明暗主题人工验收：本次不修改桌面 UI，文档示例也不是已接入产品页面；没有用浏览器 Vite 页替代 Electron 测试。

## 反向检验与更稳妥的判断

方案成立之处：核心规范补齐能消除明确遗漏，按需例子承接复杂交互而不把全部旧 Skill 注入每个任务；真实库行为验证也确实发现并修复了重复关闭回调。

限制与反例：规范写入和规则 ID 存在不能保证模型总会遵守；模板未来升级、缓存/防抖/loadingDelay 配置、跨页选择或保留历史结果需求均可能要求不同实现。不能把临时 Props 探针、编译成功或源码无改动当作完整代码生成验收。

建议结论：实现补全已落盘，维持现有加载与权限边界；须先恢复后端依赖与健康，再运行完整 Python 加载回归，之后才能确认实施方案的运行验收全部完成。
