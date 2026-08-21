# 设计阶段底部输入与原规划 Graph 回接

## 目标

工作台处于产品设计阶段时，用户可以解锁底部输入框并用自然语言修改已有设计产物。输入先由 Design Conversation Agent 识别最早受影响的正式节点，然后在原 `application_planning_workflow`、原 thread 和原 checkpoint 中回到该节点。

正式链路始终是：

```text
design_intent_analysis
  ├─ requirements -> requirements_review(interrupt) -> product_planning
  ├─ product_planning -> product_planning_review(interrupt) -> ui_confirmation
  ├─ ui_confirmation -> ui_confirmation_review(interrupt) -> technical_planning
  ├─ technical_planning -> technical_planning_review(interrupt) -> END
  └─ design_chat_response -> 原审阅门(interrupt)
```

意图节点只负责路由，不复制 RequirementSpec、ProductPlan、UiDesign 或 TechnicalPlan 的生成逻辑。

## 运行边界

- AG-UI endpoint：`POST /application-page-planning/run`
- workflow scope：`application_planning`
- Graph：`Backend/app/graph/application_planning_workflow.py`
- 意图 Agent：`Backend/app/agents/design_conversation/router.py`
- 恢复请求：`forwardedProps.applicationPlanningInteraction`，携带当前服务端中断的 `gateId`、`artifactRevision`、`artifact` 和显式 `action`
- 实时状态：只有服务端审阅门创建修订事务后，started/updated 快照才投影 `design_change_submission`、`design_change_request`、`design_change_target`、`design_change_reason` 和修订开始前冻结的 `design_change_existing_artifacts`；前端仅在冻结快照明确标记当前阶段已有产物时展示“重新生成”，不得用当前待确认产物或残留 target 作兜底
- threadId：继续使用原创建规划 threadId
- checkpoint：服务端 SQLite checkpoint 是恢复权威；前端不回传 `resumeState` 重建创建规划上下文

底部自由输入提交 `action=design_change`，恢复当前审阅 interrupt 后进入 `design_intent_analysis`。正式卡片分别提交 `answer`、`confirm` 或 `revise`；UI 设计稿确认卡中的 `select_template`、`regenerate`、`adjust_pages` 和 `skip` 提交 `action=ui_action`，恢复当前 `ui_confirmation_review` 后直达 `ui_confirmation`，不得调用意图 Agent。创建规划节点只按该结构化 action 决定确认、修订或回答分支，不再用用户文本中的中文关键词二次猜测。服务端通过 Pydantic 校验动作与当前审阅阶段的组合，并在同一 thread 内串行执行“读取中断、校验 `gateId + artifactRevision`、恢复 Graph”的完整区间；旧卡片、并发重复提交和产物更新后的过期提交都在任何修订 started 投影之前拒绝。

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

`design_change_request` 保存用户原始输入，`design_change_generation_target` 是单节点再生成游标：首个受影响节点消费原始指令；该节点的新版本确认后，游标推进到下一正式产物，并改用“上游已确认新版本”的依赖更新指令。待确认期间游标不推进，确认 resume payload 优先于生成指令，因此不会重复生成当前产物，也不会把原修改文本重复套给下游。不再维护 applied-nodes 列表，也不在协议层替换通用 `request`。

- RequirementSpec：把现有 spec 与最新反馈交给原需求分析节点；最新反馈只作为增量补丁，未提及事实和稳定 ID 必须保留，摘要必须描述合并后的完整需求，不能退化为本轮输入。
- ProductPlan：把现有 plan、已确认 RequirementSpec 与原始变更交给原产品规划节点；只修改反馈或新 RequirementSpec 实际影响的字段，其余页面目标、信息、操作、状态、验收标准和摘要保持不变。
- UiDesign：页面集合不变时转换为 `adjust_pages`；页面集合变化时由原 UI 节点重建当前页面集合。
- TechnicalPlan：上游重新确认后，基于新的 ProductPlan 和 UiDesign 增量重做并再次确认。

任何新版本都必须经过原节点后的 review interrupt 明确确认。澄清答案只补充信息，不等于确认。确认完成后由同一 Graph task 继续下一阶段；传输层会开启新的 AG-UI runId，但始终复用原 threadId 和 checkpoint，不允许前端手工拼接阶段或另起 Workflow。

聊天区在修订节点运行时只展示“正在重新生成对应产物”的实时状态；需求节点仍在澄清时只展示问题，不写需求草稿或页面兜底，澄清结束后才在右侧展示最新 Markdown 草稿，聊天区只展示对应的确认操作卡。用户确认后才形成正式产物。设计阶段不展示通用 Workflow 步骤归档摘要。

## 生命周期

意图命中正式产物时，创建生命周期受控回退到对应真实处理阶段；需求层先回到分析，只有新版本再次确认后才进入文档生成：

| target | lifecycle stage |
| --- | --- |
| `requirements` | `analyzing_requirement` |
| `product_planning` | `generating_product_plan` |
| `ui_confirmation` | `generating_ui_designs` |

回退只允许发生在尚未完成的创建规划阶段。生命周期进入 `ready_for_workbench` 后，底部恢复普通 `/conversation/run` 自由对话，不再显示设计流程输入锁，也不得借设计变更入口修改创建产物。
