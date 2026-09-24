# Direct 实体 SQL 与 API 契约确认

本图描述 `agent_runtime_direct` 当前的开发产物页。实体 SQL 与 API 契约可分别确认；保存 API 契约不要求实体 SQL 已确认。

```mermaid
flowchart TD
    T[已确认的 TechnicalPlan] --> E1
    T --> A1

    subgraph E[实体页：建表 SQL]
        E1[选择实体] --> E2[按实体 ID 读取正式字段]
        E2 --> E3[确定性生成 SQLite CREATE TABLE SQL<br/>加入 id 与 owner_id 列]
        E3 --> E4[在隔离的内存 SQLite 库执行<br/>核对实际列与正式字段]
        E4 -->|失败| EERR[显示错误；实体仍未完成]
        E4 -->|成功| E5[展示字段和 SQL，供用户审阅或复制]
        E5 --> E6[用户点击确认实体 SQL]
        E6 --> E7{预览 SQL 摘要<br/>仍与当前结果一致？}
        E7 -->|否| EERR
        E7 -->|是| E8[保存确认 JSON、Markdown<br/>和版本化 migration SQL]
        E8 --> E9[刷新 ApplicationLifecycle 开发产物状态]
        E9 --> E10[实体状态 completed<br/>实体计数增加]
    end

    subgraph A[API 页：请求与响应契约]
        A1[选择 Contract 和 Endpoint] --> A2[读取方法、路径、初始 Schema<br/>及当前 TechnicalPlan 哈希]
        A2 --> A3{是否调用模型生成草稿？}
        A3 -->|是| A4[参考接口语义和关联实体字段<br/>生成可编辑的出入参草稿]
        A3 -->|否| A5[编辑当前草稿]
        A4 --> A5
        A5 --> A6[用户点击保存并确认契约]
        A6 --> A7{校验 JSON Schema、字段类型、引用、<br/>Endpoint 身份与上游哈希}
        A7 -->|失败| AERR[显示错误；继续编辑]
        AERR --> A5
        A7 -->|通过| A8[保存正式契约 JSON 和 Markdown<br/>记录 basedOn TechnicalPlan 哈希]
        A8 --> A9[契约状态 confirmed<br/>接口代码仍未完成]
    end
```

实体 migration 保存到生成项目的 `agent-runtime/src/app/infrastructure/migrations/sql/`。SQL 只在临时库校验，不会在目标业务库自动执行。TechnicalPlan 变化后，旧 SQL 或 API 契约须重新核对；旧确认不能继续作为当前 Build 输入。
