import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, mkdir, readFile, writeFile, cp, symlink, rm, realpath } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { build, loadConfigFromFile } from 'vite'

const consumer = fileURLToPath(new URL('..', import.meta.url))
const require = createRequire(import.meta.url)
const postcss = createRequire(require.resolve('vite/package.json'))('postcss')

for (const [entry, cssMinify] of [
  ['src/main.tsx', false],
  ['../agent-ui-design/main.tsx', true],
]) {
  /** 用真实 Vite/Tailwind 构建消费者和设计生产者入口，覆盖压缩前后的样式边界。 */
  test(`business page utilities compile in ${entry} (minify=${cssMinify}) without changing Agent UI`, async (t) => {
    const root = await realpath(await mkdtemp(path.join(tmpdir(), 'agent-ui-business-styles-')))
    t.after(() => rm(root, { recursive: true, force: true }))
    await mkdir(path.join(root, 'src/pages'), { recursive: true })
    await mkdir(path.join(root, 'src/agent-ui'), { recursive: true })
    await symlink(path.join(consumer, 'node_modules'), path.join(root, 'node_modules'), 'dir')
    await cp(path.join(consumer, 'src/styles.css'), path.join(root, 'src/styles.css'))
    await cp(path.join(consumer, 'src/agent-ui/styles.css'), path.join(root, 'src/agent-ui/styles.css'))
    // 入口来自真实生产者，不能在测试中手工增加正文样式以绕过装配遗漏。
    const main = await readFile(path.join(consumer, entry), 'utf8')
    const imports = main.match(/^import ['"]\.\/[^'"\n]+\.css['"]/gm) ?? []
    if (imports.some((line) => line.includes('business.css'))) {
      await cp(path.join(consumer, 'src/business.css'), path.join(root, 'src/business.css'))
    }
    await writeFile(
      path.join(root, 'index.html'),
      '<html><head></head><body><script type="module" src="/src/main.ts"></script></body></html>',
    )
    await writeFile(
      path.join(root, 'src/pages/home.tsx'),
      `
    export const page = <main className="max-w-2xl bg-slate-100 text-slate-800 rounded-2xl border border-slate-200 shadow-sm px-4 sm:flex-row dark:bg-slate-950">
      <button className="bg-violet-600 hover:bg-violet-700 focus:ring-2 focus:ring-violet-500 dark:bg-violet-500 animate-spin">开始请假</button>
    </main>
  `,
    )
    /** 分别构建官方 Agent 基线和加入正文入口的结果，比较组件外部主题是否被改变。 */
    async function compile(withBusiness) {
      await writeFile(
        path.join(root, 'src/main.ts'),
        imports.filter((line) => withBusiness || !line.includes('business.css')).join('\n') +
          '\nimport "./agent-ui/styles.css"\n',
      )
      const loaded = await loadConfigFromFile(
        { command: 'build', mode: 'production' },
        path.join(consumer, 'vite.config.ts'),
      )
      const bundle = await build({
        ...loaded.config,
        configFile: false,
        root,
        logLevel: 'error',
        build: { write: false, minify: false, cssMinify },
      })
      const css = bundle.output
        .filter((item) => item.type === 'asset' && item.fileName.endsWith('.css'))
        .map((item) => item.source)
        .join('\n')
      return postcss.parse(css)
    }
    const baseline = await compile(false)
    const ast = await compile(true)
    const rules = []
    ast.walkRules((rule) => rules.push(rule))
    for (const selector of [
      '.bg-slate-100',
      '.bg-violet-600',
      '.border-slate-200',
      '.max-w-2xl',
      '.rounded-2xl',
      '.hover\\:bg-violet-700',
      '.focus\\:ring-violet-500',
      '.dark\\:bg-slate-950',
      '.sm\\:flex-row',
    ]) {
      assert.ok(
        rules.some((rule) => rule.selector.includes(`.agent-design-business ${selector}`)),
        `Missing scoped business utility: ${selector}`,
      )
    }
    for (const property of ['--color-slate-100', '--color-violet-600', '--radius-2xl', '--container-2xl']) {
      const declarations = []
      ast.walkDecls(property, (declaration) => declarations.push(declaration))
      assert.ok(
        declarations.some((declaration) => declaration.parent.selector === '.agent-design-business'),
        `Missing business theme token: ${property}`,
      )
      const original = []
      baseline.walkDecls(property, (declaration) => original.push([declaration.parent.selector, declaration.value]))
      assert.deepEqual(
        declarations
          .filter((declaration) => declaration.parent.selector !== '.agent-design-business')
          .map((declaration) => [declaration.parent.selector, declaration.value]),
        original,
        `Agent theme token changed outside the business owner: ${property}`,
      )
    }
    // 原有 Agent 主题的圆角工具仍引用其自身变量；正文使用独立的默认圆角变量。
    assert.ok(
      rules.some(
        (rule) => rule.selector === '.rounded-2xl' && rule.nodes.some((node) => node.value?.includes('var(--radius)')),
      ),
    )
    assert.ok(
      rules.some(
        (rule) =>
          rule.selector === '.agent-design-business .rounded-2xl' &&
          rule.nodes.some((node) => node.value?.includes('var(--radius-2xl)')),
      ),
    )
    assert.ok(
      rules.some((rule) => rule.selector === '.consumer'),
      'Consumer shell styles must remain unscoped',
    )
    let scopedAnimation = false
    ast.walkAtRules('keyframes', (rule) => {
      if (rule.params === 'spin-design-business') scopedAnimation = true
    })
    assert.ok(scopedAnimation, 'Business animation names must be isolated')
  })
}
