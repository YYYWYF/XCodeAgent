# 单数据源字段取值规则

完整配置及任务阶段示例见 [字段映射示例](FIELD_MAPPING_EXAMPLES.md)。

任务规划与后端实现共用 `agents/field_value_prompt_rules.py` 中的英文取值规则；`agents/field_value_prompt_rules_cn.py` 是仅供查阅的中文对照，不参与运行时导入或注入，修改规则时同步维护两份内容。保留原始契约字段名。明确允许依赖缺失的业务分支先按已确认说明执行，其余必需输入缺失再适用缺值策略；非法输入、空结果与上游失败不能混作缺值。后端执行的契约语义缺口使用 `contract_mismatch`，任务类型边界或授权路径缺口使用 `plan_mismatch`，均要求具体 `change_request`。

字段映射沿用原有数据来源选择及“更换数据源”入口，不增加数据表 / 外部 API 的场景 Tab。每个 Endpoint 在工作台绑定一张数据表或一个外部 Operation；多字段依赖不等于多数据源。

## 交互

- 查询条件保留列、运算符、值来源、参数及一层 AND/OR 分组。
- 数据库写入和外部 API 入参按目标字段配置。一个接口入参可供多个目标复用。
- 接口参数只提供直接取值，不在下方提供“添加业务处理”。需要加工时选择“业务处理”，在独立弹窗中添加参与处理的字段，并填写自然语言规则；切换时保留原接口参数为初始依赖。
- 固定值使用目标字段类型；集合条件输入 JSON 数组。“内置参数”值来源选项置灰，不可选择。业务规则中的当前用户 ID、当前时间依赖仍由服务端可信上下文提供。
- 业务规则采用逐行“值来源 + 参与处理的字段”，支持添加、删除多项输入，参数按位置分组，同一字段不能重复选择；查询与外部入参只允许请求字段，返回处理可使用当前数据源字段。内置参数入口置灰。零输入可按业务规则独立生成。
- 规则弹窗及字段摘要均不展示“输入缺失时”、缺值策略和默认值控件，新规则沿用 `error`，编辑已配置规则时保留原缺值策略与默认值；存储与生成契约保持不变。
- 移除逐字段“业务入参用途”入口，改为可选的“接口映射说明”：用自然语言描述接口整体的校验、执行顺序、分支和返回处理。复用 `implementationDescription`（最多 4000 字符），随草稿暂存及正式映射确认，传入任务规划和代码生成；不填写不阻止确认，说明不得覆盖结构化契约或授权额外数据源。
- 规则显式应用前不替换原值。规则弹窗打开期间禁用外层暂存和确认，取消或关闭恢复原配置。草稿与正式确认边界保持不变。
- 浅色和深色样式共用工作台 `--wb-*` 主题变量。

规则使用 760px 独立弹窗，不占用条件行的参数列；输入行仅保留值来源、字段和删除操作，采用宽松分栏，窄屏上下排列。取消和关闭丢弃未应用编辑，应用后回到简短摘要。

### 业务规则输入来源范围

| 场景 | 接口参数 | 数据源字段 |
| --- | --- | --- |
| 数据库查询条件（查询、修改、删除） | 当前应用接口的请求字段 | 不提供，查询尚未执行 |
| 数据库写入字段（新增、修改） | 当前应用接口的请求字段 | 不提供，当前取值契约不支持读取已有行后加工 |
| 外部 API 入参 | 当前应用接口的请求字段 | 不提供，上游调用尚未执行 |
| 数据表返回字段 | 当前应用接口的请求字段 | 当前绑定表的字段 |
| 外部 API 返回字段 | 当前应用接口的请求字段 | 当前绑定 Operation 的响应体字段 |

- 接口参数按 Path、Query、Header、Body 分类，不包含本接口的返回字段，也不把外部 API 的请求目标当作可读输入。
- 值来源菜单仅显示当前场景中存在候选字段的类别；无请求字段时隐藏“接口参数”，无数据源候选时隐藏“数据源字段”。唯一保留的置灰类别是暂未开放的“内置参数”。
- 已被其他输入行使用的字段不可重复选择；没有剩余可选字段时禁用“添加输入”。允许零输入规则，不强制添加空行。
- “固定值”和“业务处理”属于目标取值方式，不是业务规则的输入字段类别；常量和处理过程写入处理规则。

## 当前契约

正式 JSON 和草稿格式均为 `endpoint-field-mapping.v7`，不提供历史读取、迁移或双写。

`sourceBinding` 保存当前选定表或 Operation 的身份，避免所有返回字段都是业务生成时丢失来源选择。它不是第二份数据源配置；实际元数据仍来自数据源目录。

`databaseQuery.items[].right`、`databaseWrites[].right`、`externalApiBindings[].right` 和 `fieldMappings[].right`（`mappingType=value_mapping`）共用取值契约：

| kind | 内容 |
| --- | --- |
| endpoint | `endpointField`，必须是当前 Endpoint 的完整请求字段快照 |
| fixed | `value`，与目标类型及条件运算符形态匹配 |
| business | `origin`、`endpointFields[]`、`builtinFields[]`、`businessDescription`、`missingBehavior`、可选 `defaultValue` |

`origin` 仅用于恢复接口参数 / 内置参数 / 业务生成的编辑状态。所有业务取值以完整依赖清单和处理说明为准，平台不执行用户表达式或脚本。

`missingBehavior` 为 `error`（报错）、`omit`（省略目标值，查询时跳过该条件）或 `default`（使用类型匹配的默认值）。默认值仅在 `default` 下允许；数值零和布尔 false 均为有效值。外部必填参数禁止 `omit`。

外部请求目标统一保存在 `externalApiBindings: [{ externalField, right }]`，不再由应用请求侧的 `source_mapping` 表示。每个目标唯一，输入可复用。调用前的规则禁止依赖该次调用尚未返回的响应。

数据源返回加工继续使用 `source_mapping`、`sourceFields[]`、`processingType` 和 `businessDescription`，并增加缺值策略。`business_description` 继续表达应用入参的纯业务用途。确认时检查依赖快照、来源范围、目标唯一性、类型和必填覆盖。

## 下游消费

独立 `/endpoint-designs/run` AG-UI 流保存草稿和确认后的 Markdown/JSON 双文件，不推进主工作流。修改规则后必须再次确认，开发门禁继续通过显式“确认并检测”复检。

Markdown、只读详情、冻结契约、构建任务输入和业务验收均保留完整规则。生成代码在数据源调用前计算请求值；数据库固定值和加工结果均参数化，不生成 SQL 字符串片段。update/delete 的所有条件因缺值被省略时必须报错，禁止执行无条件修改。

主要实现：`domain/api_design{,_fields,_values,_database}.py`、`services/api_design_values.py`、`FieldMapping/{ValueRuleEditor,RuleEditor,ResponseFieldMapping,MappingDescription}.tsx`。

回归入口：`tests.test_api_design_values`、`tests.test_binding_workspace`、`tests.test_api_design`、`tests.test_endpoint_design_detail`；前端纯函数用例在 `Frontend/tests/fieldMappingModel.test.ts`。
