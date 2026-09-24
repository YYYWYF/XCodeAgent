# Direct 接口与页面代码生成

API 契约确认后，用户还需启动接口代码开发。页面开发使用同一主 Workflow，并带入页面依赖的接口。Build 会同时检查目标所需的正式 API 契约和关联实体 SQL。

```mermaid
flowchart TD
    S{用户选择开发目标}
    S -->|接口| EP[点击开始开发接口代码<br/>Scope：当前 Contract 与 Endpoint]
    S -->|页面| PG[点击开发页面<br/>Scope：当前 Page]

    EP --> R[进入主 Workflow 的开发就绪检查]
    PG --> R
    R --> C[装载与当前 TechnicalPlan 哈希匹配的<br/>已确认 API 契约，投射运行时 Schema]
    C --> G{目标需要的 API 契约<br/>与关联实体 SQL 都已确认？}
    G -->|否| STOP[停止任务拆解<br/>提示回开发产物页确认缺失项]
    G -->|是| U{解析目标 Build Unit 与依赖}

    U -->|接口| UE[当前 Python Endpoint<br/>所属 Python Service<br/>关联 Entity、Migration、Repository]
    U -->|页面| UP[当前 Page、Frontend Shell、API Client<br/>页面引用的 Endpoint 及其 Python 依赖]
    UE --> PLAN[生成任务计划和文件范围<br/>等待用户确认 Build DAG]
    UP --> PLAN
    PLAN --> BUILD[按 Unit 依赖执行 Build]
    BUILD --> PY[Python 生成器写入生成项目的 agent-runtime<br/>Entity、Repository、Service、DTO、FastAPI Endpoint]
    BUILD --> TYPE{当前 Scope 包含页面？}
    TYPE -->|是| FE[Frontend 生成器写入<br/>API Client 和页面代码]
    TYPE -->|否| CHECK[校验代码交付物<br/>执行单测门禁]
    PY --> CHECK
    FE --> CHECK
    CHECK -->|失败| FAIL[进入失败或修复流程<br/>当前目标不计为完成]
    CHECK -->|通过或按流程明确跳过| DONE[提交当前接口或页面的<br/>initialDevelopmentStatus = completed]
    DONE --> COUNT[开发产物目录中<br/>对应接口或页面计数增加]
```

保存 API 契约只确定 DTO 的字段来源，不会自动写出 DTO、Service 或 Endpoint。代码文件位于生成项目的 `agent-runtime/`；XCodeAgent 自身的 `Backend/app/` 是生成流程的实现，不是生成项目的业务代码目录。接口和页面的具体目标文件由已确认的 Build 任务限定。
