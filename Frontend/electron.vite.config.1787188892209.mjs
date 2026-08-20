// electron.vite.config.ts
import { resolve } from "path";
import { defineConfig } from "electron-vite";
import react from "@vitejs/plugin-react";

// src/renderer/src/config/style.json
var style_default = {
  classPrefix: "xa"
};

// electron.vite.config.ts
var supportedAppEnvs = ["dev", "st", "uat", "prd"];
var isSupportedAppEnv = (value) => supportedAppEnvs.includes(value);
var appEnv = process.env["APP_ENV"] ?? "dev";
if (!isSupportedAppEnv(appEnv)) {
  throw new Error(`Unsupported APP_ENV: ${appEnv}`);
}
var appEnvDefine = {
  "process.env.APP_ENV": JSON.stringify(appEnv)
};
var electron_vite_config_default = defineConfig({
  main: {
    define: appEnvDefine
  },
  preload: {
    define: appEnvDefine
  },
  renderer: {
    build: {
      rollupOptions: {
        input: {
          index: resolve("src/renderer/index.html"),
          login: resolve("src/renderer/login.html")
        }
      }
    },
    resolve: {
      alias: {
        "@renderer": resolve("src/renderer/src")
      }
    },
    css: {
      preprocessorOptions: {
        less: {
          // 与 cx() 共用 style.json，保证 TSX 类名和 Less 选择器同步换前缀。
          additionalData: `@class-prefix: ${style_default.classPrefix};`,
          javascriptEnabled: true,
          // antd v4 的 themes/index.less 用 `@import './@{root-entry-name}.less'`
          // 动态选择主题，但该变量默认未定义，less 4.x 会编译失败并抛出
          // "Cannot read properties of undefined (reading 'message')" 的二次错误。
          // 同时在 Ant Design 的编译入口统一品牌主色，未单独覆写的主按钮和交互态默认使用紫色。
          modifyVars: {
            "@root-entry-name": "default",
            "@primary-color": "#6b3cf0"
          }
        }
      }
    },
    plugins: [react()]
  }
});
export {
  electron_vite_config_default as default
};
