# integrate_template 合并 template_refactor — Codex 执行指引

## 1. 当前分支关系

当前存在以下分支：

```text
dev_agent
   │
   └── integrate_template
                ↑
                │
                │ integrate
                │
        template_refactor
```

其中：

* `dev_agent`：当前主干开发分支；
* `integrate_template`：从 `dev_agent` 创建的临时集成分支；
* `template_refactor`：模板体系重构分支。

本次所有代码集成、冲突解决和验证工作，均在：

```text
integrate_template
```

分支完成。

`template_refactor` 保持原状，不对其执行 rebase、reset、历史重写等操作。

本次目标是：

> 以 `integrate_template` 当前代码作为最新功能基线，将 `template_refactor` 的重构结果整体迁移进来，最终得到“保留 dev_agent 最新业务能力，同时采用 template_refactor 新架构”的代码状态。

---

# 2. 集成方式

本次采用：

```bash
git merge --squash template_refactor
```

而不是：

```bash
git merge template_refactor
```

也不是：

```bash
git rebase template_refactor
```

`squash merge` 的目的：

1. 不修改 `template_refactor` 历史；
2. 不在 `integrate_template` 中产生 merge commit；
3. 将整个 `template_refactor` 看成一个完整的架构变更集；
4. 解决完成后最终形成一个独立的 Template Refactor 集成提交。

---

# 3. 开始前确认状态

开始前首先确认：

```bash
git branch --show-current
git status
```

当前分支必须为：

```text
integrate_template
```

如果不是，不要自行切换、reset 或修改分支，先报告当前状态。

同时查看两个分支整体差异：

```bash
git diff --stat integrate_template...template_refactor
```

以及提交差异：

```bash
git log --left-right --graph --oneline \
  integrate_template...template_refactor
```

目的是先理解：

```text
integrate_template 当前已经具备哪些能力

template_refactor 相对于共同祖先修改了哪些模块
```

不要看到冲突后立即开始修改。

---

# 4. 执行 squash merge

如果当前尚未执行 merge，则执行：

```bash
git merge --squash template_refactor
```

如果已经处于冲突状态，不要再次执行 merge。

直接进入冲突分析。

---

# 5. 冲突状态下 ours / theirs 的含义

本次执行的是：

```bash
integrate_template
        ←
template_refactor
```

因此在冲突状态下：

```text
ours / Current / stage 2
=
integrate_template 当前版本
=
dev_agent 功能基线
```

```text
theirs / Incoming / stage 3
=
template_refactor
=
重构架构版本
```

共同祖先：

```text
stage 1
=
base
```

可以使用：

```bash
git show :1:<file>
git show :2:<file>
git show :3:<file>
```

分别查看：

```text
:1 = base
:2 = integrate_template
:3 = template_refactor
```

禁止因为名称是：

```text
ours
theirs
Current
Incoming
```

就直接机械选择其中一方。

---

# 6. 本次集成最重要的原则

最终结果不是：

```text
integrate_template
+
template_refactor
```

的文本拼接。

而应该是：

```text
integrate_template 的最新业务语义
+
template_refactor 的最新架构语义
```

具体理解为：

## integrate_template 是功能基线

原则上必须保留其中有效的：

* 最新业务功能；
* 新增 API；
* 参数变化；
* Bug Fix；
* 异常处理；
* 配置变化；
* 用户旅程变化；
* 测试行为；
* 项目启动及验证能力；
* template_refactor 开发期间主干新增的其他有效能力。

但：

> 保留功能，不代表必须保留旧代码结构。

如果这些能力位于已经被 `template_refactor` 替代的旧组件中，应把能力迁移到新架构对应组件。

---

## template_refactor 是架构基线

以下内容原则上以 `template_refactor` 为准：

* 新模块边界；
* 新职责划分；
* 新 Core 模型；
* Template Engine 结构；
* Template Source 结构；
* TemplateState；
* ChangeSet；
* Workspace Apply；
* Template Update；
* Runtime / Launcher / Runner 等新的职责边界；
* 已明确废弃的旧接口；
* 已明确废弃的旧 Service；
* 被新架构替换的数据模型。

禁止为了保留 `integrate_template` 中的一段代码，而把已经废弃的旧架构重新恢复回来。

---

# 7. 第一阶段：先只读分析全部冲突

首先执行：

```bash
git status
git diff --name-only --diff-filter=U
git ls-files -u
```

此阶段：

> 不要修改任何冲突代码。

先按模块对冲突分类。

至少分类为：

```text
A. template_refactor 完全替代的旧实现

B. integrate_template 后续新增的独立能力

C. 双方都修改的核心代码

D. modify / modify

E. delete / modify

F. rename / modify

G. 接口 / Model / DTO 冲突

H. 配置 / dependency 冲突

I. 测试代码冲突

J. generated / lock / 构建产物冲突
```

不要按照：

```bash
git diff --name-only --diff-filter=U
```

返回的文件顺序逐个机械处理。

应该按照：

```text
模块
↓
职责
↓
依赖关系
```

处理。

---

# 8. 每个核心冲突先做三方分析

对于核心文件必须检查：

```bash
git show :1:<file>
git show :2:<file>
git show :3:<file>
```

同时搜索：

* 调用方；
* 被调用方；
* Interface；
* DTO / Model；
* 测试；
* 配置；
* 同模块其他实现。

修改前必须明确回答：

```text
1. base 原来是什么？

2. integrate_template 相对 base 修改了什么？

3. template_refactor 相对 base 修改了什么？

4. integrate_template 的修改属于：
   - 新功能
   - Bug Fix
   - 接口变化
   - 配置变化
   - 普通重构
   中哪一种？

5. template_refactor 的修改属于：
   - 架构重构
   - 职责迁移
   - 接口替换
   - 删除旧实现
   - 数据模型变化
   中哪一种？

6. integrate_template 的新能力在新架构中应该由谁承接？
```

如果无法回答第 6 个问题，不要直接修改代码。

---

# 9. 冲突解决策略

每个冲突先选择一种明确策略。

允许的策略包括：

```text
A. 保留 integrate_template

B. 保留 template_refactor

C. 保留 template_refactor 新架构
   + 迁移 integrate_template 新能力

D. 保留 integrate_template 最新业务行为
   + 适配 template_refactor 新接口

E. 删除旧实现
   + 将仍然有效的逻辑迁移到新组件

F. 合并 Source of Truth
   + 重新生成派生产物
```

对于架构级冲突，默认优先考虑：

```text
C
```

即：

> 保留 `template_refactor` 架构，并迁移 `integrate_template` 最新能力。

---

# 10. 典型场景处理规则

## 场景一：template_refactor 已完全替代旧组件

例如：

```text
integrate_template：

OldTemplateService
    ├── updateTemplate()
    └── validateProject()
```

而 `template_refactor` 已重构成：

```text
TemplateCore
TemplateUpdater
ProjectValidator
WorkspaceCommandRunner
```

不能简单选择：

```text
ours
```

恢复整个 `OldTemplateService`。

也不能简单：

```text
theirs
```

导致 `validateProject()` 新能力消失。

正确方式是：

```text
分析 validateProject() 行为
        ↓
找到新架构责任组件
        ↓
迁移到 ProjectValidator
        ↓
OldTemplateService 保持删除
```

即：

```text
保留能力
≠
保留旧类
```

---

# 11. delete / modify 冲突

例如：

```text
template_refactor：
删除 OldTemplateService

integrate_template：
继续修改 OldTemplateService
```

不要简单保留或删除。

首先分析：

```text
integrate_template 后续修改的业务目的是什么？
```

然后判断：

```text
这个能力在新架构中应该由谁承接？
```

如果能力仍有效：

```text
迁移能力
↓
新组件

旧文件
↓
保持删除
```

如果该能力已经随着架构一起废弃，则保持删除。

---

# 12. rename / modify 冲突

如果 `template_refactor`：

```text
A → B
```

而 `integrate_template` 又修改了 `A`：

不要简单把 `A` 内容全部复制到 `B`。

首先确认：

```text
A → B
```

只是 rename，还是同时发生职责变化。

如果只是 rename：

```text
把 integrate_template 的有效修改迁移到 B
```

如果同时拆分成：

```text
A
↓
B + C + D
```

则按照职责分别迁移。

---

# 13. 接口变化

如果：

```text
integrate_template
```

仍然调用旧接口，而：

```text
template_refactor
```

已经定义新接口：

原则上：

```text
保留 template_refactor 新接口
```

然后迁移：

```text
integrate_template 最新调用行为
```

到新接口。

除非存在明确的外部兼容要求，否则不要为了减少冲突新增：

```text
OldInterfaceAdapter
LegacyService
CompatibleXXX
```

等临时兼容层。

---

# 14. 配置与依赖

对于：

```text
pom.xml
package.json
application.yml
```

等文件：

不要简单采用一方。

分别确认：

```text
integrate_template 新增了哪些 dependency / config

template_refactor 新增、删除或替换了哪些 dependency / config
```

最终只保留：

```text
最终架构真实需要
+
当前业务真实需要
```

的配置。

避免旧架构 dependency 因 merge 而重新引入。

---

# 15. generated / lock 文件

例如：

```text
pnpm-lock.yaml
package-lock.json
generated/*
target/*
dist/*
```

不要优先人工逐行融合。

应该：

```text
先解决 Source of Truth
↓
再通过项目标准方式重新生成
```

例如：

```text
package.json
↓
pnpm install
↓
pnpm-lock.yaml
```

是否执行重新生成命令，应以项目当前已有构建规则为准。

不要擅自升级依赖版本。

---

# 16. 测试冲突

测试也是功能语义的一部分。

如果 `integrate_template` 新增测试描述的是有效业务行为：

```text
原则上保留行为
```

但测试代码本身可能需要适配：

```text
template_refactor 新架构
```

禁止为了让测试通过而直接删除测试。

只有已经明确废弃的旧架构行为对应测试才可以调整或删除。

---

# 17. 推荐处理顺序

不要按文件名顺序。

建议按照依赖关系：

```text
1. dependency / build / config
           ↓
2. Domain / Core Model
           ↓
3. Core Interface
           ↓
4. Core Implementation
           ↓
5. Template Engine / Template Source
           ↓
6. Workspace / Runtime / Runner
           ↓
7. Service / Orchestration
           ↓
8. API / Controller
           ↓
9. Frontend integration
           ↓
10. Tests
           ↓
11. Generated artifacts
```

一个模块完整处理以后，再进入下一个模块。

---

# 18. 每个模块采用固定流程

对于每一个冲突模块严格执行：

```text
只读分析
↓
确认 root cause
↓
确定能力迁移关系
↓
选择一种解决策略
↓
最小修改
↓
局部验证
↓
再处理下一个模块
```

禁止：

```text
先改一堆
↓
最后统一编译
↓
发现几十个问题
```

---

# 19. 每完成一个模块立即验证

至少执行：

```bash
git diff --check
```

然后根据模块运行已有：

```text
编译
单元测试
类型检查
静态检查
```

必须优先使用项目已有验证方式。

不要自行创建一套新的构建体系。

---

# 20. 同一问题连续失败时

如果同一模块尝试一种解决方案失败：

不要不断叠加修复。

重新检查：

```text
错误信息
↓
失败调用链
↓
当前 diff
↓
新旧架构责任映射
```

每次只形成一个明确假设，并通过最小修改验证。

如果同一模块已经连续尝试 3 次仍然失败：

停止继续打补丁。

重新检查是否存在：

```text
新旧架构并存
职责拆分错误
接口边界错误
State 模型不一致
调用方向错误
隐藏共享状态
```

此时优先重新判断架构迁移方向。

---

# 21. 禁止事项

本次任务禁止自行执行：

```bash
git reset --hard
git clean -fd
git rebase
git rebase --abort
git branch -D
git push --force
```

不要修改：

```text
template_refactor
```

分支历史。

不要切换到 `dev_agent` 并直接修改主干。

不要自行 push。

不要自行把 `integrate_template` 合回 `dev_agent`。

这些操作必须在集成完成并由用户明确决定后进行。

---

# 22. 禁止为了消冲突恢复旧架构

尤其禁止：

```text
旧 Service 已经删除
↓
为了让调用方编译
↓
重新创建旧 Service
```

或者：

```text
新接口改造比较复杂
↓
增加 Legacy Adapter
↓
同时维持两套实现
```

除非代码中明确存在真实兼容需求。

---

# 23. 禁止额外重构

本次只处理：

```text
template_refactor
→
integrate_template
```

的集成。

不要顺便：

* 改命名；
* 清理整个项目；
* 格式化无关代码；
* 重构其他模块；
* 升级依赖；
* 修改与本次集成无关的业务逻辑。

发现其他问题可以记录，但不要混入本次修改。

---

# 24. 所有冲突处理完成后的检查

首先确认：

```bash
git diff --name-only --diff-filter=U
```

必须无输出。

然后：

```bash
git grep -n \
  -e '^<<<<<<< ' \
  -e '^=======$' \
  -e '^>>>>>>> '
```

预期无残留冲突标记。

继续执行：

```bash
git diff --check
git status
```

---

# 25. 完成冲突后必须做一次“能力丢失回检”

编译通过并不代表集成正确。

必须重点检查：

```text
integrate_template / dev_agent
在 template_refactor 开发期间新增的有效能力，
有没有因为采用 template_refactor 新架构而静默消失。
```

重点回检：

* 新增 API；
* Controller 参数；
* Service 能力；
* Bug Fix；
* 异常处理；
* 配置；
* DTO / Model 字段；
* 用户旅程；
* 项目启动逻辑；
* 项目验证逻辑；
* 测试；
* Runtime 行为。

最终必须做到：

```text
dev_agent 最新功能
        ↓
全部能够在 template_refactor 新架构中找到对应实现
```

---

# 26. 完整验证

所有冲突处理完成后执行项目已有的完整验证：

```text
compile
+
unit test
+
type check
+
必要的 integration test
+
必要的项目启动验证
```

必须以当前仓库已有命令为准。

如果因为环境原因无法运行某项验证：

明确报告：

```text
未验证什么
为什么无法验证
可能存在什么风险
```

不要声称验证已经通过。

---

# 27. 提交规则

`squash merge` 解决完成以后：

不要使用：

```bash
git merge --continue
```

本次最终应该形成普通 commit。

但在完成全部验证前：

```text
不要 commit
```

全部验证完成后，先向用户报告结果。

如果用户明确要求提交，再执行类似：

```bash
git add .
git commit -m "refactor: integrate template architecture"
```

不要自行 push。

---

# 28. 最终报告

完成后输出：

## 一、集成结果

```text
是否仍有 Git 冲突：
是否完整编译：
测试结果：
启动验证结果：
```

## 二、能力迁移

列出关键映射：

```text
integrate_template 原能力
→
template_refactor 新架构中的最终位置
```

例如：

```text
TemplateService.validateProject()
→
ProjectValidator

TemplateService.launchProject()
→
ProjectLauncher / WorkspaceCommandRunner
```

## 三、旧架构处理

说明：

```text
哪些旧类保持删除
哪些旧接口没有恢复
哪些能力被迁移到了新组件
```

## 四、风险

只报告实际存在的：

```text
未执行的测试
无法验证的环境
存在不确定性的行为
需要人工确认的业务语义
```

---

# 29. 最终成功标准

不能只以：

```text
没有冲突标记
```

或者：

```text
代码可以编译
```

作为完成标准。

最终必须同时满足：

```text
1. integrate_template 中 dev_agent 的最新有效能力没有丢失；

2. template_refactor 的目标架构没有被回退；

3. 没有因为解决冲突重新引入废弃架构；

4. 新增能力已经落入新架构正确职责；

5. API / Model / Config 等调用关系一致；

6. 编译、测试以及能够执行的运行验证通过；

7. 没有与本次集成无关的大范围修改。
```

本次工作的最终目标只有一句话：

> **以 integrate_template 为最新功能基线，将 template_refactor 的新架构完整迁移进来，得到“最新 dev_agent 功能运行在 template_refactor 新架构之上”的最终代码状态。**
