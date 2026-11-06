"""只读解析目标构建入口与输出目录，不执行宿主配置或选择第二套构建工具。"""

from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import re
from typing import Any

from tree_sitter import Node
from tree_sitter_language_pack import get_parser


def validation_stages(package: dict[str, Any]) -> list[str]:
    """复用明确的类型/构建脚本；单元测试由开发流程自身确认门管理。"""
    scripts = package.get("scripts", {})
    build, typecheck = scripts.get("build"), scripts.get("typecheck")
    checked_build = isinstance(build, str) and build.split() == ["tsc", "-b", "&&", "rsbuild", "build"]
    if (not isinstance(build, str) or not build.strip()
            or not (isinstance(typecheck, str) and typecheck.strip() or checked_build)):
        raise ValueError("目标必须已有 typecheck 和 build 脚本，或当前模板内含 tsc -b 的 rsbuild 构建。")
    return [name for name in ("lint", "typecheck", "build") if name in scripts]


def _string(node: Node | None) -> str:
    """只读取没有转义和插值的明确字符串，路径仍由调用方做物理边界检查。"""
    if node is None or node.type != "string" or b"\\" in node.text:
        raise ValueError("入口和输出目录必须使用明确字符串，动态配置需单独审阅。")
    return node.text.decode("utf-8")[1:-1]


def _fields(node: Node | None) -> dict[str, Node]:
    """只接受静态对象键，拒绝展开、计算键与重复键覆盖实际布局。"""
    if node is None or node.type != "object":
        raise ValueError("构建布局必须使用静态对象配置。")
    result: dict[str, Node] = {}
    for item in node.named_children:
        if item.type == "comment":
            continue
        key = item.child_by_field_name("key")
        value = item.child_by_field_name("value")
        if item.type != "pair" or key is None or value is None or key.type not in {"property_identifier", "string"}:
            raise ValueError("构建布局存在动态字段，需单独审阅。")
        name = _string(key) if key.type == "string" else key.text.decode("utf-8")
        if name in result:
            raise ValueError("构建布局存在重复字段。")
        result[name] = value
    return result


def _configuration(content: str, bundler: str) -> Node:
    """复用现有 TypeScript grammar 解析唯一静态默认导出，绝不加载目标模块。"""
    root = get_parser("typescript").parse(content.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError("构建配置语法无效。")
    exported = [item for item in root.named_children if item.type == "export_statement"
                and any(child.type == "default" for child in item.children)]
    if len(exported) != 1:
        raise ValueError("构建配置必须唯一默认导出。")
    config = exported[0].child_by_field_name("value")
    if config is not None and config.type == "call_expression":
        imports: set[str] = set()
        for item in root.named_children:
            if item.type != "import_statement" or _string(item.child_by_field_name("source")) != (
                "@rsbuild/core" if bundler == "rsbuild" else "vite"
            ):
                continue
            clause = next((child for child in item.named_children if child.type == "import_clause"), None)
            bindings = next((child for child in clause.named_children if child.type == "named_imports"), None) if clause else None
            for binding in bindings.named_children if bindings else ():
                name = binding.child_by_field_name("name")
                if (name is not None and name.text == b"defineConfig"
                        and not any(child.type == "type" for child in (*item.children, *binding.children))):
                    alias = binding.child_by_field_name("alias")
                    imports.add((alias if alias is not None else name).text.decode())
        function = config.child_by_field_name("function")
        arguments = config.child_by_field_name("arguments")
        values = [item for item in arguments.named_children if item.type != "comment"] if arguments else []
        if function is None or function.type != "identifier" or function.text.decode() not in imports or len(values) != 1:
            raise ValueError("仅支持公共 defineConfig 的唯一静态参数。")
        config = values[0]
    if config is None:
        raise ValueError("构建配置缺少默认导出对象。")
    return config


def inspect_dependency_config(content: str, bundler: str) -> dict[str, str]:
    """从已解析的静态配置读取入口与输出，不加载其他运行期字段。"""
    fields = _fields(_configuration(content, bundler))
    entry, output = "", "dist"
    if bundler == "rsbuild":
        source = _fields(fields["source"]) if "source" in fields else {}
        if "entry" in source:
            entries = _fields(source["entry"])
            if len(entries) != 1:
                raise ValueError("共享 UI 依赖准备仅支持唯一前端入口。")
            entry = _string(next(iter(entries.values())))
        result = _fields(fields["output"]) if "output" in fields else {}
        if "distPath" in result:
            dist = result["distPath"]
            output = _string(dist if dist.type == "string" else _fields(dist).get("root"))
    else:
        if "root" in fields:
            raise ValueError("自定义 Vite root 需单独审阅。")
        build = _fields(fields["build"]) if "build" in fields else {}
        if "rollupOptions" in build:
            raise ValueError("自定义 Vite 构建入口需单独审阅。")
        if "outDir" in build:
            output = _string(build["outDir"])
    return {"entry": entry.removeprefix("./"), "outputDir": output.removeprefix("./")}


def rsbuild_postcss_changes(target: Path) -> dict[str, str]:
    """将当前模板丢失插件的浅覆盖列入差异，未知 PostCSS 自定义不自动接管。"""
    from app.services.agent_ui_dependency_plan import safe_path

    configs = sorted(target.glob("rsbuild.config.*"))
    if not configs:
        return {}
    path = safe_path(target, configs[0].name)
    content = path.read_text(encoding="utf-8")
    fields = _fields(_configuration(content, "rsbuild"))
    tools = fields.get("tools")
    if tools is None:
        return {}
    settings = _fields(tools)
    if "postcss" not in settings:
        return {}
    postcss = _fields(settings["postcss"])
    options = _fields(postcss.get("postcssOptions"))
    # Rsbuild 以浅合并替换整个 postcssOptions，随后禁用 config 自动加载；
    # 这里只移除模板的 config-only 覆盖，让既有 PostCSS 配置按默认入口生效。
    reference = options.get("config")
    expected = r'''path\s*\.\s*resolve\s*\(\s*projectRoot\s*,\s*(['"])(postcss\.config\.(?:js|mjs))\1\s*\)'''
    match = re.fullmatch(expected, reference.text.decode() if reference else "")
    if set(postcss) != {"postcssOptions"} or set(options) != {"config"} or match is None:
        raise ValueError("已有 Rsbuild PostCSS 覆盖需要定向审阅，不能自动接管。")
    if not safe_path(target, match[2]).is_file():
        raise ValueError("Rsbuild 引用的 PostCSS 文件不存在。")
    pair = settings["postcss"].parent
    if pair is None:
        raise ValueError("无法定位 PostCSS 配置边界。")
    start, end = pair.start_byte, pair.end_byte
    following, previous = pair.next_sibling, pair.prev_sibling
    if following is not None and following.type == ",":
        end = following.end_byte
    elif previous is not None and previous.type == ",":
        start = previous.start_byte
    data = content.encode("utf-8")
    line_start = data.rfind(b"\n", 0, start) + 1
    line_end = data.find(b"\n", end)
    line_end = len(data) if line_end == -1 else line_end
    if not data[line_start:start].strip() and not data[end:line_end].strip():
        start, end = line_start, min(line_end + 1, len(data))
    return {path.name: (data[:start] + data[end:]).decode("utf-8")}


class _ModuleEntries(HTMLParser):
    """读取 Vite 当前 HTML 中唯一实际模块入口，不按存在的源码文件猜测。"""

    def __init__(self) -> None:
        """初始化只读入口列表。"""
        super().__init__()
        self.entries: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """拒绝重复属性和内联模块歧义，仅收集 script module 的 src。"""
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "module":
            if len(attributes) != len(attrs):
                raise ValueError("HTML 模块入口存在重复属性。")
            self.entries.append(attributes.get("src") or "")


def current_dependency_layout(target: Path, package: dict[str, Any], entry: str = "") -> dict[str, str]:
    """从明确构建脚本和当前静态配置推导入口/输出，配置与源码均由原快照保护。"""
    from app.services.agent_ui_dependency_plan import safe_path

    build = (package.get("scripts") or {}).get("build", "")
    command = str(build).split("&&")[-1].split()
    if command not in (["rsbuild", "build"], ["vite", "build"]):
        raise ValueError("目标构建工具不明确，需单独审阅前端入口与输出目录。")
    bundler = command[0]
    configs = sorted(target.glob(bundler + ".config.*"))
    if len(configs) > 1:
        raise ValueError("目标存在多份构建配置，请明确唯一入口。")
    layout = {"entry": "", "outputDir": "dist"}
    if configs:
        path = safe_path(target, configs[0].name)
        if path.suffix not in {".ts", ".js", ".mjs", ".mts"} or not path.is_file() or path.stat().st_size > 65536:
            raise ValueError("构建配置类型或大小不受支持，需单独审阅。")
        layout = inspect_dependency_config(path.read_text(encoding="utf-8"), bundler)
    actual = layout["entry"]
    if not actual:
        if bundler == "rsbuild":
            actual = "src/index.tsx"
        else:
            parser = _ModuleEntries()
            parser.feed(safe_path(target, "index.html").read_text(encoding="utf-8"))
            if len(parser.entries) != 1 or not parser.entries[0] or parser.entries[0].startswith("//"):
                raise ValueError("Vite HTML 必须声明唯一明确的模块入口。")
            actual = parser.entries[0].removeprefix("/").removeprefix("./")
    if entry and entry != actual:
        raise ValueError("审阅入口与当前构建配置不一致。")
    if not actual.startswith("src/") or PurePosixPath(actual).suffix not in {".tsx", ".jsx", ".ts", ".js"}:
        raise ValueError("样式只能接入 src 中明确审阅的前端入口。")
    if not safe_path(target, actual).is_file():
        raise ValueError("构建声明的前端入口不存在。")
    output = layout["outputDir"]
    if not output or output in {"src", "node_modules"} or output.startswith(("src/", "node_modules/")):
        raise ValueError("构建输出必须与源码和依赖目录独立。")
    safe_path(target, output)
    return {"bundler": bundler, "entry": actual, "outputDir": output}
