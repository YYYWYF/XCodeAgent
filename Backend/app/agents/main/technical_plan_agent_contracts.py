"""完整生成和局部修订共用的当前 Agent 七模块规划合同。"""


TECHNICAL_PLAN_AGENT_CONTRACT_REQUIREMENT = (
    "Agent 合同规则：agent_contracts 必须且仅覆盖已确认 ProductPlan.agents 中存在 "
    "surface.enabled=true 页面入口的 Agent；没有启用入口时显式使用空数组，不省略。"
    "允许补齐缺失合同、移除已停用合同和修复七模块配置，不能扩大已确认产品范围。"
    "每项只能有 agentId、name、purpose、pageIds、agentSettings、interactionContract；"
    'interactionContract 必须完整采用以下结构，其中 interactionKinds 按业务需要填写：{"mode":"conversation","inputSchema":{"type":"object",'
    '"properties":{},"required":[],"additionalProperties":false},"textOutput":true,'
    '"publicStateSchema":null,"outcomes":[],"interactionKinds":[]}。'
    "用户提问只通过 AG-UI messages 传递，不是业务输入字段；不能在 inputSchema.properties 或 required 中添加 message、prompt 或 question。"
    "普通文本回复由 textOutput=true 声明，不是业务 outcomes；不能把回复说明或验收标准放入 outcomes。"
    "properties 必须为 {}，required 和 outcomes 必须为 []，不是待补充的示例占位。"
    "文本会话是公开输入输出形式，不限制内部 Tool/Skill/Knowledge；按已确认业务需求规划 enabled 和 bindings/files，不得为通过校验删除或禁用已有能力。"
    "interactionKinds 按业务需要显式声明 clarification/confirmation/approval_required 的不重复子集；写入 Tool 必须声明 confirmation 或 approval_required 并在实际执行前确认。"
    "普通 Endpoint 必须显式 kind=business；首次规划由平台物化运行入口，修订时原样保留已有运行入口。"
    "agentId/name/purpose 原样采用产品事实，agentId 为 lower_snake_case 且不能为 runtime。"
    "pageIds 按 pageActionBindings 顺序只包含 surface.enabled=true 的 pageId。"
    "agentSettings 必须且只能有 prompt/model/memory/tools/skills/knowledge/context 七个对象。"
    "prompt 只允许非空中文 systemPrompt、可选 persona={role,tone} 和可选字符串数组 constraints；"
    "model 只允许 source=project 和可选 generation={temperature:0到2的数值}，继承项目模型及凭据。"
    "memory 描述可信 Principal/thread 隔离和模板 checkpoint 复用；context 描述可信输入及缺失处理。"
    "tools 必须有 enabled 和 bindings 数组，enabled 与 bindings 是否非空一致；每项只能有 "
    "toolId/apiContractId/endpointId，精确引用当前接口，toolId 为唯一 lower_snake_case。"
    "Tool 在 Runtime 内调用对应 ApplicationService，不新增公共 Agent 业务接口。"
    "skills 和 knowledge 禁用时只能为 {\"enabled\":false}，不得保留 files 或其他字段；"
    "启用时只能有 enabled=true 和 files，files 为1到16个不重复的项目内精确 Markdown 文件。"
    "技能路径采用 agent-runtime/src/app/skills/<集合>/<技能>/SKILL.md；"
    "知识路径采用 agent-runtime/src/app/knowledge/<名称>.md 或该目录下的普通 Markdown 子路径。"
    "资源路径不得有隐藏段、..、通配符、反斜杠或 URL；只规划业务实际需要的资源，"
    "未要求的模块保持禁用，不虚构外部检索配置。禁止在任何模块写入模型或数据库凭据。"
    "本轮候选仍需平台全量校验和用户正式确认；不要选择拓扑或修改应用开关。\n"
)
