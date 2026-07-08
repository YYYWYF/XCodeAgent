# from __future__ import annotations
# 
# import html
# import hashlib
# import json
# import re
# import shlex
# from dataclasses import dataclass
# from typing import Annotated, Any, Callable, Dict, List, Literal, Optional, Tuple, Type
# from uuid import uuid4
# 
# from fastapi import HTTPException
# from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, SystemMessage
# from langgraph.graph import END, START, MessagesState, StateGraph
# from langgraph.graph.message import add_messages
# from pydantic import BaseModel, ValidationError
# from typing_extensions import NotRequired
# 
# from app.services.builtin_skills import load_react_antd_v4_codegen_prompt
# from app.config import Settings
# from app.services.llm_client import ModelResponse, create_model_provider
# from app.tools.antd_v4_docs import build_prompt_context
# from app.workspace import workspace as workspace_tools
# from app.workspace.workspace import build_prompt_context as build_workspace_tools_prompt_context
# 
# try:
#     from langgraph.checkpoint.memory import MemorySaver
# except ImportError:  # pragma: no cover - compatibility with newer LangGraph names
#     from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
# 
# 
# APPLICATION_DEVELOPER_PROMPT = """
# # XCodeAgent Role
# 
# 你是 XCodeAgent，一个应用开发助手，不是单纯的前端开发助手。
# 
# 你的目标是帮助用户把一个应用需求完整落地：需求澄清、产品/交互设计、数据模型、API 设计、前端实现、后端或集成边界、工程改动、验证和交付报告。
# 
# 工作方式：
# - 优先按业务功能切片理解需求，不要把“页面”和“API”割裂成两套流程。
# - 每个功能切片都应同时考虑用户目标、UI/交互、数据模型、API/服务边界、权限、错误处理和验证方式。
# - 生成计划时必须包含最终验证环节；生成代码后必须说明如何验证准确性。
# - 先理解现有工程结构和技术栈，再决定具体实现路径。
# - React、Ant Design、TypeScript 规范只是当前工程在前端代码落地时的实现约束，不是你的身份定位。
# - 当任务不是 React 前端代码时，不要把回答收窄成前端建议；仍然从完整应用开发角度处理。
# """.strip()
# 
# WORKSPACE_TOOL_PROMPT = """
# # Tool Use
# 
# When the user asks you to inspect, create, edit, search, run, or verify local workspace files, use the available workspace tools instead of only describing the action. Continue calling tools until the requested work is actually done or a tool result says explicit user approval is required.
# 
# If a tool result has `requires_approval: true`, do not retry the same tool call without approval. Explain what needs approval and why.
# 
# When deleting workspace files, use file_delete instead of terminal_exec so the frontend can review the exact code diff and approval request.
# """.strip()
# 
# MAX_TOOL_ROUNDS = 12
# MAX_TOOL_RESULT_CHARS = 20000
# TEXT_TOOL_INVOKE_RE = re.compile(
#     r"<invoke\s+name=(?P<quote>['\"])(?P<name>.*?)(?P=quote)\s*>(?P<body>.*?)</invoke>",
#     re.DOTALL,
# )
# TEXT_TOOL_WRAPPER_RE = re.compile(
#     r"<tool\s+name=(?P<quote>['\"])(?P<name>.*?)(?P=quote)\s*>(?P<body>.*?)</tool>",
#     re.DOTALL,
# )
# TEXT_TOOL_CALL_WRAPPER_RE = re.compile(
#     r"<tool_call>(?P<body>.*?)</tool_call>",
#     re.DOTALL,
# )
# TEXT_FUNCTION_EQUALS_RE = re.compile(
#     r"<function=(?P<name>[^>\s]+)>(?P<body>.*?)</function>",
#     re.DOTALL,
# )
# TEXT_TOOL_ARGUMENT_RE = re.compile(
#     r"<(?P<tag>[A-Za-z][\w.-]*)\s+name=(?P<quote>['\"])(?P<name>.*?)(?P=quote)\s*>"
#     r"(?P<value>.*?)</(?P=tag)>",
#     re.DOTALL,
# )
# TEXT_EQUALS_ARGUMENT_RE = re.compile(
#     r"<parameter=(?P<name>[^>\s]+)>(?P<value>.*?)</parameter>",
#     re.DOTALL,
# )
# SIMPLE_TEXT_ARGUMENT_RE = re.compile(
#     r"<(?P<name>[A-Za-z_][\w.-]*)>(?P<value>.*?)</(?P=name)>",
#     re.DOTALL,
# )
# SELF_CLOSING_TEXT_TOOL_RE = re.compile(
#     r"<(?P<name>[A-Za-z][\w.-]*)\s+(?P<attrs>[^<>]*?)/>",
#     re.DOTALL,
# )
# WRAPPED_TEXT_TOOL_RE = re.compile(
#     r"<(?P<name>[A-Za-z][\w.-]*)>(?P<body>.*?)</(?P=name)>",
#     re.DOTALL,
# )
# TEXT_TOOL_ATTRIBUTE_RE = re.compile(
#     r"(?P<name>[A-Za-z_][\w]*)=(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
#     re.DOTALL,
# )
# 
# WorkspaceToolRunner = Tuple[Type[BaseModel], Callable[[Any], Dict[str, Any]]]
# 
# 
# @dataclass(frozen=True)
# class TextToolUse:
#     id: str
#     name: str
#     input: Dict[str, Any]
# 
# 
# @dataclass(frozen=True)
# class PendingAgentToolRequest:
#     thread_id: str
#     tool_name: str
#     input: Dict[str, Any]
#     workspace_root: Optional[str]
#     code_change: Optional[Dict[str, Any]] = None
# 
# 
# WORKSPACE_TOOL_RUNNERS: Dict[str, WorkspaceToolRunner] = {
#     "workspace_info": (workspace_tools.WorkspaceRequest, workspace_tools.workspace_info),
#     "workspace_list_files": (workspace_tools.ListFilesRequest, workspace_tools.list_files),
#     "workspace_tree": (workspace_tools.TreeRequest, workspace_tools.workspace_tree),
#     "file_read": (workspace_tools.ReadFileRequest, workspace_tools.read_file),
#     "file_write": (workspace_tools.WriteFileRequest, workspace_tools.write_file),
#     "file_patch": (workspace_tools.PatchFileRequest, workspace_tools.patch_file),
#     "file_delete": (workspace_tools.DeleteFileRequest, workspace_tools.delete_file),
#     "search_files": (workspace_tools.SearchFilesRequest, workspace_tools.search_files),
#     "search_text": (workspace_tools.SearchTextRequest, workspace_tools.search_text),
#     "terminal_exec": (workspace_tools.TerminalExecRequest, workspace_tools.terminal_exec),
#     "git_status": (workspace_tools.GitStatusRequest, workspace_tools.git_status),
#     "git_diff": (workspace_tools.GitDiffRequest, workspace_tools.git_diff),
# }
# 
# WORKSPACE_TOOLS: List[Dict[str, Any]] = [
#     {
#         "name": "workspace_info",
#         "description": "Inspect the selected workspace root and git repository metadata. Corresponds to workspace.info.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string", "description": "Absolute workspace root. Defaults to the current application workspace."},
#             },
#         },
#     },
#     {
#         "name": "workspace_list_files",
#         "description": "List files in a workspace directory. Corresponds to workspace.list_files.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string", "default": "."},
#                 "recursive": {"type": "boolean", "default": False},
#                 "include_hidden": {"type": "boolean", "default": False},
#                 "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
#             },
#         },
#     },
#     {
#         "name": "workspace_tree",
#         "description": "Return a directory tree for the workspace. Corresponds to workspace.tree.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string", "default": "."},
#                 "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3},
#                 "include_hidden": {"type": "boolean", "default": False},
#                 "limit": {"type": "integer", "minimum": 1, "maximum": 3000, "default": 500},
#             },
#         },
#     },
#     {
#         "name": "file_read",
#         "description": "Read a UTF-8 text file inside the workspace. Corresponds to file.read.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string", "description": "Workspace-relative file path."},
#                 "start_line": {"type": "integer", "minimum": 1, "default": 1},
#                 "max_lines": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 400},
#                 "max_chars": {"type": "integer", "minimum": 200, "maximum": 200000, "default": 20000},
#                 "allow_sensitive": {"type": "boolean", "default": False},
#             },
#             "required": ["path"],
#         },
#     },
#     {
#         "name": "file_write",
#         "description": "Create or replace a UTF-8 text file inside the workspace. Corresponds to file.write.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string", "description": "Workspace-relative file path."},
#                 "content": {"type": "string"},
#                 "create_dirs": {"type": "boolean", "default": True},
#                 "overwrite": {"type": "boolean", "default": True},
#                 "dry_run": {"type": "boolean", "default": False},
#                 "allow_sensitive": {"type": "boolean", "default": False},
#             },
#             "required": ["path", "content"],
#         },
#     },
#     {
#         "name": "file_patch",
#         "description": "Patch an existing UTF-8 text file by replacing exact text. Corresponds to file.patch.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string"},
#                 "edits": {
#                     "type": "array",
#                     "items": {
#                         "type": "object",
#                         "properties": {
#                             "old_text": {"type": "string"},
#                             "new_text": {"type": "string", "default": ""},
#                             "replace_all": {"type": "boolean", "default": False},
#                         },
#                         "required": ["old_text"],
#                     },
#                     "minItems": 1,
#                 },
#                 "expected_sha256": {"type": "string"},
#                 "dry_run": {"type": "boolean", "default": False},
#                 "allow_sensitive": {"type": "boolean", "default": False},
#             },
#             "required": ["path", "edits"],
#         },
#     },
#     {
#         "name": "file_delete",
#         "description": "Delete an existing workspace file. Use this instead of terminal commands for file deletion. Corresponds to file.delete.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string"},
#                 "dry_run": {"type": "boolean", "default": False},
#                 "allow_sensitive": {"type": "boolean", "default": False},
#             },
#             "required": ["path"],
#         },
#     },
#     {
#         "name": "search_files",
#         "description": "Search workspace file names or glob-style relative paths. Corresponds to search.files.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "query": {"type": "string"},
#                 "path": {"type": "string", "default": "."},
#                 "include_hidden": {"type": "boolean", "default": False},
#                 "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
#             },
#             "required": ["query"],
#         },
#     },
#     {
#         "name": "search_text",
#         "description": "Search text inside workspace files. Corresponds to search.text.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "query": {"type": "string"},
#                 "path": {"type": "string", "default": "."},
#                 "regex": {"type": "boolean", "default": False},
#                 "case_sensitive": {"type": "boolean", "default": False},
#                 "include_hidden": {"type": "boolean", "default": False},
#                 "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
#                 "max_chars_per_match": {"type": "integer", "minimum": 40, "maximum": 1000, "default": 240},
#             },
#             "required": ["query"],
#         },
#     },
#     {
#         "name": "terminal_exec",
#         "description": "Run a low-risk command in the workspace without a shell. Risky commands may require approval. Corresponds to terminal.exec.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "argv": {"type": "array", "items": {"type": "string"}},
#                 "command": {"type": "string", "description": "Shell-like command string split with shlex; not executed through a shell."},
#                 "cwd": {"type": "string", "default": "."},
#                 "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
#                 "max_output_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "default": 12000},
#             },
#         },
#     },
#     {
#         "name": "git_status",
#         "description": "Run git status in the workspace. Corresponds to git.status.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "porcelain": {"type": "boolean", "default": True},
#             },
#         },
#     },
#     {
#         "name": "git_diff",
#         "description": "Run git diff in the workspace. Corresponds to git.diff.",
#         "input_schema": {
#             "type": "object",
#             "properties": {
#                 "workspace_root": {"type": "string"},
#                 "path": {"type": "string"},
#                 "staged": {"type": "boolean", "default": False},
#                 "max_chars": {"type": "integer", "minimum": 1000, "maximum": 200000, "default": 20000},
#             },
#         },
#     },
# ]
# 
# 
# class AgentState(MessagesState):
#     system_prompt: NotRequired[str]
#     workspace_root: NotRequired[str]
#     temperature: NotRequired[float]
#     max_tokens: NotRequired[int]
#     thread_id: NotRequired[str]
#     pending_approvals: NotRequired[List[Dict[str, Any]]]
#     code_changes: NotRequired[List[Dict[str, Any]]]
# 
# 
# class AgentRuntime:
#     def __init__(self, settings: Settings) -> None:
#         self.settings = settings
#         self.provider = create_model_provider(settings)
#         self._pending_tool_requests: Dict[str, PendingAgentToolRequest] = {}
#         self.graph = self._build_graph()
# 
#     def _build_graph(self):
#         graph = StateGraph(AgentState)
#         graph.add_node("llm", self._call_model)
#         graph.add_edge(START, "llm")
#         graph.add_edge("llm", END)
#         return graph.compile(checkpointer=MemorySaver())
# 
#     async def _call_model(self, state: AgentState) -> dict[str, list[AIMessage]]:
#         model_messages, inline_system = self._to_model_messages(state["messages"])
#         latest_user_message = self._latest_user_message(state["messages"])
#         system_prompt = self._compose_system_prompt(
#             state.get("system_prompt"),
#             latest_user_message=latest_user_message,
#         )
#         if inline_system:
#             system_prompt = f"{inline_system}\n\n{system_prompt}" if system_prompt else inline_system
# 
#         max_tokens = int(state.get("max_tokens") or self.settings.default_max_tokens)
#         temperature = float(state.get("temperature") or self.settings.default_temperature)
#         workspace_root = state.get("workspace_root")
#         pending_approvals: List[Dict[str, Any]] = []
#         code_changes: List[Dict[str, Any]] = []
# 
#         for _ in range(MAX_TOOL_ROUNDS):
#             response = await self.provider.complete(
#                 model=self.settings.model_api_name,
#                 max_tokens=max_tokens,
#                 temperature=temperature,
#                 system=system_prompt,
#                 messages=model_messages,
#                 tools=WORKSPACE_TOOLS,
#             )
#             tool_uses = self._tool_uses(response)
#             if tool_uses:
#                 for tool_use in tool_uses:
#                     result = self._execute_workspace_tool(tool_use, workspace_root=workspace_root)
#                     code_change = self._code_change_from_tool_result(result)
#                     if code_change:
#                         code_changes.append(code_change)
#                     approval_request = self._approval_request_from_tool_result(
#                         result,
#                         tool_use,
#                         thread_id=str(state.get("thread_id") or ""),
#                         workspace_root=workspace_root,
#                     )
#                     if approval_request:
#                         pending_approvals.append(approval_request)
#                     model_messages.append(
#                         {
#                             "role": "tool",
#                             "tool_call_id": tool_use.id,
#                             "content": self._json_tool_result(result),
#                             "is_error": not bool(result.get("ok")),
#                         }
#                     )
#                 model_messages.insert(
#                     len(model_messages) - len(tool_uses),
#                     {
#                         "role": "assistant",
#                         "content": response.text,
#                         "tool_calls": [
#                             {
#                                 "id": tool_use.id,
#                                 "name": tool_use.name,
#                                 "input": tool_use.input,
#                             }
#                             for tool_use in tool_uses
#                         ],
#                     },
#                 )
#                 continue
# 
#             text = self._extract_text(response)
#             text_tool_uses = self._text_tool_uses(text)
#             if not text_tool_uses:
#                 return {
#                     "messages": [AIMessage(content=text)],
#                     "pending_approvals": pending_approvals,
#                     "code_changes": code_changes,
#                 }
# 
#             text_tool_results = []
#             for tool_use in text_tool_uses:
#                 result = self._execute_workspace_tool(tool_use, workspace_root=workspace_root)
#                 code_change = self._code_change_from_tool_result(result)
#                 if code_change:
#                     code_changes.append(code_change)
#                 approval_request = self._approval_request_from_tool_result(
#                     result,
#                     tool_use,
#                     thread_id=str(state.get("thread_id") or ""),
#                     workspace_root=workspace_root,
#                 )
#                 if approval_request:
#                     pending_approvals.append(approval_request)
#                 text_tool_results.append(result)
#             model_messages.append({"role": "assistant", "content": text})
#             model_messages.append(
#                 {"role": "user", "content": self._text_tool_results_message(text_tool_results)}
#             )
# 
#         return {
#             "messages": [
#                 AIMessage(
#                     content=(
#                         "工具调用次数过多，已停止继续执行。请缩小任务范围或明确下一步。"
#                     )
#                 )
#             ],
#             "pending_approvals": pending_approvals,
#             "code_changes": code_changes,
#         }
# 
#     async def chat(
#         self,
#         message: str,
#         *,
#         session_id: Optional[str] = None,
#         system_prompt: Optional[str] = None,
#         workspace_root: Optional[str] = None,
#         temperature: Optional[float] = None,
#         max_tokens: Optional[int] = None,
#         approval_decision: Optional[Dict[str, Any]] = None,
#     ) -> Dict[str, object]:
#         thread_id = session_id or str(uuid4())
#         messages: List[BaseMessage] = [HumanMessage(content=message)]
#         approval_context, approval_code_changes = self._approval_decision_context(
#             approval_decision,
#             thread_id=thread_id,
#             workspace_root=workspace_root,
#         )
#         if approval_context:
#             messages.append(HumanMessage(content=approval_context))
#         state: AgentState = {"messages": messages, "thread_id": thread_id}
#         if system_prompt:
#             state["system_prompt"] = system_prompt
#         if workspace_root:
#             state["workspace_root"] = workspace_root
#         if temperature is not None:
#             state["temperature"] = temperature
#         if max_tokens is not None:
#             state["max_tokens"] = max_tokens
# 
#         result = await self.graph.ainvoke(
#             state,
#             config={"configurable": {"thread_id": thread_id}},
#         )
#         answer = self._last_ai_message(result["messages"])
#         approvals = self._pending_approvals_from_value(result.get("pending_approvals"))
#         code_changes = self._dedupe_code_changes(
#             [
#                 *approval_code_changes,
#                 *self._code_changes_from_value(result.get("code_changes")),
#                 *self._pending_code_changes_for_approvals(approvals),
#             ]
#         )
#         code_change_set = self._code_change_set(
#             code_changes,
#             approvals=approvals,
#             workspace_root=workspace_root,
#         )
#         return {
#             "session_id": thread_id,
#             "model": self.settings.model_api_name,
#             "answer": answer,
#             "messages": self._serialize_messages(result["messages"]),
#             "approval": self._latest_pending_approval(approvals),
#             "approvals": approvals,
#             "codeChanges": code_change_set,
#         }
# 
#     def _compose_system_prompt(
#         self,
#         client_system_prompt: Optional[str],
#         *,
#         latest_user_message: str,
#     ) -> str:
#         prompt_parts = [
#             APPLICATION_DEVELOPER_PROMPT,
#             self.settings.default_system_prompt,
#             client_system_prompt,
#             WORKSPACE_TOOL_PROMPT,
#             load_react_antd_v4_codegen_prompt(),
#             build_workspace_tools_prompt_context(),
#             build_prompt_context(latest_user_message),
#         ]
#         return "\n\n".join(part.strip() for part in prompt_parts if part and part.strip())
# 
#     @staticmethod
#     def _latest_user_message(messages: List[BaseMessage]) -> str:
#         for message in reversed(messages):
#             if isinstance(message, HumanMessage):
#                 return AgentRuntime._message_text(message)
#         return ""
# 
#     @staticmethod
#     def _to_model_messages(messages: List[BaseMessage]) -> Tuple[List[Dict[str, Any]], str]:
#         output: List[Dict[str, str]] = []
#         system_parts: List[str] = []
# 
#         for message in messages:
#             content = AgentRuntime._message_text(message)
#             if not content:
#                 continue
#             if isinstance(message, SystemMessage):
#                 system_parts.append(content)
#             elif isinstance(message, AIMessage):
#                 output.append({"role": "assistant", "content": content})
#             else:
#                 output.append({"role": "user", "content": content})
# 
#         return output, "\n\n".join(system_parts)
# 
#     @staticmethod
#     def _extract_text(response: ModelResponse) -> str:
#         return response.text.strip()
# 
#     @staticmethod
#     def _tool_uses(response: ModelResponse) -> List[Any]:
#         return response.tool_calls
# 
#     @staticmethod
#     def _text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for index, tool_use in enumerate(AgentRuntime._json_text_tool_uses(text), start=1):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{index}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         tool_call_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(
#             AgentRuntime._tool_call_text_tool_uses(text),
#             start=tool_call_start,
#         ):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         equals_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(
#             AgentRuntime._function_equals_text_tool_uses(text),
#             start=equals_start,
#         ):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         wrapper_start = len(tool_uses) + 1
#         for index, match in enumerate(TEXT_TOOL_WRAPPER_RE.finditer(text), start=wrapper_start):
#             body = match.group("body")
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{index}",
#                     name=html.unescape(match.group("name").strip()),
#                     input=AgentRuntime._parse_text_tool_body_or_arguments(body),
#                 )
#             )
#         invoke_start = len(tool_uses) + 1
#         for index, match in enumerate(TEXT_TOOL_INVOKE_RE.finditer(text), start=invoke_start):
#             body = match.group("body")
#             payload: Dict[str, Any] = {}
#             for argument in TEXT_TOOL_ARGUMENT_RE.finditer(body):
#                 payload[argument.group("name")] = AgentRuntime._parse_text_tool_value(
#                     argument.group("value")
#                 )
#             if not payload:
#                 payload = AgentRuntime._parse_text_tool_body(body)
#             payload = AgentRuntime._normalize_tool_payload(payload)
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{index}",
#                     name=html.unescape(match.group("name").strip()),
#                     input=payload,
#                 )
#             )
#         self_closing_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(
#             AgentRuntime._self_closing_text_tool_uses(text),
#             start=self_closing_start,
#         ):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         wrapped_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(
#             AgentRuntime._wrapped_text_tool_uses(text),
#             start=wrapped_start,
#         ):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         shell_redirect_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(
#             AgentRuntime._shell_redirect_text_tool_uses(text),
#             start=shell_redirect_start,
#         ):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         plain_start = len(tool_uses) + 1
#         for offset, tool_use in enumerate(AgentRuntime._plain_text_tool_uses(text), start=plain_start):
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"text-tool-{offset}",
#                     name=tool_use.name,
#                     input=tool_use.input,
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _function_equals_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for match in TEXT_FUNCTION_EQUALS_RE.finditer(text):
#             name = html.unescape(match.group("name").strip())
#             if AgentRuntime._canonical_tool_name(name) not in WORKSPACE_TOOL_RUNNERS:
#                 continue
#             payload = {
#                 argument.group("name"): AgentRuntime._parse_text_tool_value(argument.group("value"))
#                 for argument in TEXT_EQUALS_ARGUMENT_RE.finditer(match.group("body"))
#             }
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"function-equals-tool-{len(tool_uses) + 1}",
#                     name=name,
#                     input=AgentRuntime._normalize_tool_payload(payload),
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _tool_call_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for match in TEXT_TOOL_CALL_WRAPPER_RE.finditer(text):
#             payload = AgentRuntime._parse_text_tool_body(match.group("body"))
#             tool_use = AgentRuntime._tool_use_from_json_payload(payload)
#             if tool_use is not None:
#                 tool_uses.append(tool_use)
#         return tool_uses
# 
#     @staticmethod
#     def _json_text_tool_uses(text: str) -> List[TextToolUse]:
#         value = text.strip()
#         if not value.startswith("{") and not value.startswith("["):
#             return []
#         try:
#             parsed = json.loads(value)
#         except json.JSONDecodeError:
#             return []
# 
#         items = parsed if isinstance(parsed, list) else [parsed]
#         tool_uses: List[TextToolUse] = []
#         for item in items:
#             if not isinstance(item, dict):
#                 continue
#             tool_use = AgentRuntime._tool_use_from_json_payload(item)
#             if tool_use is not None:
#                 tool_uses.append(
#                     TextToolUse(
#                         id=f"json-tool-{len(tool_uses) + 1}",
#                         name=tool_use.name,
#                         input=tool_use.input,
#                     )
#                 )
#         return tool_uses
# 
#     @staticmethod
#     def _tool_use_from_json_payload(payload: Dict[str, Any]) -> Optional[TextToolUse]:
#         name = payload.get("tool") or payload.get("name")
#         if not isinstance(name, str):
#             return None
#         if AgentRuntime._canonical_tool_name(name) not in WORKSPACE_TOOL_RUNNERS:
#             return None
# 
#         arguments = payload.get("arguments")
#         if isinstance(arguments, dict):
#             input_payload = arguments
#         else:
#             input_payload = {
#                 key: value
#                 for key, value in payload.items()
#                 if key not in {"tool", "name", "arguments"}
#             }
#         return TextToolUse(
#             id="json-tool",
#             name=name,
#             input=AgentRuntime._normalize_tool_payload(input_payload),
#         )
# 
#     @staticmethod
#     def _shell_redirect_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for line in text.splitlines():
#             payload = AgentRuntime._parse_echo_redirect_line(line)
#             if payload is None:
#                 continue
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"shell-redirect-tool-{len(tool_uses) + 1}",
#                     name="file_write",
#                     input=payload,
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _parse_echo_redirect_line(line: str) -> Optional[Dict[str, Any]]:
#         stripped = line.strip()
#         if not stripped or stripped.startswith("```"):
#             return None
#         try:
#             parts = shlex.split(stripped)
#         except ValueError:
#             return None
# 
#         if len(parts) >= 4 and parts[0] == "echo" and ">" in parts:
#             redirect_index = parts.index(">")
#             if redirect_index <= 1 or redirect_index + 1 >= len(parts):
#                 return None
#             return {
#                 "path": parts[redirect_index + 1],
#                 "content": " ".join(parts[1:redirect_index]),
#                 "overwrite": True,
#             }
# 
#         match = re.fullmatch(r"echo\s+(['\"])(?P<content>.*)\1\s*>\s*(?P<path>\S+)", stripped)
#         if not match:
#             return None
#         return {
#             "path": match.group("path"),
#             "content": match.group("content"),
#             "overwrite": True,
#         }
# 
#     @staticmethod
#     def _wrapped_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for match in WRAPPED_TEXT_TOOL_RE.finditer(text):
#             name = html.unescape(match.group("name").strip())
#             if AgentRuntime._canonical_tool_name(name) not in WORKSPACE_TOOL_RUNNERS:
#                 continue
#             payload = AgentRuntime._parse_text_tool_body_or_arguments(match.group("body"))
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"wrapped-tool-{len(tool_uses) + 1}",
#                     name=name,
#                     input=payload,
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _self_closing_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for match in SELF_CLOSING_TEXT_TOOL_RE.finditer(text):
#             name = html.unescape(match.group("name").strip())
#             if AgentRuntime._canonical_tool_name(name) not in WORKSPACE_TOOL_RUNNERS:
#                 continue
#             payload = {
#                 attribute.group("name"): AgentRuntime._parse_text_tool_value(
#                     attribute.group("value")
#                 )
#                 for attribute in TEXT_TOOL_ATTRIBUTE_RE.finditer(match.group("attrs"))
#             }
#             payload = AgentRuntime._normalize_tool_payload(payload)
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"self-closing-tool-{len(tool_uses) + 1}",
#                     name=name,
#                     input=payload,
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _plain_text_tool_uses(text: str) -> List[TextToolUse]:
#         tool_uses: List[TextToolUse] = []
#         for line in text.splitlines():
#             parsed = AgentRuntime._parse_plain_tool_line(line)
#             if parsed is None:
#                 continue
#             name, payload = parsed
#             tool_uses.append(
#                 TextToolUse(
#                     id=f"plain-tool-{len(tool_uses) + 1}",
#                     name=name,
#                     input=payload,
#                 )
#             )
#         return tool_uses
# 
#     @staticmethod
#     def _parse_plain_tool_line(line: str) -> Optional[Tuple[str, Dict[str, Any]]]:
#         stripped = line.strip()
#         if not stripped or stripped.startswith("<"):
#             return None
# 
#         for tool_name in sorted(WORKSPACE_TOOL_RUNNERS, key=len, reverse=True):
#             candidates = (tool_name, tool_name.replace("_", "."))
#             for candidate in candidates:
#                 if stripped == candidate:
#                     return candidate, {}
#                 prefix = f"{candidate} "
#                 if stripped.startswith(prefix):
#                     return candidate, AgentRuntime._parse_plain_tool_arguments(
#                         stripped[len(prefix) :]
#                     )
#         return None
# 
#     @staticmethod
#     def _parse_plain_tool_arguments(value: str) -> Dict[str, Any]:
#         stripped = value.strip()
#         if not stripped:
#             return {}
#         if stripped.startswith("{"):
#             return AgentRuntime._parse_text_tool_body_or_arguments(stripped)
# 
#         matches = list(re.finditer(r"(?<!\S)([A-Za-z_][\w]*)=", stripped))
#         if not matches:
#             return {}
# 
#         payload: Dict[str, Any] = {}
#         for index, match in enumerate(matches):
#             key = match.group(1)
#             start = match.end()
#             end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
#             raw_value = stripped[start:end].strip()
#             if (raw_value.startswith('"') and raw_value.endswith('"')) or (
#                 raw_value.startswith("'") and raw_value.endswith("'")
#             ):
#                 raw_value = raw_value[1:-1]
#             payload[key] = AgentRuntime._parse_text_tool_value(raw_value)
#         return AgentRuntime._normalize_tool_payload(payload)
# 
#     @staticmethod
#     def _parse_text_tool_body(body: str) -> Dict[str, Any]:
#         value = html.unescape(body.strip())
#         if not value:
#             return {}
#         try:
#             parsed = json.loads(value)
#         except json.JSONDecodeError:
#             return {}
#         return parsed if isinstance(parsed, dict) else {}
# 
#     @staticmethod
#     def _parse_text_tool_body_or_arguments(body: str) -> Dict[str, Any]:
#         payload = AgentRuntime._parse_text_tool_body(body)
#         if payload:
#             return AgentRuntime._normalize_tool_payload(payload)
# 
#         payload = {
#             argument.group("name"): AgentRuntime._parse_text_tool_value(argument.group("value"))
#             for argument in SIMPLE_TEXT_ARGUMENT_RE.finditer(body)
#         }
#         return AgentRuntime._normalize_tool_payload(payload)
# 
#     @staticmethod
#     def _parse_text_tool_value(value: str) -> Any:
#         parsed = html.unescape(value.strip())
#         if parsed.startswith("{") or parsed.startswith("["):
#             try:
#                 return json.loads(parsed)
#             except json.JSONDecodeError:
#                 return parsed
#         return parsed
# 
#     @staticmethod
#     def _response_content_payload(response) -> List[Dict[str, Any]]:
#         payload: List[Dict[str, Any]] = []
#         for block in response.content:
#             block_type = getattr(block, "type", None)
#             if block_type == "text":
#                 payload.append({"type": "text", "text": getattr(block, "text", "")})
#             elif block_type == "tool_use":
#                 payload.append(
#                     {
#                         "type": "tool_use",
#                         "id": getattr(block, "id", ""),
#                         "name": getattr(block, "name", ""),
#                         "input": getattr(block, "input", {}) or {},
#                     }
#                 )
#         return payload
# 
#     @staticmethod
#     def _tool_result_payload(tool_use: Any, *, workspace_root: Optional[str]) -> Dict[str, Any]:
#         result = AgentRuntime._execute_workspace_tool(tool_use, workspace_root=workspace_root)
#         return AgentRuntime._tool_result_payload_from_result(tool_use, result)
# 
#     @staticmethod
#     def _tool_result_payload_from_result(tool_use: Any, result: Dict[str, Any]) -> Dict[str, Any]:
#         tool_use_id = getattr(tool_use, "id", "")
#         return {
#             "type": "tool_result",
#             "tool_use_id": tool_use_id,
#             "content": AgentRuntime._json_tool_result(result),
#             "is_error": not bool(result.get("ok")),
#         }
# 
#     def _approval_request_from_tool_result(
#         self,
#         result: Dict[str, Any],
#         tool_use: Any,
#         *,
#         thread_id: str,
#         workspace_root: Optional[str],
#     ) -> Optional[Dict[str, Any]]:
#         tool_result = result.get("result")
#         if not isinstance(tool_result, dict) or not tool_result.get("requires_approval"):
#             return None
# 
#         approval = tool_result.get("approval")
#         if not isinstance(approval, dict) or not approval.get("id"):
#             return None
# 
#         approval_id = str(approval["id"])
#         raw_input = getattr(tool_use, "input", {}) or {}
#         tool_name = AgentRuntime._canonical_tool_name(getattr(tool_use, "name", ""))
#         code_change = self._code_change_from_tool_result(result)
#         if isinstance(raw_input, dict):
#             self._pending_tool_requests[approval_id] = PendingAgentToolRequest(
#                 thread_id=thread_id,
#                 tool_name=tool_name,
#                 input=AgentRuntime._normalize_tool_payload(dict(raw_input)),
#                 workspace_root=workspace_root,
#                 code_change=code_change,
#             )
# 
#         return {
#             **approval,
#             "agent_tool": tool_name,
#         }
# 
#     def _approval_decision_context(
#         self,
#         approval_decision: Optional[Dict[str, Any]],
#         *,
#         thread_id: str,
#         workspace_root: Optional[str],
#     ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
#         if not isinstance(approval_decision, dict):
#             return None, []
# 
#         decision_items = self._approval_decision_items(approval_decision)
#         if not decision_items:
#             return None, []
# 
#         contexts: List[str] = []
#         code_changes: List[Dict[str, Any]] = []
#         for item in decision_items:
#             context, item_code_changes = self._approval_decision_item_context(
#                 item,
#                 thread_id=thread_id,
#                 workspace_root=workspace_root,
#             )
#             if context:
#                 contexts.append(context)
#             code_changes.extend(item_code_changes)
# 
#         return "\n\n".join(contexts) if contexts else None, code_changes
# 
#     @staticmethod
#     def _approval_decision_items(approval_decision: Dict[str, Any]) -> List[Dict[str, Any]]:
#         raw_decisions = approval_decision.get("decisions")
#         if isinstance(raw_decisions, list):
#             parent_action = str(approval_decision.get("action") or "")
#             parent_feedback = str(approval_decision.get("feedback") or "").strip()
#             items: List[Dict[str, Any]] = []
#             for raw_item in raw_decisions:
#                 if not isinstance(raw_item, dict):
#                     continue
#                 item = dict(raw_item)
#                 if parent_action and not item.get("action"):
#                     item["action"] = parent_action
#                 if parent_feedback and not item.get("feedback"):
#                     item["feedback"] = parent_feedback
#                 items.append(item)
#             return items
#         return [approval_decision]
# 
#     def _approval_decision_item_context(
#         self,
#         approval_decision: Dict[str, Any],
#         *,
#         thread_id: str,
#         workspace_root: Optional[str],
#     ) -> Tuple[Optional[str], List[Dict[str, Any]]]:
#         action = str(approval_decision.get("action") or "")
#         feedback = str(approval_decision.get("feedback") or "").strip()
#         if action == "feedback":
#             approval_id = str(approval_decision.get("approvalId") or "")
#             if approval_id:
#                 self._pending_tool_requests.pop(approval_id, None)
#             return (
#                 "用户没有批准此前待执行的受保护工具调用。"
#                 f"用户补充意见：{feedback or '未提供补充意见。'}"
#             ), []
# 
#         if action not in {"approve_once", "approve_always"}:
#             return None, []
# 
#         approval_id = str(approval_decision.get("approvalId") or "")
#         if not approval_id:
#             return "用户已批准，但审批消息缺少 approvalId，无法继续执行受保护工具。", []
# 
#         pending_request = self._pending_tool_requests.get(approval_id)
#         if pending_request is None or pending_request.thread_id != thread_id:
#             return (
#                 "用户已批准，但后端没有找到对应的待执行工具请求。"
#                 "可能是审批已过期、后端已重启，或会话线程不匹配。"
#             ), []
# 
#         payload = dict(pending_request.input)
#         grant = approval_decision.get("grant")
#         if isinstance(grant, dict) and grant.get("id") and grant.get("token"):
#             payload["approval"] = {"id": str(grant["id"]), "token": str(grant["token"])}
# 
#         tool_use = TextToolUse(
#             id=f"approval-{approval_id}",
#             name=pending_request.tool_name,
#             input=payload,
#         )
#         result = self._execute_workspace_tool(
#             tool_use,
#             workspace_root=workspace_root or pending_request.workspace_root,
#         )
#         tool_result = result.get("result")
#         if not (isinstance(tool_result, dict) and tool_result.get("requires_approval")):
#             self._pending_tool_requests.pop(approval_id, None)
# 
#         code_change = self._code_change_from_tool_result(result)
#         scope_label = "后续相同操作也已放行" if action == "approve_always" else "仅本次放行"
#         return (
#             f"用户已批准此前待执行的受保护工具调用（{scope_label}）。"
#             "后端已按该审批执行暂存的工具请求，结果如下。请基于这个结果继续完成用户任务，"
#             "不要要求用户重复批准同一个已执行请求。\n\n"
#             f"{self._json_tool_result({'approval_execution_result': result})}"
#         ), ([code_change] if code_change else [])
# 
#     @staticmethod
#     def _code_change_from_tool_result(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
#         tool_result = result.get("result")
#         if not isinstance(tool_result, dict):
#             return None
#         code_change = tool_result.get("code_change")
#         if not isinstance(code_change, dict):
#             return None
#         path = code_change.get("path")
#         tool = code_change.get("tool")
#         if not isinstance(path, str) or not isinstance(tool, str):
#             return None
#         return dict(code_change)
# 
#     @staticmethod
#     def _code_changes_from_value(value: Any) -> List[Dict[str, Any]]:
#         if not isinstance(value, list):
#             return []
#         return [dict(item) for item in value if isinstance(item, dict)]
# 
#     def _pending_code_changes_for_approvals(
#         self,
#         approvals: List[Dict[str, Any]],
#     ) -> List[Dict[str, Any]]:
#         code_changes: List[Dict[str, Any]] = []
#         for approval in approvals:
#             approval_id = str(approval.get("id") or "")
#             pending_request = self._pending_tool_requests.get(approval_id)
#             if pending_request is None or pending_request.code_change is None:
#                 continue
#             code_changes.append({**pending_request.code_change, "approvalId": approval_id})
#         return code_changes
# 
#     @staticmethod
#     def _dedupe_code_changes(code_changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
#         deduped: List[Dict[str, Any]] = []
#         seen: set[str] = set()
#         for item in code_changes:
#             key = str(
#                 item.get("id")
#                 or (
#                     item.get("tool"),
#                     item.get("path"),
#                     item.get("approvalId"),
#                     item.get("diff"),
#                 )
#             )
#             if key in seen:
#                 continue
#             seen.add(key)
#             deduped.append(item)
#         return deduped
# 
#     @staticmethod
#     def _pending_approvals_from_value(value: Any) -> List[Dict[str, Any]]:
#         if not isinstance(value, list):
#             return []
#         return [dict(item) for item in value if isinstance(item, dict) and item.get("id")]
# 
#     @staticmethod
#     def _code_change_set(
#         code_changes: List[Dict[str, Any]],
#         *,
#         approvals: List[Dict[str, Any]],
#         workspace_root: Optional[str],
#     ) -> Optional[Dict[str, Any]]:
#         files = [item for item in code_changes if isinstance(item.get("path"), str)]
#         if not files:
#             return None
# 
#         unique_paths = {str(item.get("path")) for item in files}
#         additions = sum(AgentRuntime._safe_int(item.get("additions")) for item in files)
#         deletions = sum(AgentRuntime._safe_int(item.get("deletions")) for item in files)
#         has_pending = any(
#             not bool(item.get("executed")) and bool(item.get("approvalId"))
#             for item in files
#         )
#         status = "pending_approval" if has_pending or approvals else "applied"
#         digest_source = json.dumps(
#             {
#                 "workspaceRoot": workspace_root or "",
#                 "files": [
#                     {
#                         "id": item.get("id"),
#                         "path": item.get("path"),
#                         "approvalId": item.get("approvalId"),
#                         "executed": item.get("executed"),
#                     }
#                     for item in files
#                 ],
#             },
#             ensure_ascii=False,
#             sort_keys=True,
#             default=str,
#         )
#         change_id = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
#         return {
#             "id": f"code-changes:{change_id}",
#             "status": status,
#             "workspaceRoot": workspace_root or "",
#             "summary": {
#                 "files": len(unique_paths),
#                 "additions": additions,
#                 "deletions": deletions,
#             },
#             "files": files,
#             "approvals": approvals,
#         }
# 
#     @staticmethod
#     def _safe_int(value: Any) -> int:
#         try:
#             return int(value)
#         except (TypeError, ValueError):
#             return 0
# 
#     @staticmethod
#     def _latest_pending_approval(value: Any) -> Optional[Dict[str, Any]]:
#         if not isinstance(value, list):
#             return None
#         for item in reversed(value):
#             if isinstance(item, dict) and item.get("id"):
#                 return item
#         return None
# 
#     @staticmethod
#     def _execute_workspace_tool(tool_use: Any, *, workspace_root: Optional[str]) -> Dict[str, Any]:
#         name = AgentRuntime._canonical_tool_name(getattr(tool_use, "name", ""))
#         raw_input = getattr(tool_use, "input", {}) or {}
#         if not isinstance(raw_input, dict):
#             return {"ok": False, "tool": name, "error": "Tool input must be an object."}
# 
#         runner = WORKSPACE_TOOL_RUNNERS.get(name)
#         if runner is None:
#             return {"ok": False, "tool": name, "error": f"Unknown tool: {name}"}
# 
#         request_model, handler = runner
#         payload = AgentRuntime._normalize_tool_payload(dict(raw_input))
#         if workspace_root and not payload.get("workspace_root"):
#             payload["workspace_root"] = workspace_root
# 
#         try:
#             request = request_model(**payload)
#             result = handler(request)
#             return {"ok": not bool(result.get("requires_approval")), "tool": name, "result": result}
#         except ValidationError as exc:
#             return {"ok": False, "tool": name, "error": exc.errors()}
#         except HTTPException as exc:
#             return {
#                 "ok": False,
#                 "tool": name,
#                 "status_code": exc.status_code,
#                 "error": exc.detail,
#             }
#         except Exception as exc:  # pragma: no cover - defensive boundary for tool loop
#             return {"ok": False, "tool": name, "error": str(exc)}
# 
#     @staticmethod
#     def _canonical_tool_name(name: str) -> str:
#         return name.strip().replace(".", "_").replace("-", "_")
# 
#     @staticmethod
#     def _normalize_tool_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
#         normalized = dict(payload)
#         aliases = {
#             "file_path": "path",
#             "filepath": "path",
#             "workspaceRoot": "workspace_root",
#             "workspace-root": "workspace_root",
#             "workspace": "workspace_root",
#         }
#         for source, target in aliases.items():
#             if source in normalized and target not in normalized:
#                 normalized[target] = normalized[source]
#         return normalized
# 
#     @staticmethod
#     def _text_tool_results_message(results: List[Dict[str, Any]]) -> str:
#         return (
#             "Local workspace tool results are below. Do not repeat the XML/function-call text. "
#             "Use these results to produce the final user-facing answer, unless another tool call is truly needed.\n\n"
#             f"{AgentRuntime._json_tool_result({'tool_results': results})}"
#         )
# 
#     @staticmethod
#     def _json_tool_result(result: Dict[str, Any]) -> str:
#         text = json.dumps(result, ensure_ascii=False, default=str)
#         if len(text) <= MAX_TOOL_RESULT_CHARS:
#             return text
#         return text[:MAX_TOOL_RESULT_CHARS].rstrip() + "\n... [truncated]"
# 
#     @staticmethod
#     def _last_ai_message(messages: List[BaseMessage]) -> str:
#         for message in reversed(messages):
#             if isinstance(message, AIMessage):
#                 return AgentRuntime._message_text(message)
#         return ""
# 
#     @staticmethod
#     def _message_text(message: BaseMessage) -> str:
#         content = message.content
#         if isinstance(content, str):
#             return content
#         if isinstance(content, list):
#             text_parts: List[str] = []
#             for item in content:
#                 if isinstance(item, str):
#                     text_parts.append(item)
#                 elif isinstance(item, dict) and item.get("type") == "text":
#                     text_parts.append(str(item.get("text", "")))
#             return "\n".join(part for part in text_parts if part).strip()
#         return str(content)
# 
#     @staticmethod
#     def _serialize_messages(messages: List[BaseMessage]) -> List[Dict[str, str]]:
#         serialized: List[Dict[str, str]] = []
#         for message in messages:
#             role: Literal["assistant", "system", "user"]
#             if isinstance(message, AIMessage):
#                 role = "assistant"
#             elif isinstance(message, SystemMessage):
#                 role = "system"
#             else:
#                 role = "user"
#             serialized.append({"role": role, "content": AgentRuntime._message_text(message)})
#         return serialized
