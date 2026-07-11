# Agent Process Streaming Design

## Goal

Show a coding-agent run as it happens without letting transient process output bury the final answer. The workbench renders model reasoning, workflow steps, tool calls, and shell commands as compact, expandable process rows in both themes. During a run each row describes the current action; when the run finishes the process group collapses to `已处理`, while every detail remains inspectable.

## Interaction model

- The assistant message owns one ordered process group and one final-answer region.
- Running rows default to collapsed and use active copy such as `正在思考`, `正在调用 read_file 工具`, or `正在执行 pnpm build 命令`.
- Clicking a row reveals streamed reasoning text, tool arguments/results, or command/output details.
- The group automatically collapses when the run ends and its persistent summary becomes `已处理 · N 个步骤`.
- Only the final answer remains expanded outside the process group.
- The UI never invents private chain-of-thought. It displays reasoning text only when the model provider returns an explicit reasoning stream; otherwise it shows a short workflow/action summary.

## Visual system

The component uses the workbench theme tokens (`--wb-canvas`, `--wb-surface-subtle`, `--wb-border`, `--wb-text*`) so light and dark themes share identical hierarchy and interaction. Active steps use the existing accent/loading treatment; completed steps use restrained neutral/check styling. Details use a monospace inset surface with bounded height and scroll.

## Event contract

The backend continues to expose `/workflow/run` as an AG-UI SSE endpoint and adds `agent-process` custom events:

```json
{
  "id": "stable-step-id",
  "kind": "reasoning | tool | command | workflow",
  "status": "running | completed | failed",
  "title": "正在调用 read_file 工具",
  "detail": "streamed or structured detail",
  "result": "optional result",
  "sequence": 3
}
```

LangGraph is consumed with `updates` and `messages` stream modes. `updates` drives workflow lifecycle and the existing state snapshots. `messages` exposes provider-returned reasoning chunks and streamed tool-call arguments. Tool messages in completed node state close matching calls and attach results. The frontend merges events by stable ID and persists the resulting process-step array with the chat message.

## Reference mapping and context budget

- `learn-coding-agent`: keep the visible loop compact—context gathering, action, verification, repeat—without introducing a second orchestration runtime.
- OpenCode: follow its structured message-part approach for reasoning and tool states, with stable IDs and incremental updates instead of flattening everything into assistant text.
- Deep Agents / LangGraph: use native graph/message streaming and existing tools; do not mirror full model messages or unbounded tool output into workflow state.
- Context remains bounded: process detail and results are truncated at the protocol boundary, while durable session persistence retains only the UI-ready summary/detail fields.

