# XCodeAgent 指标计算方式 v1

依据已确定的 `telemetry-upload-v1.jsonc` / `telemetry-batch.schema.json`。本文件逐项定义计算口径，不表示采集已完成或所有指标已经具备精确所需的数据。按用户确认，本期做累计完成量与事后报表；代码入库沿用用户已有算法。完整质量归因、稳定身份等开发事项见 `implementation-review-v1.md`。

## 已确认的业务口径（2026-10-14）

- **代码入库**：使用用户已有计算方式。本方案不重新定义本地/远端入库条件、归因、去重或聚合算法，只承接既有算法输出。
- **页面/API**：统计截至报表截止时点累计完成过的唯一产物，包含未发布应用；同一产物再次完成不重复增加，之后删除或修改也不扣减历史累计完成量。
- **QPS**：做事后报表，按真实调用开始时间归属统计周期；允许结束后上报和迟到数据回补。本期不要求实时在途 QPS/并发，不为此增加开始事件。

## 1. 统一规则

- 统计周期记为 **T=[开始时间,结束时间)**；事件按 `occurred_at`，调用次数按 `started_at`，Token/调用耗时按 `ended_at` 归属周期。使用时长取与 T 重叠的有效区间。
- `project_id` 是应用身份；`conversation_id` 是可见对话身份；`thread_id` 是实际执行线程，不能拿线程数当对话数。
- 先按 `record_id` 去重；Trace 再按 `span_id` 去重。版本成功事实按 `(project_id,repo_id,commit_sha)` 去重，重推不能重复计数或改变首次成功时间；代码入库结果的业务去重由用户已有算法负责。
- `E(事件名)` 表示对应事件；`M` 表示 `span_kind=model` 的 Trace；表中 `payload.*` 均指 `data.payload.*`，`result.*` 均指 `data.result.*`，`model.*` 均指 `data.model.*`。
- Token 正式统计只用 `usage_source=provider`；总 Token 需 `usage_status=complete`。估算值单列，未知 `null` 不当作 0；报告同时给出有效样本数/字段覆盖率（已观测调用中字段可用的比例，不证明所有真实调用零丢失）。
- 业务质量只有在采集端能确定“一次独立节点/生成过程”时才能按 operation_id/阶段/节点聚合。并行 Unit、同节点多次生成会发生键碰撞，不能直接折叠成一个样本；业务样本标识、结果归属和自动修复链关联尚需补齐。明确区分 Node 业务摘要、模型技术返回和 Workflow 总结果；未决过程单列。
- 次数按真实发生时间归属 T；质量结果类比例按对应独立业务样本的最终结果时间归属 T，不能按最后上传时间归属；动作行为比例按受理时间归属 T。**完成一个应用**类指标以 T 内首次发布的应用为样本，但回查这些应用从创建到首版本的完整记录，不能只取 T 内发生的调用。
- 比例公式最终乘 `100%`；分母为 0 时显示“无样本”，不显示 0%。模型耗时/资源耗时可以按调用相加，用户有效使用时长必须求区间并集。

### 有效使用时间与首版本边界

`U(对象,时间范围)`：取该用户/对话/应用所有 workflow 根 Span 的 `active_intervals`，截取指定时间范围，合并重叠区间后求总时长。仅计 Agent 正在执行的模型、工具、节点及实际调用等待；排除纯用户等待、确认、暂停和尚未执行的排队。**禁止相加父子 Span 或把并行 10 秒算成 20 秒。** `active_intervals=null` 是缺失；`[]` 是确知没有执行。

`first_version_at(应用)`：完整采集的新应用最早一次成功 `application_version_created.occurred_at`。首版本指标只纳入有真实 application_created、新建后连续采集且首发事实明确的应用；上线前已有或导入的应用不能把“首次观察到的发布”当成真实首版本，也不回填假创建时间。由事实时间计算，不按上传顺序判断。“发布”沿用需求中的提交并推送成功，不等同于部署上线。

## 2. 领导指标：用户、应用、对话

| 指标 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| 用户数 | 统计截止时点，按现有账号系统口径计数用户 | 已有账号系统，不用埋点估计账号总数 |
| 活跃用户数 | T 内 `E(user_turn_submitted)` 且 `turn_kind=request` 的 `user_id` 去重数 | 按“周期内主动发起对话/请求”口径；回答和确认可另外统计 |
| 新建应用数 | T 内 `E(application_created)` 的 `project_id` 去重数 | `creation_source=new_app`；打开/导入已有工作区不算新建 |
| 发布应用数 | 先按版本身份找首次成功时间，再取首次成功时间在 T 内的版本所对应 `project_id` 去重数 | 同一应用产生多个新版本仍只计一个；相同 SHA 重推不生成新版本事实，不能靠重复上传改变周期 |
| 应用版本数 | T 内首次成功的 `(project_id,repo_id,commit_sha)` 去重数 | `E(application_version_created)`；相同 SHA 重推不算新版本 |
| 页面数量（累计完成） | 截至统计截止时点，完成事件中 `(project_id,context.target.id)` 去重数，筛选 `context.target.type=page` | `E(development_artifact_completed)`；包含未发布应用。重复完成不增加，之后删除不扣减；不以版本快照代替累计完成量 |
| API 数量（累计完成） | 截至统计截止时点，完成事件中 `(project_id,context.target.api_contract_id,context.target.id)` 去重数，筛选 `context.target.type=endpoint` | `E(development_artifact_completed)`；包含未发布应用。相同 Contract/Endpoint 重复完成不增加；只记实际完成，不记仅规划接口 |
| 对话数 | T 内 `E(conversation_created)` 的 `(project_id,conversation_id)` 去重数 | 包含 `automatic` 和 `user`；后台技术线程、控制请求线程、上下文续接不新增对话 |
| 应用平均对话数 | 对 T 内首版本完成的每个应用，统计创建至首版本前创建的对话数，再取平均 | 分母为这些首版本完成应用数，不是 T 内所有应用数 |
| 对话平均耗时 | 对 T 内创建的对话，分别计算 `U(对话,创建至 T 截止时点)`，再取平均 | 这是截至统计时点的累计有效耗时均值；确知无执行的空对话为 0，采集缺失不为 0。周期内已有对话的使用均值可另报 `U(对话,T)/T 内实际有执行的对话数` |
| 应用平均完成耗时 | 对 T 内首版本完成应用，计算 `U(应用,应用创建至 first_version_at)`，再取平均 | 只算首版本前的有效执行时间，不用“最后使用时间−创建时间” |
| 用户/应用累计 AI 使用时间 | `U(用户或应用,T)`；累计报表把范围扩展至历史观测区间 | 首版本后的继续使用也计入；与“首版本完成耗时”分别展示 |

页面/API 主指标是累计完成量，不是当前存量。若展示 T 内新增完成量，先按全量可信记录求每个产物的首次完成时间，再筛选首次完成时间属于 T 的产物；不能只在 T 内去重后当新增。累计只能基于可信完成事实，不伪造采集前的历史完成时间。

## 3. 领导指标：代码、Token、模型运维

| 指标 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| 生成代码行数 | T 内 `E(code_generated).generated_lines` 求和 | LLM 真正生成的代码；沿用已有计量方法，不用 Token 换算行数 |
| 写入代码行数 | T 内 `E(code_written).written_lines` 求和 | 生成代码实际写入成功后统计；同一写入事实重试不重复计数 |
| 入库代码行数 | 使用用户已有算法提供的入库代码行数结果；周期统计和去重也沿用该算法 | 对接 `committed_lines` 与 `measurement_method`；不由本方案根据 commit/push 重新判定入库，也不擅自相加可能为累计值的多份结果 |
| 代码入库率 / Studio 代码占比 | 直接使用用户已有算法提供的 `code_inclusion_rate`；跨应用汇总由原算法处理 | `measurement_method` 标识既有方法；`studio_code_lines/repository_code_lines` 仅在原算法提供时记录，不在本方案重算或改变分子、分母及聚合方式 |
| Token 总数 | T 内结束的 M 的 `model.total_tokens` 求和 | 只汇总模型 Span，父节点的聚合量不再次加入；失败调用已返回的 usage 也计入 |
| 各模式 Token 消耗 | 上述 Token 按 `context.mode=ultra/pro/fast` 分组求和 | 模式来自实际选择；null 单列，不按模型名反推 |
| 应用平均 Token 消耗 | 对 T 内首版本完成应用，汇总应用创建至首版本前结束的模型调用 Token，再取平均 | 完整报表与首版本完成耗时使用共同的有效应用集合：创建、首发、执行区间和 Token 均有可靠记录；缺失应用单列，不把部分 Token 当完整成本；跨首版本边界的调用单列，不随意按秒分摊 |
| 上行 TPS（窗口吞吐） | T 内结束的 M 的已知 `input_tokens` 合计 / T 的秒数 | 是应用侧输入 Token 完成量吞吐，不是模型 Provider 的预填充处理速度 |
| 下行 TPS（窗口吞吐） | T 内结束的 M 的已知 `output_tokens` 合计 / T 的秒数 | 和上行相同窗口及模型维度；Token 未知部分单列 |
| 单次流式输出 TPS | `output_tokens / (output_duration_ms / 1000)` | 仅已知 Provider 输出 Token、真实首尾输出证据且间隔大于 0 的样本；是客户端观测区间的估算输出速率，隐藏 reasoning、分块缓冲会影响解释；不宣称 Provider 精确生成速度 |
| TTFT / 首 Token 延迟 | 直接使用 M 的 `model.ttft_ms`；汇总平均值、P50/P95 | 等于逻辑调用开始至首个非空生成输出；非流式或未观测不算样本，不能用总耗时代替 |
| QPS | T 内开始的模型 Span 数 / T 的秒数 | 用户已选择事后报表：结束后上传，按 started_at 回归统计窗口，迟到记录允许回补。统计逻辑模型调用 QPS，不宣称包含 SDK 隐藏重试的 Provider 物理 QPS |
| 模型并发数 | 时点 t 满足 `started_at ≤ t < ended_at` 的模型 Span 数 | 用已结束 Trace 计算事后峰值/曲线即可；本期不要求实时在途采集。未知结束时间的样本单列，不猜测结束边界 |

## 4. 产品行为指标

| 指标 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| 纯文本 / 文本加图片使用比例 | 对 `turn_kind=request` 分组：仅 text 的次数 / 请求数；含 image 的次数 / 请求数 | `E(user_turn_submitted).input_modalities`；图片总数为 `image_count` 求和，不上传图片正文 |
| AI 产物接受 / 修改 / 重生成比例 | 对候选首次有效评价，分别统计原稿直接接受（accept 且未编辑）、编辑后接受（accept 且已编辑）、要求 AI 修改（revise）、重新生成（regenerate）、跳过（skip）的比例 | `E(artifact_reviewed)`，按 `(project_id,artifact_id,artifact_revision)` 取首次受理评价，统一分母；未评价单列。不能把用户编辑后确认计为原稿接受，也不能用后续最终接受覆盖首次不满意 |
| 用户直接编辑比例 | `user_edited=true` 的已评价候选数 / 已评价候选数 | `E(artifact_reviewed)`；不从 revision 文案猜是否编辑过 Markdown |
| Page / Endpoint / Entity 启动比例 | 对应 `context.target.type` 的开发启动事件数 / 全部这三类启动事件数 | `E(development_target_started)`；不用模型调用数量作为启动数 |
| 页面参考模板使用比例 | 开发启动时 `context.extra.reference_template_id` 非空的页面操作数 / 模板使用情况已知的页面启动操作数 | 模板选择次数另按 `E(ui_design_action_accepted).action=select_template` 计数；两者不是同一分母 |
| 模板使用后的 Build / 返工差异 | 将开发操作按是否使用模板分组，比较 Build 首轮成功率、修复轮数、Token/耗时和后续返工 | 用 `operation_id` 关联同一业务过程；跨新操作的严格返工归因还需要源操作关联，不能只凭时间接近推断 |
| 工作台自然语言请求类型分布 | 各 `request_kind` 的分类事件数 / 全部已分类请求数 | `E(request_classified)`；按 `route_stage` 可再看进入设计/规划/开发的比例 |
| 二次修改影响范围分布 | 对相同 `change_id` 取最终已接受的影响分析，按 `earliest_stage` 计数 / 已知影响分析数 | `E(revision_impact_decided)`；受影响产物集合还可按 `affected_artifacts` 分组；拒绝和批准分别展示 |
| Revision Impact 批准率 | `decision=approved` 的修订数 / 已有批准或拒绝决定的修订数 | `E(revision_impact_decided)` 按 `change_id` 去重；尚未决策的不当作拒绝 |
| 停止 / 暂停 / Retry / Resume 使用情况 | `E(control_action_accepted)` 按 `action`、`business_stage` 分组计数 | `action_id` 去重；后续结果用 `target_run_id/child_run_id/operation_id` 查 Trace，不把未接受按钮点击算动作 |
| Element Context 使用比例及效果 | `has_element_context=true` 的请求数 / 上下文标记已知的请求数；再比较两组 Token、耗时、成功率 | 请求入口用 `E(user_turn_submitted)`；执行比较用同一操作传播的 `context.extra.has_element_context` |
| 应用场景画像分布 | 每个 `project_id` 取截至统计时点最新 `E(application_profile_updated)`，按场景/标签/终端/数据源/认证授权分组计数 | `scenario_code/scenario_tags/terminal/datasource_type/auth_enabled/authorization_enabled`；未知单列，同一应用不因多次画像更新重复计应用数 |

## 5. Agent 成本、需求分析与重试

按 `business_stage`、`node_name`、`run_name`、模型、模式、产品版本、场景分别分组，即可比较相同类型任务。这里的“模型成本”使用 Token 和调用耗时，不推算未提供的货币费用。

| 指标 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| Model Call 次数 / 总耗时 / 平均耗时 | M 的去重次数；`duration_ms` 求和；已知耗时之和 / 已知耗时调用数 | 各节点可以分别计算；相加得到调用资源耗时，不是有效使用时长 |
| Tool Call 次数及分布 | `span_kind=tool` 的 Span 去重数，按 `run_name` 分组 | 工具耗时同样取 `duration_ms`；不从模型文本推断工具是否执行 |
| 节点执行耗时 | `span_kind=node` 的已知 `duration_ms` 分布 | 用于找节点瓶颈；不再把其模型/工具耗时加进去当节点时间 |
| Requirements 总调用 / Token / 耗时 | `business_stage=requirements` 的 M 计数及 Token/耗时求和 | 不包含用户阅读、回答、确认时间 |
| Requirement Analysis 平均成本 | `run_name=requirement_analysis` 的 Token/耗时分别求平均 | 如需一次完整需求分析成本，先按需求业务操作汇总，再按操作取平均，和单次调用均值分开 |
| Authorization 额外成本 | `authorization_evidence_classify` 与 `authorization_fact_extract` 的调用、Token、耗时求和 | 只统计实际发生的模型调用；没有权限需求且没有调用时不制造事实 |
| 需求澄清率 | `max(clarification_round)>0` 的需求业务操作数 / 有明确澄清观测的需求业务操作数 | `context.extra.clarification_round`；正在进行的过程可单列 |
| 平均澄清轮数 | 已成功产生联合需求文档的操作中，分别取最大 `clarification_round`，再求平均 | Trace 中取 `requirement_analysis`，完成边界由 `E(requirement_document_ready)` 确定；事件也带最终轮数 |
| 多轮澄清比例 | 最大 `clarification_round≥2` 的已完成需求操作数 / 澄清轮数已知的已完成需求操作数 | 与平均澄清轮数使用相同完成样本 |
| 平均每轮问题数 | 先按 `(project_id,operation_id,clarification_round)` 去重澄清轮次，求已知 `question_count` 均值 | 排除第 0 轮；同轮被多个模型携带时只计一次 |
| 各模型职责的 Business Retry 次数 | 各 `run_name` 中 `attempt>1` 的模型 Span 数 | 例如 requirement_analysis、authorization_fact_extract、product_plan_generate；不计底层网络重试 |
| Business Retry 额外成本 | `attempt>1` 的 M 的 Token/耗时求和 | `attempt` 必须是同一业务生成返回后校验失败的重生成序号，不把澄清后的正常执行当 Retry |
| ProductPlan 首次有效率 | product_planning 节点过程 `first_attempt_passed=true` 数 / 首轮校验结果已知的过程数 | 只用真实首轮业务校验结果，不能因模型正常返回就认为 ProductPlan 有效 |
| 联合需求文档生成次数 | `E(requirement_document_ready)` 按 `(project_id,document_id,artifact_revision)` 去重数 | RequirementSpec 与 ProductPlan 的联合候选只计一个文档版本 |
| 联合需求文档不通过率 | 首次有效评价为 `decision=revise/regenerate` 的联合候选版本数 / 已首次评价的联合候选版本数 | 关联 `document_id=artifact_id` 和相同 `artifact_revision`；尚未评价的文档不当作不通过 |
| Requirements Revision Rate | 实际因用户修改而重新生成的既有联合文档版本数 / 已生成联合文档版本数 | 严格定义需把修改后的生成关联到源文档版本；当前 JSON 未明确这条源版本关联。可先报上一行“评价不通过率”，不能混称实际再生成率 |
| Revision 模型成本 | `execution_kind=revision` 的 M 的调用、Token、耗时求和 | `change_id` 聚合同一次正式修改。完整修订成本还应包含该 change_id 下澄清后的 resume 等实际调用，另以“整次修订成本”命名 |
| Requirements 最终失败率 | 已明确失败的 requirements 业务操作数 / 已产生明确成功或失败结果的 requirements 操作数 | 最后业务结果与 `E(workflow_failure)` 联合判断；后续恢复成功不永久算最终失败，历史失败事件保留 |

## 6. 各阶段质量和效率

以下按对应阶段/节点的业务过程计算，不混用模型调用数作为业务操作数。**自动修复成功取该次自动修复链的结局，不把之后用户手动 Retry 的成功当作自动修复成功。**

| 阶段 / 指标 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| UI 模板采用 / 重生成 / 调整 / 跳过比例 | `select_template/regenerate/adjust_pages/skip` 各动作次数 / 已接受 UI 动作总次数 | `E(ui_design_action_accepted)`；这是动作构成比例，若按操作渗透率则改用去重 operation_id 作分母 |
| UI 页面生成稳定性 | 成功页面生成过程数 / 已有成功或失败结果的页面生成过程数 | 使用页面目标和实际 UI 生成节点/Worker 的 `result.outcome`；缺失结果、尚未结束单列；首轮通过可另用 `first_attempt_passed` |
| TechnicalPlan 首次有效率 / 用户修改率 / 最终失败率 | 首轮通过过程占比；已评价 technical_plan 候选中 revise/regenerate 占比；最终失败过程占比 | `first_attempt_passed`、`E(artifact_reviewed)`、最终 `outcome`；分别展示，不合成未经定义的“稳定性分数” |
| TechnicalPlan 自动修复触发率 / 成功率 | 首轮失败过程里发生自动修复的比例；已完成自动修复链中最终成功的比例 | `first_attempt_passed=false`、`repair_rounds>0` 与该链结局；跨显式 Retry 要分开 |
| API Design 首次通过率 / 阻塞率 | 首轮通过的已知检查数 / 已知首轮检查数；`outcome=blocked` 的检查过程数 / 有明确结果的检查过程数 | `business_stage=api_design_readiness`；缺失/过期 Mapping 按已有可区分 `error.code` 分类，未知原因单列 |
| Workspace Cache 命中率 | `cache_hit=true` 的扫描数 / cache_hit 已知的扫描数 | workspace_inspection 的结果，每次实际扫描算一次，不能把同一轮重复快照计成多次 |
| Code Graph 可用率 / 降级率 | `available` / `degraded` 的扫描结果数分别除以可用性已知的扫描结果数 | `result.code_graph_status`；扫描性能取对应 Node 的 `duration_ms` |
| DAG 首次成功率 | build_task_planning 首轮通过过程数 / 首轮结果已知过程数 | `first_attempt_passed`；Regenerate 次数取对应产物 `artifact_reviewed.decision=regenerate`，执行原因可用 `execution_kind=regenerate` 分析其成本 |
| Unit Reuse 比例 / Generate 数 / Task 数 | `Σunit_reuse_count / Σ(unit_reuse_count+unit_generation_count)`；分别汇总 generate 和 task 数 | `result.unit_reuse_count/unit_generation_count/task_count`；每个规划过程取最终摘要，不能相加累积进度快照 |
| Build 首轮成功率 / Repair 成功率 | 首轮通过过程占比；自动修复链中最终成功的比例 | build 的 `first_attempt_passed`、`repair_rounds`、最终 `outcome`；失败原因按结构化 error 分类 |
| 单测首轮通过率 / 平均修复次数 / Repair 后通过率 | 首轮通过过程占比；已完成单测过程的 repair_rounds 均值；已完成修复链的最终通过比例 | unit_test 的 `first_attempt_passed/repair_rounds/outcome`；测试用例通过率另算 `Σpassed/(Σpassed+Σfailed)`，skipped 不计执行用例 |
| 集成测试 Quality Gate / 首轮通过 / 自动修复 / 最终失败 | 已知门禁结果中 quality_gate_passed=true 占比；首轮通过占比；自动修复链成功比例；明确终态中 failed 占比 | integration_test 的首轮、修复、最终结果与 `test_summary`；跳过不冒充通过 |
| SmallTask 成功率 / 能力升级率 | `outcome=succeeded` 的过程占比；`capability_escalated=true` 的已知升级标记过程占比 | small_task_repair；无效 Agent 输出比例需逐次输出的实际校验结果/明确错误分类，未采集时不能靠 Token 或最终失败倒推 |
| Code Review Issue 分布 / Repair 触发率 / 成功率 | `review_issue_counts` 按级别求和；repair_rounds>0 的 review 过程占比；修复链成功比例 | code_review 的结果摘要；修复后 Build 通过率要用同一操作中明确关联的后继 Build 结果，不只看 review 无异常 |
| 项目启动成功率 / 最终验收通过率 | 实际项目启动操作中成功的占比；`acceptance_decision=accepted` 数 / 已有验收决定数 | 启动结果需能明确识别启动 Span/操作及其业务 outcome，不能从验收决定反推启动。拒绝/要求修订单列；最终交付成功还需成功版本事件 |
| 版本 / 模型 / 场景优化效果 | 按 xcodeagent_version、model.name、mode、应用场景分组，比较以上同类任务的成功率、修复率、Token和耗时 | 区分任务类型、业务阶段、样本数量与缺失覆盖率，避免把不同难度任务的均值直接当优化因果证据 |

## 7. 故障、交接与长上下文续接

| 指标 / 分析 | 计算方式 | 数据来源/说明 |
| --- | --- | --- |
| Model / Tool / 系统错误次数与分布 | Trace 中 error 非空的 Span 数，按 layer/code/模型/版本/阶段分组 | 父子 Span 可能传播同一个异常，**Span 错误数不等于独立事故数**；定位时查看调用树，不把重复传播算成多次业务失败 |
| 最终业务失败次数 / 失败率 / 可恢复比例 | 按 scope+operation_id 或 run_id 归并最终失败事实；失败率分母为同范围有明确终态的执行；recoverable=true 的失败占比 | `E(workflow_failure)`；区分历史“发生过失败”和观察截止时“最终仍失败”。自动恢复成功的瞬时错误不单发最终 Failure |
| 节点故障定位 | 按 `run_id/trace_id/span_id/parent_span_id` 查出调用树，再读 `node_name/error/model/xcodeagent_version` | 业务失败用 `failed_span_id` 跳转到 Trace；缺少错误码或 Trace 的情况显示未分类，不猜根因 |
| Thread Handoff 次数 / 阶段转换分布 | `E(thread_handoff)` 去重计数，按来源 business_stage 和 to_business_stage 分组 | 来源 identity 与目标 to_conversation_id/to_thread_id 形成边；普通 Retry/Resume 或独立新对话不算交接 |
| 用户完整对话链路 | 同一用户/应用按真实交接边及 occurred_at 还原路径，run_id 关联每段执行 | 相同 conversation_id 可再次进入；并发分支按边展示，不把项目下所有时间戳硬拼成一条链 |
| 超长上下文续接次数 / 对话续接比例 | `E(execution_thread_continued)` 的 context_limit 次数；发生过续接的 conversation_id 数 / 实际执行过的对话数 | 只统计真实续接；对话 ID 不变、thread_id/run_id 变化。后续成本可查 to_run_id；没有全部续接触发/失败事实时不能计算精确续接成功率 |

## 8. 当前字段的统计边界

主干公式已经列出，但当前 v1 不能作为“全部需求可直接实施”的冻结合同。必须结合 implementation-review-v1.md 处理业务样本、结果关联、稳定身份和数据校验；本次审阅不擅自增加新的上传字段：

- 首轮校验、每轮修复结局、SmallTask 输出校验、项目启动等必须记录**真实业务结果**；仅有模型正常结束或最终验收状态不够。
- 严格的源文档再生成率、跨新操作的返工效果归因，需要源文档/源操作关联；现有 JSON 没有把这些关联完整定义出来，不能声称精确分子已经可得。
- 当前没有独立的对话关闭事实，“对话平均耗时”采用截至统计时点的累计值，不能宣称已结束对话的最终全生命周期均值。
- 页面/API 只统计累计完成，本期不做当前存量，因此删除/撤销清单快照不是本期前置项。
- Provider 物理 QPS、预填充 TPS、隐藏 SDK 重试全过程，以及全部上下文超限触发/失败次数，不在当前字段的完整观测承诺中。
- 当前表中的比例和均值只统计适用且实际观测到的样本，报表应同时显示总样本、有效样本及缺失量。
