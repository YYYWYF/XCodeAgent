# 当前 Engine Reconcile Fixture

本目录冻结的是当前 Template Engine 实际暴露的协议，而不是 `TEMPLATE_REFACTOR.md` 中尚未落地的目标协议。

当前可消费的范围：

- 四字段 `TemplateState`：`templateRevision`、`managedFiles`、`requested`、`effective`；
- `change-set.json.operations`；
- `ADD_FILE`、`UPDATE_FILE`、`DELETE_FILE`；
- Update Package 的 `change-set.json`、`next-template-state.json` 与 `payload/`。

当前不在协议中的 `validationPlan`、risks、diagnostics、结构化节点操作、`managed.files` / `managed.nodes`、migrations 与 marker host，均不得由 XCodeAgent 自行补造。Engine 升级 OpenAPI 与 Core 后，必须先更新本目录，再实现相应消费逻辑。
