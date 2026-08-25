---
name: backend-code-scan
description: 只读扫描 backend/src/main/java 下的 Java 代码，识别架构红线问题并输出审查结果，不修改源码。
---

# 后端代码审查技能

## 工作模式

只允许扫描用户工作区的 `backend/src/main/java/**/*.java`。本技能只识别、分类和报告问题，禁止写入源码、执行修复、运行编译或执行其他命令。

详细检测关键字、误报排除条件和规则说明见 `references/rules-reference.md`。

## 核心规则

| 规则 ID | 名称 |
| --- | --- |
| CKR1104 | Kafka 降级 |
| CKR2002 | 事务中发送 Kafka |
| CKR6000 | HttpClient 超时 |
| CKR6002 | HttpURLConnection 超时 |
| CKR6004 | OkHttp 超时 |
| CKR4003 | Redis 降级 |
| CKR5000 | CallerRunsPolicy |
| CKR7019 | SQLException 北斗错误码 |

## 输出要求

- 每个问题包含规则 ID、严重级别、相对文件路径、可选行号、标题和简短说明。
- 不输出宿主机绝对路径、源码全文、密钥或内部推理。
- 后续“修复”按钮可能使用本技能中的规则说明，但本次扫描调用不得修改任何文件。
