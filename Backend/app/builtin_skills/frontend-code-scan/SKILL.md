---
name: frontend-code-scan
description: 前端npm包版本风险发现技能。用于检测项目中的已知npm包版本安全风险（如 axios 版本风险、form-data 版本风险）
---

## 概述

本技能用于检测项目中的npm包版本已知风险问题，注意不修复问题，只是发现问题

| 检测项 | 风险类型 | 检测内容 |
|--------|---------|---------|
| axios 版本风险 | 直接依赖版本 | 检查 axios 版本是否在安全范围内 |
| form-data 版本风险 | 间接依赖版本 | 检查被其他包间接引用的 form-data 版本是否在安全范围内 |


## 工作流程

当技能被触发时，按以下步骤执行：

### 步骤 1：定位项目根目录

1. 查找frontend目录下的 `package.json` 文件以及`pnpm-lock.yaml`文件，按如下包版本规则查询。以form-data包为例子，
分别在`package.json` 文件以及`pnpm-lock.yaml`文件内搜索，查看这个包的版本号，注意在`pnpm-lock.yaml`中需要查询类似
`/form-data@2.3.3:`的字段，这里2.3.3就是form-data的版本。`pnpm-lock.yaml`中如果有至少一个查询结果不满足下面各个包的
安全规则则就需要视为安全问题。注意这里不用修复，只需要把形成问题即可

#### axios 版本安全规则

- **安全版本范围**：
  - `>=1.15.0` — 即 1.15.0 及以上版本
- **风险版本**： [1.0.0, 1.15.0) 范围内的版本
- **修复方案**：
  如果版本号不符合安全版本范围：
  - 如果`package.json` 中dependencies或者devDependencies内有axios依赖声明，则将axios版本改为1.15.0
  - 如果`pnpm-lock.yaml`中有不符合安全版本范围的axios依赖，则在package.json中，与dependencies同级的层级添加如下代码
    ```js
    "pnpm": {
      "overrides": {
        "axios": "1.15.0",
      }
    }
    ```
    然后执行pnpm i安装依赖更新`pnpm-lock.yaml`

## form-data 版本安全规则

- **安全版本范围**：
  - `[2.5.4, 3.0.0)` — 即 >=2.5.4 且 <3.0.0
  - `[3.0.4, 4.0.0)` — 即 >=3.0.4 且 <4.0.0
  - `>=4.0.4` — 即 4.0.4 及以上版本
- **风险版本**：低于 2.5.4 的版本，[3.0.0, 3.0.4) 范围内的版本，[4.0.0, 4.0.4) 范围内的版本
- **修复方案**：
  如果版本号不符合安全版本范围：
  - 如果`package.json` 中dependencies或者devDependencies内有form-data依赖声明，则将form-data版本改为4.0.5
  - 如果`pnpm-lock.yaml`中有不符合安全版本范围的form-data依赖，则在package.json中，与dependencies同级的层级添加如下代码
    ```js
    "pnpm": {
      "overrides": {
        "form-data": "4.0.5",
      }
    }
    ```
    然后执行pnpm i安装依赖更新`pnpm-lock.yaml`
