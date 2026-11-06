"""为当前 Rsbuild 目标准备可审阅的应用层构建适配，不写目标或更换构建器。"""

import json
from pathlib import Path

from app.services.agent_ui_dependency_layout import _configuration, _fields
from app.services.agent_ui_dependency_plan import safe_path, read_object, sha256

LOADER_PATH = "scripts/devagentstudio/agent-ui-source-loader.cjs"
DECLARATIONS = '''/** 由 Agent UI 的 Rsbuild 构建适配器实现有限的静态资源导入。 */
interface ImportMetaEnv { readonly DEV: boolean; }
interface ImportMeta {
  readonly hot: { data: Record<string, unknown>; dispose(callback: () => void): void } | undefined;
  glob<T = unknown>(patterns: string | string[], options: {
    eager: true; query?: '?raw'; import?: 'default';
  }): Record<string, T>;
}
declare module '*?raw' {
  const source: string;
  export default source;
}
'''


def prepare_rsbuild_adapter(target: Path, source_root: str, *, owned: dict[str, str] | None = None) -> dict[str, str]:
    """仅添加独立编译规则，已有自定义链或同名脚本不匹配时保留并要求审阅。"""
    package = read_object(target / "package.json")
    if "typescript" not in {**package.get("dependencies", {}), **package.get("devDependencies", {})}:
        raise ValueError("Rsbuild Agent UI 适配需要目标已有 TypeScript，不自动新增依赖。")
    configs = sorted(target.glob("rsbuild.config.*"))
    if len(configs) != 1:
        raise ValueError("Rsbuild 适配需要唯一明确构建配置。")
    config = safe_path(target, configs[0].name)
    text = config.read_text(encoding="utf-8")
    node = _configuration(text, "rsbuild")
    fields = _fields(node)
    tools = fields.get("tools")
    settings = _fields(tools) if tools else {}
    # 使用 Node 的 URL 转换以支持空格、Unicode 和 Windows 路径。
    statement = "import { fileURLToPath as __agentUIFileURLToPath } from 'node:url';\n"
    callback = '''(chain) => {
      chain.module.rule('devagentstudio-agent-ui')
        .test(/\\.[jt]sx?$/i)
        .include.add(__agentUIFileURLToPath(new URL(SOURCE, import.meta.url))).end()
        .enforce('pre').use('devagentstudio-agent-ui')
        .loader(__agentUIFileURLToPath(new URL(LOADER, import.meta.url)));
    }'''.replace("SOURCE", json.dumps("./" + source_root + "/", ensure_ascii=False)).replace(
        "LOADER", json.dumps("./" + LOADER_PATH))
    existing = settings.get("bundlerChain")
    if existing is not None:
        if existing.text.decode() != callback or statement not in text:
            raise ValueError("已有 Rsbuild bundlerChain 需定向审阅，不能自动覆盖。")
    else:
        if "__agentUIFileURLToPath" in text:
            raise ValueError("Rsbuild 适配标识符已存在，请先审阅冲突。")
        owner = tools if tools is not None else node
        data = text.encode("utf-8")
        position = owner.end_byte - 1
        prefix = data[:position].rstrip()
        separator = "" if prefix.endswith((b"{", b",")) else ","
        insertion = "\n    bundlerChain: " + callback + ",\n  "
        if tools is None:
            insertion = "\n  tools: {" + insertion + "},\n"
        text = statement + (prefix + separator.encode() + insertion.encode() + data[position:]).decode()
    files = {config.name: text, LOADER_PATH: Path(__file__).with_name("agent_ui_rsbuild_loader.cjs").read_text(),
             source_root + "/rsbuild.d.ts": DECLARATIONS}
    for name in (LOADER_PATH, source_root + "/rsbuild.d.ts"):
        path = safe_path(target, name)
        # 仅允许重新审阅平台上次写入且当前字节仍一致的文件，用户编辑仍作为冲突保留。
        if (path.exists() and path.read_text(encoding="utf-8") != files[name]
                and (owned or {}).get(name) != sha256(path.read_bytes())):
            raise ValueError(f"已有 {name} 与当前适配器不同，请先审阅，不能自动接管。")
    return files
