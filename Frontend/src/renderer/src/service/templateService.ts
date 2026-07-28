/** 模板元信息，与 templates/<name>/manifest.json 对应 */
export interface PageTemplateManifest {
  id: string;
  name: string;
  /** 场景描述，建议不超过两行；卡片中超出部分省略，hover 显示完整内容 */
  description: string;
  /** 渲染后的页面预览图 URL，用于在模板卡片中展示并支持 hover 放大 */
  previewImage?: string;
}

/** 模板完整信息 */
export interface PageTemplate {
  manifest: PageTemplateManifest;
  /** 模板目录名，如 "DefaultPage" */
  dirName: string;
  /** 模板源码在工程中的路径，供后端 LLM 使用 */
  sourcePath: string;
}

// ---------- import.meta.glob 自动发现所有模板 ----------
// 仅 eager 加载 manifest.json，页面 .tsx 文件不加载（避免依赖缺失导致构建失败）

const manifestModules = import.meta.glob<{ default: PageTemplateManifest }>(
  '../templates/*/manifest.json',
  { eager: true },
);

function extractDirName(path: string): string {
  return path.split('/').slice(-2, -1)[0];
}

/** 获取所有可用页面模板 */
export function getAvailableTemplates(): PageTemplate[] {
  const templates: PageTemplate[] = [];

  for (const [manifestPath, mod] of Object.entries(manifestModules)) {
    const dirName = extractDirName(manifestPath);
    const manifest = mod.default;

    templates.push({
      manifest,
      dirName,
      sourcePath: `src/renderer/src/templates/${dirName}`,
    });
  }

  return templates;
}

/** 根据模板 ID 获取模板信息 */
export function getTemplateById(id: string): PageTemplate | undefined {
  return getAvailableTemplates().find((t) => t.manifest.id === id);
}
