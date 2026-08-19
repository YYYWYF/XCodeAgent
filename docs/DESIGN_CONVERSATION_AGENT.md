# 设计阶段底部输入与原规划 Graph 回接

## 目标

工作台处于产品设计阶段时，用户可以解锁底部输入框并用自然语言修改已有设计产物。输入先由 Design Conversation Agent 识别最早受影响的正式节点，然后在原 `application_planning_workflow`、原 thread 和原 checkpoint 中回到该节点。

正式链路始终是：

```text
design_intent_analysis
  ├─ requirements -> product_planning -> ui_confirmation -> technical_planning
  ├─ product_planning -> ui_confirmation -> technical_planning
  ├─ ui_confirmation -> technical_planning
  └─ design_chat_response -> END
```

意图节点只负责路由，不复制 RequirementSpec、ProductPlan、UiDesign 或 TechnicalPlan 的生成逻辑。

## 运行边界

- AG-UI endpoint：`POST /application-page-planning/run`
- workflow scope：`application_planning`
- Graph：`Backend/app/graph/application_planning_workflow.py`
- 意图 Agent：`Backend/app/agents/design_conversation/router.py`
- 请求标记：`forwardedProps.designChangeSubmission=true`
- 实时状态：修订链路的 started/updated 快照持续投影 `design_change_submission`、`design_change_request`、`design_change_target`、`design_change_reason` 和本轮开始前冻结的 `design_change_existing_artifacts`；前端只有在当前阶段原本已有产物时才展示“重新生成”，否则仍展示“生成”
- threadId：继续使用原创建规划 threadId
- resumeState：携带当前 `planningWorkflow`，只恢复当前契约允许的规划产物和设计变更上下文

只有解锁后的底部自由输入可以发送 `designChangeSubmission=true` 并进入 `design_intent_analysis`。UI 设计稿确认卡中的 `select_template`、`regenerate`、`adjust_pages` 和 `skip` 都是结构化 `ui_design_action`，必须直接恢复 `ui_confirmation`，不得调用意图 Agent；当请求同时出现两种标记时，以 `ui_design_action` 为权威入口。

底部自由输入与当前待确认阶段严格解耦：即使当前 Graph 正停在 RequirementSpec、ProductPlan、UiDesign 或 TechnicalPlan 的澄清/确认状态，自由输入也不能按当前 `clarification.mode` 拼装阶段答案，必须先进入 `design_intent_analysis`。当前阶段的澄清、确认和 UI 单页动作只由对应结构化卡片提交。

不得为设计变更创建第二个 AG-UI endpoint、session、thread 或产物 Graph。原规划 Workflow 是设计阶段唯一权威状态源。

## 路由规则

| target | 修改事实 | 真实执行入口 |
| --- | --- | --- |
| `requirements` | 产品目标、范围、角色、模块、页面清单、业务流程、业务信息需求 | `nodes.requirements` |
| `product_planning` | 固定页面集合内的页面目标、操作、跳转、可见结果、状态、产品验收标准 | `nodes.product_planning` |
| `ui_confirmation` | 布局、视觉层级、样式、控件呈现、响应式、明暗主题、本地交互表现 | `nodes.ui_confirmation` |
| `chat` | 不需要改变正式产物 | `design_chat_response` |

一条输入同时跨越多层时选择最早节点：`requirements > product_planning > ui_confirmation`。RequirementSpec 未确认时，任何正式修改都先进入 `requirements`；ProductPlan 未确认时，UI 修改先进入 `product_planning`。

## 增量更新与确认

`design_change_request` 保存用户原始输入。每个真实节点第一次进入时读取该原始输入，并基于现有产物生成增量候选；该节点进入确认轮次后读取当前确认答案，不能重复套用原始变更。

- RequirementSpec：把现有 spec 与最新反馈交给原需求分析节点；最新反馈只作为增量补丁，未提及事实和稳定 ID 必须保留，摘要必须描述合并后的完整需求，不能退化为本轮输入。
- ProductPlan：把现有 plan、已确认 RequirementSpec 与原始变更交给原产品规划节点；只修改反馈或新 RequirementSpec 实际影响的字段，其余页面目标、信息、操作、状态、验收标准和摘要保持不变。
- UiDesign：页面集合不变时转换为 `adjust_pages`；页面集合变化时由原 UI 节点重建当前页面集合。
- TechnicalPlan：上游重新确认后，基于新的 ProductPlan 和 UiDesign 增量重做并再次确认。

任何新版本都必须经过原节点的明确用户确认。澄清答案只补充信息，不等于确认。确认完成后由原 Graph 的边进入下一阶段，不允许前端手工拼接阶段或另起 Workflow。

聊天区在修订节点运行时只展示“正在重新生成对应产物”的实时状态；节点生成完成后直接展示该产物的正式确认卡。设计阶段不展示通用 Workflow 步骤归档摘要。

## 生命周期

意图命中正式产物时，创建生命周期受控回退到对应生成阶段：

| target | lifecycle stage |
| --- | --- |
| `requirements` | `generating_requirement_spec` |
| `product_planning` | `generating_product_plan` |
| `ui_confirmation` | `generating_ui_designs` |

回退只允许发生在尚未完成的创建规划阶段。模板生成或进入工作台后不得借此入口修改创建产物。
