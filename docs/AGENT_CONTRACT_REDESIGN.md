# Agent Contract 重设计

> 状态：第二版已确认；Contract Schema、规划编译、Markdown/前端阅读与 Build 消费已实施
>
> 更新时间：2026-09-06
>
> 适用范围：从已确认 ProductPlan 推导完整 Agent Contract，并驱动 TechnicalPlan、Build DAG、Agent Runtime、Java Gateway、测试与验收
>
> 本文既是当前设计基线，也记录第一批落地边界；未实现的 Runtime 能力仍必须保持关闭。

## 1. 设计结论

Agent Contract 应当是一份完整、可解析、可验证、可直接驱动代码生成和运行的执行快照，而不是只有工具和 Endpoint 的最小绑定。

每个 Agent Contract 包含：

1. 上游来源和 ProductPlan Hash；
2. 从 ProductPlan 投影的身份、能力、交互和业务边界；
3. 固定包含七部分的 `agentSettings`；
4. Invocation、Runtime、Security、Artifacts 和 Required checks；
5. 从 ProductPlan 验收标准与工程规则编译的 Evaluation。

`agentSettings` 固定包含：

```text
prompt
model
memory
tools
skills
knowledge
context
```

“完整”不等于全部由模型自由生成。最终 Contract 由 ProductPlan、TechnicalPlan API Contract、平台策略和部署环境共同编译，每个字段都必须有明确来源。

## 2. 核心原则

### 2.1 ProductPlan 是业务事实源

Agent 的名称、用途、能力、入口页面、交互方式、输入输出语义、业务边界和产品验收标准，只以已确认 ProductPlan 为权威来源。

Agent Contract 保存这些内容的确定性快照，供 Build 和 Runtime 独立消费，但快照不是第二份可独立编辑的产品事实。ProductPlan 变化后，原 Agent Contract 必须失效并重新生成。

### 2.2 Agent Contract 是编译结果

Agent Contract 不是规划模型的原始响应：

```text
ProductPlan 产品事实
       +
TechnicalPlan API/Endpoint 事实
       +
模型推导的 AgentSettings 候选
       +
平台 Runtime、安全和默认策略
       ↓
确定性解析、补齐和校验
       ↓
完整 Agent Contract
       ↓
Markdown 用户确认
       ↓
Build DAG 不可变输入
```

规划模型只负责需要语义判断的候选配置；平台字段、引用展开、模型能力检测、安全限制和产物路径由代码确定性生成。

### 2.3 安全配置不能由 Prompt 授权

System Prompt、Skill、Knowledge 文档、Tool description 和检索结果都属于不可信内容，不能扩大工具、网络、文件、数据库、凭据或审批权限。

### 2.4 当前契约只支持当前形态

实施本文后同步更新所有生产者、消费者、测试和正式文档，不增加旧字段读取、字段别名、双写、迁移或兼容分支。

## 3. 字段来源与所有权

| Contract 区域 | 主要来源 | 模型权限 | 最终决定者 |
| --- | --- | --- | --- |
| `source` | 已确认 ProductPlan | 无 | 平台 |
| `identity` | ProductPlan Agent | 无 | ProductPlan |
| `capabilities` | ProductPlan capabilities | 只能绑定 Tool | ProductPlan + Contract |
| `interaction` | ProductPlan interaction + 平台交互规则 | 可建议澄清策略 | ProductPlan + 用户确认 |
| `agentSettings.prompt` | ProductPlan + Agent 设计模型 | 可生成 | 用户确认 |
| `agentSettings.model` | 能力需求 + 项目模型配置 | 可建议最低能力 | 平台模型注册表 |
| `agentSettings.memory` | ProductPlan 多轮要求 + 部署策略 | 可建议长期记忆 | 用户确认 + 平台 |
| `agentSettings.tools` | ProductPlan capability + TechnicalPlan Endpoint | 可选择 Endpoint | Contract 校验器 + 平台权限 |
| `agentSettings.skills` | ProductPlan capability + 已批准 Skill Catalog | 可建议绑定 | 用户确认 + 平台加载器 |
| `agentSettings.knowledge` | ProductPlan 信息需求 +正式 Knowledge Source | 可建议绑定 | 用户确认 + 知识权限 |
| `agentSettings.context` | ProductPlan interaction + 模型窗口 + 平台策略 | 可建议策略 | Context Compiler |
| `invocation` | Gateway Endpoint + Runtime 规则 | 只选择 Gateway | 平台 |
| `runtime`、`security` | Runtime 模板和安全策略 | 无 | 平台 |
| `artifacts`、`requiredChecks` | `agentId` 和 Build 规则 | 无 | 平台 |
| `evaluation` | ProductPlan 验收 + 工程规则 | 无 | ProductPlan + 平台 |

## 4. 生成阶段

### 4.1 上游条件

只有以下条件同时满足时才生成 Agent Contract：

1. RequirementSpec 和 ProductPlan 已确认；
2. ProductPlan `agents[]` 非空；
3. TechnicalPlan 已生成可引用的 API Contract 和 Endpoint；
4. Agent Runtime 模板能力清单可用；
5. 项目 Model、Memory、Skill 和 Knowledge Catalog 可以被平台解析。

普通应用固定生成：

```json
{
  "agent_contracts": []
}
```

### 4.2 模型候选与正式 Contract 分离

技术规划模型可以产生 AgentSettings 候选，但候选不是正式 Artifact，不能直接交给 Build：

```json
{
  "agentId": "inventory_assistant",
  "gatewayEndpointId": "inventory_api.agent_message",
  "capabilityBindings": [
    {
      "capabilityId": "explain_inventory_status",
      "toolIds": ["get_inventory_status"]
    }
  ],
  "agentSettings": {
    "prompt": {},
    "model": {},
    "memory": {},
    "tools": {},
    "skills": {},
    "knowledge": {},
    "context": {}
  }
}
```

平台随后按 `agentId` 投影 ProductPlan，展开 Endpoint，解析模型与存储能力，解析 Skill/Knowledge 引用，补齐 Runtime、安全、路径和检查，最后生成完整 Contract。

模型候选不得包含或覆盖 `source`、`identity`、`capabilities` 中的产品事实、平台安全规则、物理凭据和产物路径。

## 5. 完整 Agent Contract

以下是 TechnicalPlan 最终持久化并供用户确认的完整结构：

```json
{
  "agentId": "inventory_assistant",
  "source": {
    "productPlanSha256": "sha256:...",
    "productAgentId": "inventory_assistant"
  },
  "identity": {
    "name": "库存助手",
    "purpose": "帮助用户理解库存状态并给出下一步建议",
    "boundaries": [
      "不得绕过库存审批",
      "不得未经确认直接修改库存"
    ]
  },
  "capabilities": [
    {
      "capabilityId": "explain_inventory_status",
      "name": "解释库存状态",
      "expectedResult": "用户理解当前库存及可执行的后续操作",
      "toolIds": ["get_inventory_status"]
    }
  ],
  "interaction": {
    "mode": "conversation",
    "supportsMultiTurn": true,
    "inputDescription": "用户输入商品、仓库或库存问题",
    "outputDescription": "返回库存状态解释和后续建议",
    "clarification": {
      "allowed": true,
      "trigger": "missing_required_context",
      "maxRounds": 3
    },
    "stateRequirements": {
      "loading": "展示正在分析库存",
      "empty": "提示用户补充商品或仓库范围",
      "error": "说明暂时无法读取库存并允许重试",
      "success": "展示库存结论和建议",
      "validation": "拒绝缺少必要业务范围的写操作"
    }
  },
  "agentSettings": {
    "prompt": {
      "persona": {
        "role": "专业、谨慎的库存业务助手",
        "tone": "清晰、克制、可执行"
      },
      "systemPrompt": "你负责根据用户问题和已授权工具结果解释库存状态。回答前确认商品与仓库范围；需要实时库存时必须调用对应工具；不得虚构工具结果或绕过业务审批。",
      "constraints": [
        "信息不足时先澄清",
        "不得声称执行了未调用的工具",
        "写操作必须等待平台审批结果"
      ]
    },
    "model": {
      "selection": "project_default",
      "modelRef": "project_default",
      "requiredCapabilities": {
        "streaming": true,
        "toolCalling": true,
        "structuredOutput": false,
        "vision": false
      },
      "generation": {
        "temperature": 0.2
      }
    },
    "memory": {
      "shortTerm": {
        "enabled": true,
        "store": "sqlite",
        "connectionRef": "agent_runtime_checkpoint",
        "scope": "thread",
        "retention": "application_managed"
      },
      "longTerm": {
        "enabled": false,
        "store": null,
        "connectionRef": null,
        "scope": "user",
        "writePolicy": "explicit"
      },
      "archive": {
        "enabled": false,
        "store": null,
        "connectionRef": null
      }
    },
    "tools": {
      "enabled": true,
      "bindings": [
        {
          "toolId": "get_inventory_status",
          "name": "查询库存状态",
          "description": "当用户询问实时库存、可用数量或仓库分布时调用。",
          "accessMode": "read",
          "endpoint": {
            "apiContractId": "inventory_api",
            "endpointId": "inventory_api.get_status",
            "method": "GET",
            "path": "/api/inventory/status",
            "requestSchemaRef": null,
            "responseSchemaRef": "inventory_api.InventoryStatusResponse"
          },
          "approvalPolicy": "platform_managed"
        }
      ]
    },
    "skills": {
      "enabled": false,
      "loadingPolicy": "explicit_only",
      "bindings": []
    },
    "knowledge": {
      "enabled": false,
      "sources": [],
      "retrieval": {
        "strategy": "semantic",
        "topK": 5,
        "scoreThreshold": 0.7,
        "rerank": false
      },
      "citationPolicy": "disabled"
    },
    "context": {
      "sources": [
        {"type": "conversation", "enabled": true, "trust": "user_input"},
        {"type": "trusted_user_context", "enabled": true, "trust": "gateway_verified"},
        {"type": "tool_results", "enabled": true, "trust": "tool_output"},
        {"type": "knowledge_results", "enabled": false, "trust": "retrieved_content"}
      ],
      "budget": {
        "strategy": "model_window",
        "maxInputRatio": 0.7,
        "reserveOutputRatio": 0.2
      },
      "compression": {
        "strategy": "none",
        "triggerRatio": null,
        "preserveRecentTurns": 8,
        "preserveSystemPrompt": true,
        "preserveToolCallPairs": true
      }
    }
  },
  "invocation": {
    "transport": "ag-ui-sse",
    "gatewayEndpointId": "inventory_api.agent_message",
    "internalPath": "/internal/agents/inventory_assistant/run"
  },
  "runtime": {
    "language": "Python",
    "pythonVersion": "3.12",
    "framework": "DeepAgents",
    "agentFactory": "create_deep_agent",
    "modelFactory": "init_chat_model",
    "deployment": "sidecar",
    "serviceName": "agent-runtime"
  },
  "security": {
    "directClientAccess": false,
    "authForwarding": "scoped-user-context",
    "secretResolution": "environment_reference_only",
    "toolAuthorization": "platform_enforced",
    "highRiskApproval": "platform_managed",
    "platformPromptMode": "locked_prefix"
  },
  "artifacts": {
    "agentPath": "agent-runtime/src/app/agent/inventory_assistant.py",
    "toolAdapterPath": "agent-runtime/src/app/tools/inventory_assistant_tools.py",
    "testPath": "agent-runtime/tests/test_inventory_assistant.py"
  },
  "requiredChecks": [
    "uv run --project agent-runtime python -m py_compile agent-runtime/src/app/agent/inventory_assistant.py agent-runtime/src/app/tools/inventory_assistant_tools.py",
    "uv run --project agent-runtime pytest agent-runtime/tests/test_inventory_assistant.py"
  ],
  "evaluation": {
    "productAcceptanceCriteria": [
      "能够根据当前商品和仓库上下文给出明确库存答复"
    ],
    "engineeringCriteria": [
      "所有 Tool 均解析到已确认 Endpoint",
      "Renderer 只能通过 Java Gateway 调用 Agent Runtime",
      "写操作未经平台批准不得执行"
    ]
  }
}
```

TechnicalPlan 根级继续固定包含 `agent_contracts[]`。多个 Agent 必须与 ProductPlan `agents[]` 一一对应并保持顺序。

## 6. Prompt

固定结构：

```json
{
  "persona": {
    "role": "非空字符串",
    "tone": "非空字符串"
  },
  "systemPrompt": "非空字符串",
  "constraints": ["非空字符串"]
}
```

Prompt 由 ProductPlan 的名称、用途、Capabilities、Interaction、Boundaries 和 Agent 设计模型共同生成。

`agentSettings.prompt.systemPrompt` 是完整业务 System Prompt，但 Runtime 必须在它前面注入不可覆盖的平台安全前缀：

```text
平台不可覆盖安全规则
+ ProductPlan 业务身份和能力
+ Agent Contract 业务 System Prompt
+ ProductPlan Boundaries
+ Tool/Skill/Knowledge 使用规则
+ Interaction 输出要求
= create_deep_agent(system_prompt=...)
```

Prompt 不得包含密钥、连接串、物理路径、未授权 Tool 或绕过审批的指令。

## 7. Model

固定结构：

```json
{
  "selection": "project_default",
  "modelRef": "project_default",
  "requiredCapabilities": {
    "streaming": true,
    "toolCalling": true,
    "structuredOutput": false,
    "vision": false
  },
  "generation": {
    "temperature": 0.2
  }
}
```

规则：

- 当前 Contract 固定使用 `project_default`，DeepSeek、GPT、Claude、Qwen 等由项目配置和 `init_chat_model` 解析；
- Contract 不保存供应商密钥；
- `requiredCapabilities` 表达 Agent 的最低模型要求；
- Tools 非空时 `toolCalling` 必须为 true；
- 平台用模型注册表校验能力，不满足时阻止确认或启动，不能静默降级；
- “是否允许追问”不是模型能力，必须放在 `interaction.clarification`。

## 8. Memory

Memory 分为三层：

| 层级 | 用途 | 允许存储 |
| --- | --- | --- |
| Short-term | 当前 Thread 消息、状态、Tool Call 配对和 checkpoint | SQLite、MySQL |
| Long-term | 跨 Thread 用户偏好、稳定业务记忆或摘要 | MySQL、OSS |
| Archive | 会话快照、长文本和审计归档 | OSS |

固定结构：

```json
{
  "shortTerm": {
    "enabled": true,
    "store": "sqlite",
    "connectionRef": "agent_runtime_checkpoint",
    "scope": "thread",
    "retention": "application_managed"
  },
  "longTerm": {
    "enabled": false,
    "store": null,
    "connectionRef": null,
    "scope": "user",
    "writePolicy": "explicit"
  },
  "archive": {
    "enabled": false,
    "store": null,
    "connectionRef": null
  }
}
```

规则：

- `supportsMultiTurn=true` 时 Short-term 必须启用；
- 本地单实例默认 SQLite；只有明确存在多实例、跨设备或共享会话要求时才能选择 MySQL；
- OSS 不作为 Short-term Store，因为它不直接提供 checkpoint 需要的事务、锁、索引和低延迟更新；
- Long-term 默认关闭，不能因多轮对话自动开启；
- Long-term 写入默认 `explicit`；
- OSS Long-term 只保存可寻址对象或摘要，需要检索时必须存在独立正式索引；
- `connectionRef` 只引用环境配置，不保存地址、账号、密码或 Token；
- Retention、清理、导出和删除必须由平台执行。

## 9. Tools

固定结构：

```json
{
  "enabled": true,
  "bindings": [
    {
      "toolId": "get_inventory_status",
      "name": "查询库存状态",
      "description": "当用户询问实时库存、可用数量或仓库分布时调用。",
      "accessMode": "read",
      "endpoint": {
        "apiContractId": "inventory_api",
        "endpointId": "inventory_api.get_status",
        "method": "GET",
        "path": "/api/inventory/status",
        "requestSchemaRef": null,
        "responseSchemaRef": "inventory_api.InventoryStatusResponse"
      },
      "approvalPolicy": "platform_managed"
    }
  ]
}
```

规则：

- `bindings=[]` 表示 `create_deep_agent(tools=[])`；
- Capability 通过 `toolIds` 绑定零个或多个 Tool；
- `toolId` 在当前 Agent 内唯一且使用 `lower_snake_case`；
- Tool description 必须说明使用时机和业务结果；
- 模型只选择 `endpointId`，Method、Path 和 Schema 由平台从 API Contract 展开；
- Gateway Endpoint 不能注册为 Tool；
- `accessMode` 只允许 `read` 或 `write`，并与 Endpoint 语义一致；
- `write` 不等于自动批准，`approvalPolicy` 固定为 `platform_managed`；
- Tool Adapter 只调用 Java Backend，不允许绕过 Java 直接访问数据库、外部 API 或宿主网络。

## 10. Skills

固定结构：

```json
{
  "enabled": true,
  "loadingPolicy": "explicit_only",
  "bindings": [
    {
      "skillId": "inventory-analysis",
      "revision": "sha256:...",
      "purpose": "提供库存分析步骤和结果表达规范"
    }
  ]
}
```

规则：

- Skill 必须来自当前应用已批准的 Skill Catalog；
- 使用稳定 `skillId` 和不可变 revision/hash，不保存任意物理路径；
- Skill purpose 必须映射到 ProductPlan Capability；
- Skill 按不可信输入处理，不能扩展未授权的 Tool、Knowledge、网络、文件或命令权限；
- Runtime 只加载 Contract 明确绑定的 Skill；
- 当前 Agent Runtime 尚无正式 Skill Loader 时，必须保持 `enabled=false` 和空数组。

## 11. Knowledge

固定结构：

```json
{
  "enabled": true,
  "sources": [
    {
      "knowledgeSourceId": "inventory-policy",
      "collectionIds": ["inventory-rules"],
      "accessMode": "read"
    }
  ],
  "retrieval": {
    "strategy": "semantic",
    "topK": 5,
    "scoreThreshold": 0.7,
    "rerank": false
  },
  "citationPolicy": "required"
}
```

规则：

- Knowledge Source 必须是独立正式 Artifact 或已批准目录中的稳定 ID；
- 不接受文件路径、URL、Bucket 名或自由文本作为知识源引用；
- Source 必须有 revision/hash、访问控制、状态和可用性证据；
- ProductPlan 没有知识需求时默认关闭；
- `enabled=false` 时 `sources=[]` 且 `citationPolicy=disabled`；
- Citation Policy 只允许 `disabled`、`when_available`、`required`；
- 检索结果属于不可信上下文，不能改变安全规则；
- Knowledge 权限必须与当前用户权限取交集；
- 当前没有正式 Knowledge Artifact 或 Runtime Retriever 时必须关闭。

## 12. Context

固定结构：

```json
{
  "sources": [
    {"type": "conversation", "enabled": true, "trust": "user_input"},
    {"type": "trusted_user_context", "enabled": true, "trust": "gateway_verified"},
    {"type": "tool_results", "enabled": true, "trust": "tool_output"},
    {"type": "knowledge_results", "enabled": false, "trust": "retrieved_content"}
  ],
  "budget": {
    "strategy": "model_window",
    "maxInputRatio": 0.7,
    "reserveOutputRatio": 0.2
  },
  "compression": {
    "strategy": "none",
    "triggerRatio": null,
    "preserveRecentTurns": 8,
    "preserveSystemPrompt": true,
    "preserveToolCallPairs": true
  }
}
```

规则：

- Context Source 使用固定枚举，禁止自由路径或任意读取器；
- `trusted_user_context` 只能由 Java Gateway 注入，Renderer 不能伪造；
- Tool 和 Knowledge Result 即使来自受控来源也按不可信内容处理；
- Budget 根据解析后的模型 Context Window 计算；
- Compression 只允许 `none`、`sliding_window`、`summary`；
- ProductPlan、System Prompt、安全规则、未完成 Tool Call 配对和审批状态不能被压缩丢弃；
- `preserveSystemPrompt` 和 `preserveToolCallPairs` 固定为 true；
- 压缩摘要不能自动写入 Long-term Memory。

## 13. Interaction 与追问

追问是 Agent 行为，不是模型供应商能力：

```json
{
  "clarification": {
    "allowed": true,
    "trigger": "missing_required_context",
    "maxRounds": 3
  }
}
```

- `supportsMultiTurn=false` 时不得配置依赖后续回答的多轮澄清；
- `allowed=true` 时通过正常 AG-UI Assistant Message 请求补充信息，并使用同一 Thread 继续；
- 澄清不能替代写操作审批；
- 达到最大轮次后明确说明缺失信息并停止，不猜测业务事实。

## 14. Runtime、Invocation 与 Security

这些字段进入完整 Contract，但由平台固定生成。

Runtime：

```json
{
  "language": "Python",
  "pythonVersion": "3.12",
  "framework": "DeepAgents",
  "agentFactory": "create_deep_agent",
  "modelFactory": "init_chat_model",
  "deployment": "sidecar",
  "serviceName": "agent-runtime"
}
```

Invocation：

```json
{
  "transport": "ag-ui-sse",
  "gatewayEndpointId": "inventory_api.agent_message",
  "internalPath": "/internal/agents/inventory_assistant/run"
}
```

Security：

```json
{
  "directClientAccess": false,
  "authForwarding": "scoped-user-context",
  "secretResolution": "environment_reference_only",
  "toolAuthorization": "platform_enforced",
  "highRiskApproval": "platform_managed",
  "platformPromptMode": "locked_prefix"
}
```

Renderer 只能调用 Java Gateway。真实密钥、数据库密码、OSS Token、模型 Token、内部服务凭据和用户敏感信息不得进入 Contract、Markdown、模型上下文或默认日志。

## 15. Artifacts 与 DeepAgents 映射

路径由 `lower_snake_case agentId` 确定性生成：

```json
{
  "agentPath": "agent-runtime/src/app/agent/<agentId>.py",
  "toolAdapterPath": "agent-runtime/src/app/tools/<agentId>_tools.py",
  "testPath": "agent-runtime/tests/test_<agentId>.py"
}
```

生成代码必须等价于：

```python
llm = init_chat_model(model=resolved_project_model)

agent = create_deep_agent(
    model=llm,
    tools=resolved_tools,
    system_prompt=compiled_system_prompt,
    checkpointer=resolved_short_term_memory,
)
```

该片段只说明目标映射，实际参数以 Agent Runtime 模板公开构造入口为准。

每个 Agent 至少执行：

```text
uv run --project agent-runtime python -m py_compile <agentPath> <toolAdapterPath>
uv run --project agent-runtime pytest <testPath>
```

若 Tools、Skills、Knowledge、MySQL Memory、OSS 或 Compression 启用，平台必须追加对应的契约、权限、启动和集成检查。

## 16. 确定性校验

### 16.1 ProductPlan 闭合

- Contract 数量、Agent ID 和顺序与 ProductPlan 完全一致；
- Identity、Capability、Interaction、Boundary 和产品验收标准与 ProductPlan 投影一致；
- Capability 的 Tool ID 只引用当前 Contract Tool；
- 页面 Agent action 全部映射到同一 Gateway Endpoint；
- ProductPlan Hash 不一致时 Contract 立即失效。

### 16.2 Model 与 Memory

- 项目模型能被 `init_chat_model` 解析并满足 Required Capabilities；
- Tools 非空时模型必须支持 Tool Calling；
- Short-term Store 只能是 SQLite 或 MySQL；
- MySQL/OSS 只保存连接引用，不保存凭据；
- Long-term/Archive 启用时必须存在连接、权限、Retention 和删除策略；
- OSS 不能作为 Short-term Checkpointer。

### 16.3 Tools、Skills、Knowledge 与 Context

- Tool Endpoint 唯一存在且 Schema 引用闭合；
- Access Mode 与 Endpoint 语义一致；
- Skill ID、revision 和状态可解析；
- Knowledge Source ID、revision、权限和状态可解析；
- Skill/Knowledge 不得扩大 Tool 权限；
- Context Source、Budget 和 Compression 策略受支持；
- System Prompt、安全规则和 Tool Call 配对始终保留；
- 未实现 Runtime Adapter 的能力必须保持关闭。

### 16.4 Runtime 与安全

- Runtime、Invocation、Security、Artifacts 和 Checks 与平台编译结果完全一致；
- Client 不能直连 sidecar；
- Gateway Endpoint 不能同时作为 Tool；
- 写操作和高风险操作必须进入审批；
- Contract 中不得出现密钥、连接串、宿主绝对路径或未知字段。

## 17. 用户确认与 Markdown

Agent Contract 仍属于 TechnicalPlan，不新增平行工作流节点。

TechnicalPlan Markdown 的“智能体契约”章节依次展示：

1. Agent 身份、用途和 ProductPlan 来源；
2. Capabilities、入口页面和 Interaction；
3. Prompt、Model、Memory、Tools、Skills、Knowledge、Context；
4. Gateway、Runtime 和 Security；
5. Artifacts、Required checks 和 Evaluation。

界面必须区分 ProductPlan 投影、可编辑 AgentSettings、平台只读配置和部署环境引用。

用户编辑 Markdown 后，服务端只同步允许编辑的 AgentSettings 候选；随后重新从 ProductPlan、API Contract 和平台策略编译完整 JSON，再展示 Diff 并等待确认。不得直接覆盖平台安全字段。

## 18. 失效规则

以下任一变化使 Agent Contract 失效：

- ProductPlan Agent、Capability、Interaction、页面绑定、Boundary 或验收标准变化；
- Gateway 或 Tool Endpoint 变化；
- Model Capability 要求变化；
- Memory Store 或连接引用变化；
- Skill revision 变化；
- Knowledge Source revision、权限或检索策略变化；
- Context/Compression 策略变化；
- Runtime 模板公开能力变化；
- 用户修改 Prompt 或其他可编辑 AgentSettings。

失效后必须重新生成并确认，同时废弃依赖旧 Contract 的 Build DAG、代码候选、测试报告、Launch 证据和验收结论。

## 19. 当前能力边界

当前 Agent Runtime 模板已具备：

- `init_chat_model`；
- `create_deep_agent`；
- Tools；
- SQLite checkpoint；
- AG-UI SSE；
- Thread 级恢复；
- Java Gateway 可信上下文边界。

以下能力当前如无正式实现，Contract 必须显式关闭：

- MySQL Checkpointer；
- OSS Archive/Long-term Memory；
- Long-term Memory 检索和写入；
- Runtime Skill Loader；
- Knowledge Retriever/Reranker；
- Context Summary Compressor；
- 单 Agent 模型覆盖；
- Vision 和自定义结构化最终输出。

设计完整不代表可以把未实现能力伪装成 Enabled。实现某项能力时必须同步加入 Runtime Adapter、配置解析、权限、健康检查、测试和 UI 状态。

## 20. 实施拆分与当前进度

### 第一批：Contract Schema 与规划（已实施）

- 修改 TechnicalPlan Prompt、示例和 Contract-only 修复 Prompt；
- 重写 `project_plan.py` 的候选校验与完整 Contract 编译；
- 更新跨 Artifact 校验、Markdown 渲染与回写；
- 更新 Backend 契约测试。

### 第二批：当前 Runtime 能力（模板基础已存在，业务生成联调待完成）

- Prompt Compiler；
- Project-default Model Resolver；
- SQLite Short-term Memory；
- Tool Adapter；
- Context Source 和基础 Budget；
- DeepAgents 构造入口；
- Required checks。

### 第三批：工作台与 Build（阅读界面和 Build 输入已实施，其余待完成）

- 七类 AgentSettings 阅读界面；
- Build DAG 上下文；
- Java Gateway 与 Frontend Agent 入口；
- Testing、Review、Launch 和 Acceptance 证据。

### 后续独立批次

- MySQL Short-term Memory；
- OSS Archive；
- Long-term Memory；
- Skills；
- Knowledge/RAG；
- Context Compression；
- 单 Agent Model Override。

后续能力必须先完成 Runtime 和权限实现，再允许 Contract 将它设为 Enabled。

## 21. 已确认决策

2026-09-06 用户已确认：

1. ProductPlan 是业务语义唯一权威，Agent Contract 保存带 Hash 的完整派生快照；
2. 正式 Contract 包含 Identity、Capabilities、Interaction、七类 AgentSettings、Invocation、Runtime、Security、Artifacts、Checks 和 Evaluation；
3. `agentSettings` 固定包含 Prompt、Model、Memory、Tools、Skills、Knowledge、Context；
4. 追问策略属于 Interaction，不属于 Model Capability；
5. Short-term Memory 只允许 SQLite/MySQL，OSS 只用于 Long-term 或 Archive；
6. Skill 和 Knowledge 必须引用正式 Catalog/Artifact，不能使用自由路径；
7. 平台安全前缀、权限和审批不能被 System Prompt 覆盖；
8. 正式 Contract 由平台编译，模型候选不能直接进入 Build；
9. 当前未实现的设置必须显式关闭；
10. 按批次实施，不在一次修改中同时引入全部 Runtime 能力。

当前代码已经完成模型候选与正式 Contract 分离、ProductPlan Hash、七段 `agentSettings`、Endpoint 快照展开、平台派生字段重编译校验、Markdown/前端阅读以及 Build Tool 依赖消费。MySQL、OSS、Long-term Memory、Skill Loader、Knowledge Retriever、Summary Compression、Vision、结构化最终输出和单 Agent 模型覆盖仍未实现，也不得在 Contract 中启用。
