# 产品阶段自由输入与原规划 Graph 回接

## 目标

工作台处于产品阶段时，底部始终显示普通输入框。用户可以直接输入需求变化、产品行为变化、UI 调整、产品事实问题或闲聊，不需要先选择任务类型，也不需要点击“暂停并自由输入”。

产品 Agent 只维护产品 WHAT：

- RequirementSpec：产品目标、范围、角色、模块、页面、业务流程、业务信息和业务权限。
- ProductPlan：页面目标、操作、跳转、状态、可见结果和产品验收规则；它是内部派生产物，不在 UI 中成为用户显式编辑对象。
- UiDesign：布局、视觉层级、控件呈现、主题、响应式和已有交互的视觉表现。

TechnicalPlan、API/Schema、数据库和数据源实现、源码、Build、命令与测试均不属于产品阶段。

## 唯一正式链路

产品自由输入继续复用：

~~~
POST /application-page-planning/run
  -> application_planning_workflow
  -> design_intent_analysis
~~~

不得新建产品对话 endpoint、第二个规划 Graph 或第二个 planning checkpoint thread。前端提交 forwardedProps.applicationPlanningInteraction，保留服务端最新中断给出的 gateId、artifact 和 artifactRevision，自由输入固定使用 action=design_change。当前卡片的 answer、confirm、revise、ui_action 和 enter_planning 仍是独立结构化语义，不能由自然语言猜测或替代。

## Coordinator 与确定性 Policy

Backend/app/agents/design_conversation/router.py 中的 Coordinator 只返回语义：

~~~
intent:
  chat | read_only | requirement_change | ui_change | clarification | out_of_scope

change_level:
  requirement | product_behavior | ui | none

suggested_phase:
  planning | development | test | none
~~~

模型不得返回 Graph node。Backend/app/agents/design_conversation/policy.py 是代码层能力门禁，只允许以下组合进入原 Graph：

| intent + change_level | Graph target |
| --- | --- |
| requirement_change + requirement | requirements |
| requirement_change + product_behavior | product_planning |
| ui_change + ui | ui_confirmation |

RequirementSpec 未确认时，任何正式产品修改最早回到 requirements；ProductPlan 未确认时，UI 修改最早回到 product_planning。未知组合、旧 Graph target、技术节点或异常输出没有目标节点，默认零写入。

一条请求只要包含 API、Schema、数据库、源码、命令或测试等越界部分，就整体返回 out_of_scope，不得只执行其中的产品部分。

## 零写入意图

以下意图只生成 conversation_response 并回到原审阅门：

~~~
chat
read_only
clarification
out_of_scope
~~~

它们不得调用产物失效、当前产物 revision 或 lifecycle restart，不得改变 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan 或 application_planning_confirmation。只读问答只使用 Coordinator 收到的有界产品上下文，不扫描工作区源码。

## 正式修改和确认

正式修改仍由原节点写草稿并经过原确认门：

~~~
requirement_change
  -> requirements / product_planning
  -> downstream regeneration
  -> original confirmation gates

ui_change
  -> ui_confirmation
  -> original confirmation gate
~~~

design_change_request 保存原始输入，design_change_generation_target 和 design_change_generation_request 继续作为单节点生成游标；上游确认后才推进下游。RequirementSpec、ProductPlan 或 UiDesign 变化后，TechnicalPlan 按现有失效和重新确认规则处理。UiDesign 页面集合稳定时继续复用 adjust_pages，页面集合变化时重建。

## 前端输入与并发

产品阶段输入框提示为：

~~~
告诉产品 Agent 你想调整的需求、页面或 UI，也可以直接提问…
~~~

planning run 执行期间输入框仍可编辑，但发送按钮和回车发送都被禁止，并提示“当前设计正在生成，完成后即可发送新的调整。”前端不得为自由输入停止当前 run、排队第二个 run 或绕过同一 planning thread 的单写事务。进入待确认、等待用户或空闲状态后才允许发送。

规划阶段和开发阶段继续使用各自原有对话路径；产品专用自由输入仅在 workbenchPhase=product 时进入 /application-page-planning/run。
