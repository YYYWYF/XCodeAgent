import { defineConfig, type Plugin } from 'vite'
import tailwindcss from '@tailwindcss/vite'

/** 检查真实生产模块图，拒绝开发工具、源码 catalog 和 Node 内建模块进入页面。 */
function productionBoundary(): Plugin {
  return {
    name: 'agent-ui-production-boundary',
    generateBundle() {
      for (const id of this.getModuleIds()) {
        if (/agent-ui-creator|source-registry|node:(fs|crypto)|__vite-browser-external/.test(id)) {
          this.error(`Development-only module in production graph: ${id}`)
        }
      }
    },
  }
}

export default defineConfig({
  plugins: [tailwindcss(), productionBoundary()],
  // 正文独立编译，防止共享 Agent 的扫描范围、主题覆盖和圆角变量影响普通页面。
  // https://vite.dev/config/shared-options.html#css-postcss
  css: {
    postcss: {
      plugins: [{
        postcssPlugin: 'agent-design-business-boundary',
        /** 仅隔离正文入口的编译结果，官方 Agent 与消费者外壳的样式保持原样。 */
        Once(root) {
          if (!root.source?.input.file?.replace(/\\/g, '/').endsWith('/src/business.css')) return
          const scope = '.agent-design-business'
          const animations = new Map<string, string>()
          root.walkAtRules(/keyframes$/, (rule) => {
            const scoped = `${rule.params}-design-business`
            animations.set(rule.params, scoped)
            rule.params = scoped
          })
          root.walkRules((rule) => {
            // 嵌套规则沿用外层限定；关键帧内部的百分比不能当作普通选择器。
            for (let parent = rule.parent; parent && parent.type !== 'root'; parent = parent.parent) {
              if (parent.type === 'rule' || (parent.type === 'atrule' && /keyframes$/.test(parent.name))) return
            }
            rule.selectors = [...new Set(rule.selectors.map((selector) =>
              /^(?::root|:host|html|body)$/.test(selector) ? scope : `${scope} ${selector}`))]
          })
          root.walkDecls((declaration) => {
            if (declaration.prop.startsWith('--animate-') || /^animation(?:-name)?$/.test(declaration.prop)) {
              declaration.value = declaration.value.split(/(\s+|,)/)
                .map((value) => animations.get(value) ?? value).join('')
            }
          })
        },
      }],
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 同一运行包保持同一分块，避免公开 facade 循环引用跨 chunk 执行。
        manualChunks(id) {
          if (id.includes('node_modules')) return 'agent-ui-vendor'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5188,
    strictPort: true,
    proxy: { '/agent': 'http://127.0.0.1:47835' },
  },
})
