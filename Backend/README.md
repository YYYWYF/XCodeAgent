# Local LangGraph Agent

一个 `Python + FastAPI + LangGraph` 本地后端。它按 `APP_ENV` 读取对应环境文件中的模型 Provider 配置,通过 `MODEL_TRANSPORT=direct/transmit` 选择模型直连或 AG-UI 转发，共用业务代码，并通过 `/workflow/run` 暴露 AG-UI 流式接口。默认直连，修改配置后重启后端。配置及边界见 [模型接入说明](../docs/MODEL_TRANSPORT.md)。

当前后端还内置了：

- `requirement_planner` 工具：分析用户需求，生成选择题式澄清问题，并在信息足够后输出结构化开发计划。
- `development_orchestrator` 工具：把需求澄清、统一 SDD、功能切片计划、任务 DAG、并行批次和验证计划串成一套开发编排流程。
- 本地工作区工具：给 Electron 前端和 agent 调用的文件、搜索、命令和 Git 工具，所有路径都限制在 `workspace_root` 内。

## 启动

先在 `Backend` 目录创建虚拟环境并安装依赖（推荐 Python 3.12）：

```bash
# windows bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
APP_ENV=dev uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# macOS
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
APP_ENV=dev uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt 
$env:APP_ENV = "dev"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 # 使用.env开发环境配置启动后端
```

创建所需环境配置后，推荐从**项目根目录**使用脚本启动。参数优先于进程 `APP_ENV`；均未设置时默认为 dev：

```bash
bash scripts/start-backend.sh                 # dev，读取 Backend/.env
bash scripts/start-backend.sh --env st        # 读取 Backend/.env.st
bash scripts/start-backend.sh --env uat       # 读取 Backend/.env.uat
bash scripts/start-backend.sh --env prd       # 读取 Backend/.env.prd
```

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-backend.ps1 -Env st
```

也可以在已激活虚拟环境的 **Backend 目录**直接启动，未设置 `APP_ENV` 时默认 dev：

```bash
APP_ENV=st python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```powershell
$env:APP_ENV = "st"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端分别使用 `pnpm dev`、`pnpm dev:st`、`pnpm dev:uat`、`pnpm dev:prd`，必须与后端一致。
`/health.app_env` 返回后端实际环境；Electron 在启动更新检查前核对，错配时拒绝继续启动。

Workflow 调试入口在前端 Chat Composer 的“Workflow 调试”面板中，提供主工作流可调试节点，并可传入已落盘的 RequirementSpec、ProjectPlan、WorkspaceSnapshot 或 BuildTaskPlan 路径。选择 `launch_project` 会进入验收子图并强制重跑项目启动；审查和最终验收确认仍通过各自的结构化确认动作提交。

服务启动后：

- 健康检查：http://127.0.0.1:8000/health
- Swagger 文档：http://127.0.0.1:8000/docs
- AG-UI 流式接口：http://127.0.0.1:8000/workflow/run
- 需求规划工具：http://127.0.0.1:8000/tools/requirement-planner
- 开发编排器：http://127.0.0.1:8000/tools/development-orchestrator
- 工作区工具能力：http://127.0.0.1:8000/tools/workspace/capabilities

## 调用示例

```bash
curl -N -X POST http://127.0.0.1:8000/workflow/run \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"threadId":"demo-thread","messages":[{"role":"user","content":"创建一个库存管理应用"}],"forwardedProps":{"selectedSkillNames":["inventory-domain"]}}'
```

`selectedSkillNames` 为可选字符串数组。非空时，Backend 会验证并只挂载这些已开启的用户 Skill，完整读取每个 `SKILL.md` 后强制注入 Frontend、Data Source、Test、RepairPlanner 四个 Deep Agent；空数组或字段缺失时，只有已开启用户 Skill 可按需发现，正文不强制注入。用户技能默认开启，关闭项按环境持久化在 `~/.devagentstudio[_dev|_st|_uat]/skill-settings.json`；关闭的技能不能被显式选择，并从下一次 Agent bundle 创建或后续运行开始失效。所选正文总量上限为 64 KiB，恢复中的 Workflow 不允许替换最初的技能集合。

后续请求复用同一个 `threadId`，服务会用 LangGraph checkpointer 延续该主工作流。

需求规划工具可以直连调用，也可以通过 AG-UI 的 `forwardedProps.agentMode=requirement-planner` 使用：

```bash
curl -X POST http://127.0.0.1:8000/tools/requirement-planner \
  -H 'Content-Type: application/json' \
  -d '{"message":"我要做一个客户管理后台，需要客户列表和数据看板","action":"start"}'
```

返回中的 `state` 需要在下一轮作为 `planner_state` 传回；当用户完成选择后，传 `action: "finalize"` 会生成最终 `plan`。

开发编排器可以通过 `action: "start" | "answer" | "finalize" | "dispatch" | "verify"` 使用。它不会把页面和 API 拆成两套流程，而是按业务功能切片生成统一计划：每个 feature 同时包含 UI、API、数据模型、验收标准和验证方式。信息不足时会复用 `requirement_planner` 输出问题；信息足够后会返回：

- `plan.sdd`：Spec + Design。
- `plan.features`：统一功能切片。
- `taskGraph.tasks`：可执行任务 DAG。
- `executionBatches`：按依赖和 `targetFiles` 冲突计算出的串行/并行批次。
- `verification`：验证状态；传 `action: "verify"` 时会按 `verificationPlan.commands` 执行安全命令。

直连调用示例：

```bash
curl -X POST http://127.0.0.1:8000/tools/development-orchestrator \
  -H 'Content-Type: application/json' \
  -d '{"message":"我要做一个客户管理后台，需要列表、筛选、详情和客户接口","action":"finalize"}'
```

AG-UI 使用时传：

```json
{
  "agentMode": "development-orchestrator",
  "orchestratorAction": "finalize",
  "workspaceRoot": "/Users/yifei/Documents/example-workspace"
}
```

## 本地工作区工具

Electron 前端选择一个目录后，把绝对路径作为 `workspace_root` 传给工具接口。若请求里不传，则使用环境变量 `DEVAGENTSTUDIO_WORKSPACE_ROOT`，再退回到后端当前工作目录。

当前工具：

- `workspace.info`：`POST /tools/workspace/info`
- `workspace.list_files`：`POST /tools/workspace/list-files`
- `workspace.tree`：`POST /tools/workspace/tree`
- `file.read`：`POST /tools/file/read`
- `file.write`：`POST /tools/file/write`
- `file.patch`：`POST /tools/file/patch`
- `search.files`：`POST /tools/search/files`
- `search.text`：`POST /tools/search/text`
- `terminal.exec`：`POST /tools/terminal/exec`
- `git.status`：`POST /tools/git/status`
- `git.diff`：`POST /tools/git/diff`

读文件示例：

```bash
curl -X POST http://127.0.0.1:8000/tools/file/read \
  -H 'Content-Type: application/json' \
  -d '{"workspace_root":"/Users/yifei/Documents/DevAgentStudioBack","path":"app/main.py","max_lines":80}'
```

精确 patch 示例，建议 agent 优先用这个而不是整文件覆盖：

```bash
curl -X POST http://127.0.0.1:8000/tools/file/patch \
  -H 'Content-Type: application/json' \
  -d '{"workspace_root":"/Users/yifei/Documents/DevAgentStudioBack","path":"README.md","dry_run":true,"edits":[{"old_text":"Local LangGraph Agent","new_text":"Local DevAgent Studio Backend"}]}'
```

执行命令示例：

```bash
curl -X POST http://127.0.0.1:8000/tools/terminal/exec \
  -H 'Content-Type: application/json' \
  -d '{"workspace_root":"/Users/yifei/Documents/DevAgentStudioBack","argv":["python3","--version"]}'
```

`terminal.exec` 不走 shell，只接收 `argv` 或用 `shlex` 拆分 `command`。`rm`、`sudo`、`git reset`、`git clean`、包管理安装等中高风险命令会先返回 `requires_approval: true` 和审批 id；前端确认后调用 `/tools/approvals/{id}/approve` 取得一次性 token，再把 token 放到 `approval` 字段重试。

## 配置

| APP_ENV | 实际文件 | 脱敏模板 | 用户数据目录 |
| --- | --- | --- | --- |
| dev | `.env` | `.env.example` | `~/.devagentstudio_dev` |
| st | `.env.st` | `.env.st.example` | `~/.devagentstudio_st` |
| uat | `.env.uat` | `.env.uat.example` | `~/.devagentstudio_uat` |
| prd | `.env.prd` | `.env.prd.example` | `~/.devagentstudio` |

复制对应模板并填写实际模型、服务地址和凭据。例如 `cp .env.st.example .env.st`。
实际文件均被 Git 忽略，必须由开发机或公司 CI 提供；不要提交、打印或分享真实凭据。

每次仅加载选中的文件，不叠加 `.env`，不搜索父目录。环境非法或文件缺失时立即报错。
文件中声明的业务配置优先于系统环境变量；未声明的项仍使用系统变量及现有代码默认值。
`APP_ENV`、`DEVAGENTSTUDIO_BACKEND_ENV_FILE`、`DEVAGENTSTUDIO_BACKEND_HOST/PORT`、
`DEVAGENTSTUDIO_WORKING_DIR`、`PYTHONUTF8` 属于启动控制项，文件内设置不生效。
工作目录由所选环境固定映射。需要自定义文件位置时，在启动进程设置
`DEVAGENTSTUDIO_BACKEND_ENV_FILE`（推荐绝对路径），此时只加载该文件。

完整打包命令和环境参数见 [跨平台启动与打包](../docs/CROSS_PLATFORM_SUPPORT.md)。

OpenAI-compatible 服务示例：

```dotenv
MODEL_PROVIDER=openai
MODEL_BASE_URL=https://api.moonshot.cn/v1
MODEL_API_KEY=replace-with-your-token
MODEL_NAME=moonshot-v1-8k
```

也可以使用 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `OPENAI_MODEL` 作为 `MODEL_*` 的兼容别名。`MODEL_PROVIDER` 只支持 `openai` 或 `openai-compatible`，后者会被归一化为 `openai`。

可选覆盖项：

```dotenv
AGENT_SYSTEM_PROMPT="You are a helpful local agent."
AGENT_TEMPERATURE=0.2
AGENT_MAX_TOKENS=2048
UI_DESIGN_MAX_TOKENS=4096
UI_DESIGN_MAX_RETRIES=2
MODEL_TRUST_ENV=false
MODEL_OUTPUT_LOG_ENABLED=false
DEVAGENTSTUDIO_WORKSPACE_ROOT=/Users/yifei/Documents/example-workspace
DEVAGENTSTUDIO_CHECKPOINT_RETENTION_DAYS=30
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=replace-with-your-langsmith-key
LANGSMITH_PROJECT=devagentstudio-workflow
```

`UI_DESIGN_MAX_TOKENS` 控制单个 UI 设计稿模型调用的最大输出 token 数；`UI_DESIGN_MAX_RETRIES` 控制设计稿代码校验失败后的最大自动修复次数。

`MODEL_OUTPUT_LOG_ENABLED=true` 会在模型生成时把文本输出流式打印到后端控制台，并在调用结束后打印工具调用概要，便于调试。

workflow checkpoint 默认写入当前工作区的 `.devagentstudio/checkpoints/checkpoints.sqlite`，用于持久化主 workflow 的 `ProjectState`，支持后端重启后的状态恢复。`DEVAGENTSTUDIO_CHECKPOINT_DB` 可选用于强制覆盖 SQLite checkpoint 数据库位置；设置后所有 workflow 会共享该数据库。`DEVAGENTSTUDIO_CHECKPOINT_RETENTION_DAYS` 控制旧 checkpoint 的默认保留天数，默认 30 天，每个 thread 至少保留最新 checkpoint，等待用户输入的 thread 不会被自动清理。

`LANGSMITH_TRACING` 未配置或为空时默认关闭，不会影响后端启动；只有显式设置为 `true`、`1`、`yes` 或 `on` 时才会启用 LangSmith tracing。主 workflow 会向 LangGraph runnable config 注入 `run_id`、`thread_id`、`project_id`、`workspace` 等 metadata，并在桌面端 Workflow Run 卡片中显示 LangSmith 状态和跳转入口。非 US 区域账号还需要按 LangSmith 要求配置 `LANGSMITH_ENDPOINT`。
