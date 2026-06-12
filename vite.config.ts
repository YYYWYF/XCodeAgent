import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import styleConfig from './src/config/style.json';

export default defineConfig({
  plugins: [react()],
  css: {
    preprocessorOptions: {
      less: {
        // Ant Design 的 Less 源码包含 JavaScript 表达式，需要开启此选项。
        javascriptEnabled: true,
        // 与 cx() 共用 style.json，保证 TSX 类名和 Less 选择器同步换前缀。
        additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
      },
    },
  },
});
