# 拓扑升级方案：Agent 直连 → 网关组合

> **文档性质**：方案草案，**非规范性 · 待评审**。**主流程（六步 + 工作量结论）已并入网关设计评审文档的 §11「拓扑升级主流程（概览）」，本文保留完整 Runbook、逐项改动清单与验收清单，作为其详细依据。**
> **适用场景**：一个已经上线的 `agent_runtime_direct` 应用（Frontend + Agent Runtime），要升级为 `gateway_composed`（Frontend + Gateway + Backend + Agent Runtime）。
> **一句话方案**：**门牌号不变，门口加前台。**
> 浏览器访问的地址、Agent 的历史会话、业务数据都不变；变的只有两件事——**请求先到谁手上**（Runtime → Gateway）、**谁验身份**（Runtime → Gateway）。
> **与上一版的关系**：本文是 2026-09-20 版「升级设计草案」的重写版，把抽象原则换成了可照着做的步骤与具体字段。

---

## 一、先用一句话理解升级

> 你的应用现在是一栋**只有一扇门**的房子，推门进去直接就是 Agent 房间。
> 升级＝在门口加一个**前台**（Gateway）：前台先核对身份证，再带你去 Agent 房间，或者去新建的业务房间（Backend）。
> **房子还是那栋房子，门牌号也不换。** 只是从"谁都能直接推门进 Agent 房间"变成了"先过前台"。

上图的三个要点：

1. **Frontend 的源码基本不动**（只重生成一行 origin 配置）；
2. **Agent Runtime 的会话状态不动**（thread / run / checkpoint 原地保留）；
3. **Gateway 和 Backend 是新增的**（这就是这次升级要多付出的部分）。

---

## 二、具体例子：`recheck` 应用升级前后

假设我们有一个智能审核助手应用 `recheck`，目前只有 Agent 对话功能。

### 2.1 浏览器看到的地址：不变

| | 升级前 | 升级后 |
| --- | --- | --- |
| 请求 | `POST https://recheck.example.com/agents/recheck/run` | `POST https://recheck.example.com/agents/recheck/run` |
| 谁先收到 | Agent Runtime（公开边界） | **Gateway**（新的公开边界） |
| 谁验身份 | Agent Runtime | **Gateway**，验完换签内部票据给 Runtime |
| path / 请求体 / 响应格式 | — | **完全不变** |

> 前端代码**一行都不用改**。这是整套方案能成立的前提：**拓扑升级不改变公开契约**。
>
> 前提是让 Gateway 监听原来 Runtime 的公开域名与端口。如果换成新域名新端口，那就属于运行值变更（见 §4 的 B6），需要运维同步，浏览器地址就会变——这是要尽量避免的。

### 2.2 目录结构：变了

**升级前**

```text
recheck/
  frontend/
  agent-runtime/
  .xcodeagent/template-state.json
```

**升级后**

```text
recheck/
  frontend/                     # 不变（只重生成 public-origin 配置）
  backend/                      # 新增
    recheck-gateway/            #   新增：Gateway 子模块
    recheck-service/            #   新增：业务后端
    recheck-common/             #   新增：公共模块（合同/安全/观测）
  agent-runtime/                # 不变（只改认证姿态）
  .xcodeagent/template-state.json
```

> Gateway **不是**第四套工程，它是 Backend 父工程的一个子模块（`backend/recheck-gateway/`），但**独立进程运行**。

### 2.3 一次请求的流程：从 4 步变成 8 步

| 升级前（4 步） | 升级后（8 步） |
| --- | --- |
| 1. 浏览器带 JWT 请求 Runtime | 1. 浏览器带 JWT 请求 Gateway |
| 2. Runtime 本地验 JWT | 2. Gateway 做 Origin / CORS / 大小 / 路由校验 |
| 3. Runtime 执行 Agent 逻辑 | 3. Gateway 本地验 JWT + 查登出状态 |
| 4. Runtime 返回事件流 | 4. Gateway 换签内部票据 `Xcode-User-Info` |
| | 5. Gateway 转发给 Agent Runtime |
| | 6. Runtime 验 Gateway 服务身份 + 票据 |
| | 7. Runtime 执行 Agent 逻辑 |
| | 8. 事件流经 Gateway 原样返回（不缓冲、不改写） |

多出来的 4 步，就是 Gateway 的价值：**统一入口 + 统一认证 + 统一治理**。

### 2.4 跟着走的 vs 要换掉的

| 分类 | 内容 |
| --- | --- |
| **跟着走，不动** | 应用身份（applicationId / 模块命名）；Frontend 业务源码；Agent 定义（agent_id / Tool / Prompt / 模型）；Runtime 的 thread / run / checkpoint / 事件日志；领域数据；用户身份映射 |
| **换掉 / 新增** | 公开入口（Runtime → Gateway）；认证终止点（Runtime → Gateway）；Runtime 的信任姿态；票据形态；新增 Backend 与 Gateway 模块；启动顺序 |

---

## 三、升级六步（照着做）

### Step 0 — 前置检查（4 项必须都为真）

| 前置 | 为什么需要 | 现状 |
| --- | --- | --- |
| `gateway_composed` 已在拓扑注册表注册（枚举 + Definition） | 否则无法编译该拓扑 | ❌ 只有设计 |
| Gateway 模板与生成器存在 | 否则生成不出 `recheck-gateway` | ❌ 不存在 |
| Agent Runtime 模板支持 internal profile（拒绝浏览器凭据） | 否则 Runtime 无法从"公开边界"变为"内部服务" | ❌ 未验证 |
| `TechnicalPlan` 支持 `topology.migratedFrom` | 否则门禁无法识别这是一次拓扑变更 | ❌ 无字段 |

**四项任一为假，升级无法执行。** 现在四项全假，所以本方案当前是"目标态方案"，不是"马上能跑的方案"。

### Step 1 — 冻结：排空 Agent run

- 停止接受新的 Agent run；
- 让活跃 run 自然结束，或显式取消；
- 确认没有进行中的 run 后，再进入下一步。

> **为什么必须排空**：升级把票据形态从"direct 拓扑自有合同"换成了 Gateway 签发的 `Xcode-User-Info`（6 小时租约）。已经在跑的 run 拿的是旧形态票据，无法无缝迁移。
> V1 明确**不支持**"用新票据 + checkpoint 无缝恢复同一 run"，所以把"排空"写成硬约束，而不是假装无感。

### Step 2 — 修订：改技术计划

这是一次 **Formal Revision**，必须由用户重新确认 `TechnicalPlan`。

**升级前**

```json
{
  "topology": { "type": "agent_runtime_direct", "public_edge": "agent_runtime" },
  "services": [
    { "id": "recheck-agent-runtime", "kind": "agent_runtime", "required": true }
  ],
  "agent_contracts": [
    { "agent_id": "recheck-assistant", "runtime_service_id": "recheck-agent-runtime" }
  ]
}
```

**升级后**

```json
{
  "topology": {
    "type": "gateway_composed",
    "public_edge": "gateway",
    "migratedFrom": "agent_runtime_direct"
  },
  "services": [
    { "id": "recheck-service", "kind": "business_backend", "required": true },
    { "id": "recheck-agent-runtime", "kind": "agent_runtime", "required": true }
  ],
  "api_contracts": [
    {
      "id": "recheck_agent_api",
      "endpoints": [
        {
          "id": "recheck.run",
          "method": "POST",
          "path": "/agents/recheck/run",
          "owner_service_id": "recheck-agent-runtime",
          "upstream_path": "/internal/agents/recheck/run",
          "gateway": {
            "route_kind": "agent_stream",
            "policy_profile": "streaming",
            "anonymous": false,
            "criticality": "required"
          }
        }
      ]
    }
  ],
  "internal_rpc_contracts": [],
  "backend_adapters": []
}
```

**这次修订只改了 4 件事：**

1. `topology.type` → `gateway_composed`，`public_edge` → `gateway`，并记下 `migratedFrom`；
2. `services[]` 增加一个 `business_backend`；
3. Agent Endpoint 的 `path` **保持原值** `/agents/recheck/run`，只新增 `upstream_path`；
4. 补上 `gateway` 路由元数据（`route_kind` / `policy_profile` / `criticality`）。

> `migratedFrom` 的用途**不是**读取旧格式，而是让确认门禁识别"这是一次拓扑升级"，从而强制跑升级检查（排空证据、重绑清单、Runtime internal profile 验证）。

### Step 3 — 重新生成与构建

平台按新 `TechnicalPlan` 重新编译并构建，会**自动重生成**这些产物：

| 产物 | 做什么 |
| --- | --- |
| `frontend:public-origin-config` | 公开 Origin 从 Runtime 改指 Gateway |
| `backend:gateway-config` + `gateway:route:*` + `gateway:route-registry` | 生成 Gateway 配置与只读路由表 |
| `gateway:unified-auth-binding` | 启用统一认证 + 内部票据签发 |
| `backend:xcode-user-info:*` | Backend / Runtime 侧验签与 Principal 恢复 |
| `gateway:agent-compatibility` | Agent 路由、连接治理、6h 租约与重新授权 |
| `backend:adapter:*` | 仅当声明了外部服务时才生成（本例为空） |
| `gateway:launch-verification` | 新的四段启动顺序与探测 |

**Runtime 私有状态目录原地不动，不迁移、不转格式。**

### Step 4 — 验证（清单见 §5）

重点验证三件最容易出问题的事：

1. **路由可达**：原公开 path 仍能通，且确实经过 Gateway；
2. **认证链完整**：JWT 在 Gateway 验证，内部票据能签发与验签，公开 JWT 不出现转发；
3. **Runtime 拒绝浏览器凭据**：直接拿浏览器凭据打 Runtime 的内部 listener，必须被拒——这是本次升级最关键的"信任姿态切换"证据。

### Step 5 — 切换

required checks 全部通过后，由 Project Launcher 启动新拓扑产物。

启动顺序变为：

```text
Backend → Agent Runtime → Gateway → Frontend
```

失败时：**不切换**，旧产物继续运行。

> 这就是当前版本的安全网：不是"自动回滚"，而是"新产物没通过验证就不启用"。

### Step 6 — 收尾与观察

- 确认 Gateway 成为唯一公开探测入口；
- 确认 Backend / Runtime 只接受内部身份；
- 观察认证失败率、登出撤销延迟（应 ≤ 60s）、Agent 流建立与断开；
- 确认旧拓扑产物已完成退役。

---

## 四、逐项改动清单（用来估工作量）

| # | 改哪里 | 类型 | 谁改 |
| --- | --- | --- | --- |
| B1 | Frontend Public Origin 从 Runtime 改指 Gateway | 平台确定性重生成 | 平台 |
| B2 | Route Contract 新增 `upstream_path`（public path 不变） | 平台确定性重生成 | 平台 |
| B3 | Gateway 启用 `unified-auth-binding`；Runtime 关闭公开认证 | 配置 + 少量代码 | 平台 + Runtime 模板 |
| B4 | **Runtime 切换到 internal profile**（拒绝浏览器凭据，只验服务身份 + `Xcode-User-Info`） | **代码级改造** | Runtime 模板 |
| B5 | 票据形态统一为 `Xcode-User-Info` | 平台确定性重生成 | 平台 |
| B6 | 运维值重绑：CORS 白名单、SSO 回调 URI、Cookie domain、证书、DNS | **运行值**，不重编译 | 运维 |
| B7 | 是否顺带启用 Agent RBAC（`authorization.enabled`） | 决策 | 用户 |
| B8 | 是否新增内部 RPC（Agent 要用 Backend 能力时） | 生成物（可选） | 平台 |
| B9 | 启动顺序与健康探测改为四段 | 平台确定性重生成 | 平台 |

**结论：9 项里只有 B3 / B4 涉及代码，其中 B4 是真正的硬骨头。**
评审时按这张表估工，比笼统说"改造一下"靠谱得多。

---

## 五、验收清单（可直接勾选）

**路由与入口**
- [ ] 原公开 path 全部可达，且响应与升级前一致
- [ ] Gateway 是唯一公开探测入口
- [ ] Frontend 只包含 Gateway 的公开 Origin
- [ ] Backend / Runtime 的内部地址未进入前端

**认证与身份**
- [ ] 统一认证 JWT 只在 Gateway 验证，未被转发到 Backend / Runtime
- [ ] 内部票据 `Xcode-User-Info` 可正常签发与验签
- [ ] 客户端伪造的内部身份 Header 被删除
- [ ] 登出后活跃流与 RPC 在 60 秒内失效
- [ ] **直接带浏览器凭据访问 Runtime 内部 listener 被拒绝**

**服务图完整性**
- [ ] 服务图同时存在 Frontend / Gateway / Backend / Agent Runtime
- [ ] 缺少任一模块时编译、构建、启动都被拒绝
- [ ] Agent Endpoint owner 是 `agent_runtime`，业务 Endpoint owner 是 `business_backend`

**状态延续**
- [ ] 升级前的 thread / run / checkpoint 在升级后仍可被访问
- [ ] 领域数据未受影响

**运行时**
- [ ] 四段启动顺序生效，Gateway readiness 覆盖全部 required upstream
- [ ] Agent 流未被缓冲，断开与取消语义正确
- [ ] 错误合同不泄露内部地址、栈信息与凭据

---

## 六、失败怎么办

| 情况 | 处理 |
| --- | --- |
| 构建失败 | 不切换，旧产物继续运行；修复后重新构建 |
| 验证不通过 | 同上；不得"跳过检查先切上去试试" |
| 切换后发现问题 | 当前版本**无自动回滚**；按 current-contract-only，回退 = 重新走一次修订并重新构建旧拓扑产物 |
| 想"边跑边灰度" | **不支持**。见 §7 的策略取舍 |

> 明确不做：把"升级失败自动切回旧拓扑"写进合同。那需要另一套部署合同（候选槽 / promotion / rollback），目前属于非目标。

---

## 七、三种做法，建议只做一种

| 做法 | 说明 | 结论 |
| --- | --- | --- |
| **A. 静默窗口升级** | 排空 → 整批替换 | ✅ **建议 V1 唯一做法**。语义最简单，无中间态 |
| B. 双栈过渡 | 旧 Runtime 保持公开，同时建 Gateway，逐步切流 | ❌ **明确不做**。会同时存在两个公开边界；且违反"无 active/candidate、无原子切换"的既定边界 |
| C. 另建应用 | 新建一个 gateway_composed 应用，手工迁数据 | 兜底方案。代价：丢应用身份、丢 Runtime 历史、丢前端积累。要写清代价，别被当成免费方案 |

---

## 八、现在能不能做

**不能。** 四个前置条件（Step 0）当前全部不满足。本方案的正确使用方式是：

1. 评审时确认**方向**（要不要支持升级、按哪种做法）；
2. 等 `gateway_composed` 注册、Gateway 模板可用、Runtime internal profile 验证通过之后；
3. 再按本文档落地，并把 §5 的验收清单直接转成测试用例。

---

## 九、需要拍板的 4 件事

| # | 议题 | 建议 |
| --- | --- | --- |
| **G8** | 是否承认"拓扑升级"是一等公民（`TechnicalPlan` 加 `migratedFrom` + 独立升级检查） | 承认。否则以后每次演进都只能"另建应用" |
| **G9** | 升级窗口策略 | 只做静默窗口（A）；明确拒绝双栈与灰度（B） |
| **G10** | 升级时是否顺带开启 Agent RBAC | **默认不开**。`authorization.enabled` 的启用是独立决策，不与拓扑升级耦合 |
| **G11** | 是否强制套 Gateway 统一路径前缀 | **不强制**。升级时继承既有 public path，否则拓扑升级会变成 API 破坏性变更 |

---

## 附：给评审现场用的三句话

1. **升级不是"加一个网关"，是"公开入口和认证位置整体搬家"。**
2. **搬家的代价集中在两处**：Runtime 要改成只信内部票据；前端 Origin 要改指 Gateway。其余都是平台自动重生成或运维值调整。
3. **安全网是"验证通过前不切换"，不是"自动回滚"。** 所以不做灰度、不做双栈，用一次停机窗口换语义简单。
