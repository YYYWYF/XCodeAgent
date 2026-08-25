---
name: high-arch-redline-fix
description: 架构红线高等级问题识别与修复工具。自动扫描Java项目代码识别8条高优先级规则问题（CKR1104/CKR2002/CKR6000/CKR6002/CKR6004/CKR4003/CKR5000/CKR7019），修复代码，验证编译，生成审计报告。Use when 用户要求审计架构红线高等级问题、修复架构红线高等级问题等所有涉及架构高等级红线问题识别修复的场景。
---

# 架构红线审计技能

> 详细检测逻辑、修复代码模板、误报排除规则见 `references/rules-reference.md`

## 工作模式
1. **扫描模式**：自动扫描 `backend/src/main/java/**/*.java` 识别问题

## 核心规则（8条）

| 规则ID | 名称 | 修复方式 |
|--------|------|----------|
| CKR1104 | Kafka降级 | try-catch + log + throw |
| CKR2002 | 事务中发Kafka | 事务与消息分离 |
| CKR6000 | HttpClient超时 | 添加超时配置 |
| CKR6002 | HttpURLConnection超时 | 添加超时配置 |
| CKR6004 | OkHttp超时 | 添加超时配置 |
| CKR4003 | Redis降级 | try-catch + log + throw |
| CKR5000 | CallerRunsPolicy | 改为AbortPolicy |
| CKR7019 | SQLException北斗错误码 | 全局异常处理器设置X-B3-ReturnCode + 局部catch |

---

## 工作流程

### 步骤1：解析问题
按 `references/rules-reference.md` 中的检测关键字和排除条件扫描

### 步骤2：逐项修复

**每个文件修复前必检：**
1. **Logger**：确认有 `@Slf4j` 或 Logger 字段，无则添加 `@Slf4j` + import
2. **Import**：添加所需 import，不引入重复
3. **方法边界**：Read 完整方法体，确认 try-catch 不跨方法边界
4. **已有处理**：确认目标代码未被现有 try-catch 包裹（含外层），避免重复嵌套

**每个文件修复后必检：**
1.**不改业务逻辑**：不加缓存、重试，不将异步改同步，不修改原始的代码逻辑（比如原有的数据库分页查询不能改为全量查询）

按 `references/rules-reference.md` 中的修复代码模板执行。

---

## 修复原则

1. **全部自动处理**
2. **精准异常捕获**：按规则类型捕获对应异常，不全用 Exception（具体类型见 rules-reference.md）
3. **只加日志**：降级类 try-catch + log.warn + throw
4. **只加超时**：超时类只添加超时配置
5. **只改策略**：CKR5000 只改拒绝策略为 AbortPolicy
6. **不改业务逻辑**：不加缓存、重试，不将异步改同步，不修改原始的代码逻辑（比如原有的数据库分页查询不能改为全量查询）
7. **事务安全**：catch 后必须重新抛出异常，保证事务回滚
8. **验证失败则撤销**：编译失败回滚修复，标记待人工
9. **CKR7019 核心**：项目全局异常处理器设置 `X-B3-ReturnCode` 为非成功码；`X-B3-ErrorMsg` 使用通用提示不暴露 SQL 详情；错误码必须使用项目已有的枚举值