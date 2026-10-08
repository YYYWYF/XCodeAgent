# 截图生成需求文档集成说明

## 目标与边界

本副本将 `recognizeScreenshotAgent` 作为 XCodeAgent 的内部需求分析器嵌入创建规划流程。集成只替换 `requirements` 节点中的需求生成来源，不新增旁路工作流，也不改变 RequirementSpec、ProductPlan、UI Design、TechnicalPlan 的原有确认顺序。

文字模式仍调用原 `requirements_analyzer`；截图模式调用 `Backend/app/agents/screenshot_requirements/`。两种分析器返回相同的内部结果结构，后续都由原 `requirements` 节点完成校验、草稿写入和用户确认。

```text
新建应用表单
  ├─ 文字描述 ────────────────┐
  └─ 上传截图 → Electron 校验/复制 ─┤
                                  ↓
                    AG-UI requirementInput
                                  ↓
                     requirements 原节点
                       ├─ text: 原分析器
                       └─ screenshot: 截图识别 Agent
                                  ↓
              原校验 → 自动覆盖草稿 JSON/Markdown → 原确认门禁
                                  ↓
                         原后续规划流程
```

## 前端与传输契约

新建应用界面新增“文字描述 / 上传截图”选择。截图模式支持一次选择最多 10 张 PNG、JPEG 或 WebP，每张不超过 15 MB。

Renderer 不读取图片正文，也不向 AG-UI 发送 base64。Electron 主进程按真实文件头、普通文件属性、大小和 SHA-256 校验图片，然后复制到新应用工作区：

```text
.xcodeagent/inputs/screenshots/<batch-id>/<index>-<sha-prefix>.<ext>
```

同一批次的当前需求输入清单独立写入：

```text
.xcodeagent/inputs/screenshots/requirement-input.json
```

应用列表恢复时，Electron 会重新检查清单、路径边界、普通文件属性、格式、大小和摘要，再把 `requirementInput` 组装到运行时应用视图。该字段不会写入 `.xcodeagent/application.json`；后者仍只保存应用配置。

AG-UI 的 `forwardedProps.requirementInput` 只发送工作区相对路径和不可变清单：

```json
{
  "mode": "screenshot",
  "screenshots": [
    {
      "relativePath": ".xcodeagent/inputs/screenshots/<batch-id>/01-<sha>.png",
      "name": "home.png",
      "mimeType": "image/png",
      "size": 12345,
      "sha256": "<64-character-lowercase-hex>"
    }
  ]
}
```

后端在模型调用前重新校验路径必须位于该工作区的截图输入目录、文件不能是符号链接，且大小和摘要必须与清单一致。

## 自动替换语义

截图识别 Agent 不直接操作 `requirement.json`。它返回现有 `requirements` 节点消费的 `requirement_spec + clarification + authorization_config_conflict` 结构；原节点验证成功后调用原有草稿写入方法，以新候选覆盖：

- `.xcodeagent/drafts/specs/requirement-spec.json`
- `.xcodeagent/drafts/specs/requirement-spec.md`

因此不再需要手工替换 JSON，同时保留“待确认草稿 → 用户确认 → 正式文档”的原流程。正式文件仍只在用户确认后写入 `.xcodeagent/specs/`。

## 视觉识别策略

嵌入 Agent 保留原项目的 EXIF 方向修正、RGB 转换、轻量增强、低分辨率放大、小角度纠偏、四方向参考图和长截图切片。模型输出使用严格 Pydantic/JSON Schema 契约，并适配为 XCodeAgent 当前 RequirementSpec：

- 识别应用信息、参与者、功能模块、页面、需求阶段实体、业务流程和产品验收标准。
- 截图文字只作为界面数据，不能成为模型指令。
- 模型把可见信息、控件、流程步骤等字符串数组误写为带标签/说明的对象时，在模型输出边界无损合并为单条文字；未知对象仍由严格 Schema 拦截。其余 JSON 结构错误最多发起一次纯文本修复，不重复上传截图。
- 不根据截图猜测认证、权限规则、数据库、接口或后端实现。
- 未决维度转换为原有澄清问题；无未决项时进入原需求文档确认卡片。
- 权限开关以创建表单的明确配置为准，具体授权规则继续由原 XCodeAgent 流程处理。

## 配置

截图模型默认复用 XCodeAgent 的 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`、超时和代理策略。主模型不支持图片时可配置：

```dotenv
XCODEAGENT_SCREENSHOT_BASE_URL=https://api.openai.com/v1
XCODEAGENT_SCREENSHOT_API_KEY=replace-with-your-vision-token
XCODEAGENT_SCREENSHOT_MODEL_NAME=gpt-4.1-mini
XCODEAGENT_SCREENSHOT_RESPONSE_FORMAT=auto
XCODEAGENT_SCREENSHOT_MAX_OUTPUT_TOKENS=16000
```

地址、密钥和模型名必须同时配置或同时留空，避免把主模型凭据发送到不匹配的截图服务；三项同时留空时完整复用主模型配置。主模型使用 Anthropic 协议时必须显式配置一组独立的 OpenAI-compatible 截图模型。`auto` 对 DeepSeek 地址使用 `json_object`，其他 OpenAI 兼容服务使用严格 `json_schema`。所选模型必须支持图片输入。

## 本地验证

在项目根目录执行：

```powershell
cd Backend
python -m unittest tests.test_screenshot_requirements
python -m compileall app

cd ..\Frontend
pnpm install
pnpm typecheck
pnpm test:requirement-screenshots
pnpm test:application-authorization
pnpm test:application-planning-runtime
pnpm build
```

自动化测试覆盖图片预处理、协议校验、AG-UI 状态传递、分析器契约，以及截图结果自动覆盖已有 RequirementSpec 草稿。

## 数据保留

原始截图副本与当前清单保留在目标应用的 `.xcodeagent/inputs/screenshots/` 中，便于应用列表恢复、checkpoint 恢复和需求修订重放。它们可能包含业务敏感信息；共享或归档工作区前应按组织策略清理该目录。
