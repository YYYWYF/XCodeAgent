# 前端安全性检视指南

## 概述

前端安全性检视主要关注代码中可能存在的安全漏洞和风险，包括 XSS 攻击、CSRF 攻击、敏感信息泄露等安全问题。

## 检查项目

### 1. XSS（跨站脚本）防护

#### 输入验证和过滤

- **检查点**: 用户输入处理
- **问题表现**: 未对用户输入进行验证和过滤，直接使用
- **建议**: 实施严格的输入验证，使用白名单机制

```javascript
// 推荐 - 使用净化库
import DOMPurify from "dompurify";

const userInput = document.getElementById("input").value;
const sanitizedInput = DOMPurify.sanitize(userInput);
document.getElementById("output").innerHTML = sanitizedInput;
```

#### 输出编码

- **检查点**: 动态内容输出
- **问题表现**: 直接输出未编码的用户数据到 HTML
- **建议**: 对输出内容进行适当的编码

```javascript
// 推荐 - 使用textContent
element.textContent = userData;
```

### 2. CSRF（跨站请求伪造）防护

#### Token 验证

- **检查点**: CSRF Token 使用
- **问题表现**: 敏感操作未使用 CSRF Token
- **建议**: 为所有状态变更请求添加 CSRF Token

```javascript
// 从meta标签获取CSRF Token
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

// 在请求头中添加Token
fetch("/api/user/update", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  },
  body: JSON.stringify(data),
});
```

#### SameSite Cookie 设置

- **检查点**: Cookie 安全属性
- **问题表现**: Cookie 未设置 SameSite 属性
- **建议**: 为认证 Cookie 设置 SameSite=Strict 或 Lax

### 3. 敏感信息保护

#### 硬编码敏感信息

- **检查点**: 代码中的敏感信息
- **问题表现**: API 密钥、密码、密钥等敏感信息硬编码在代码中
- **建议**: 使用环境变量或配置文件

```javascript
// 不推荐 - 硬编码密钥
const API_KEY = "sk-1234567890abcdef";

// 推荐 - 使用环境变量
const API_KEY = process.env.REACT_APP_API_KEY;
```

#### 本地存储安全

- **检查点**: 客户端存储使用
- **问题表现**: 敏感信息存储在 localStorage 或 sessionStorage 中
- **建议**: 避免在客户端存储敏感信息，使用 HttpOnly Cookie

### 4. 第三方资源安全

#### CDN 资源完整性验证

- **检查点**: 第三方资源加载
- **问题表现**: 使用未验证的 CDN 资源
- **建议**: 使用 Subresource Integrity (SRI)

```html
<!-- 推荐 - 使用SRI -->
<script
  src="https://cdn.example.com/library.js"
  integrity="sha384-oqVuAfXRKap7fdgcCY5uykM6+R8GqS4gJ8P+2l2Q5h5g5v5v5v5v5v5v5v5v5v5v5"
  crossorigin="anonymous"
></script>
```

#### 外部链接安全

- **检查点**: 外部链接处理
- **问题表现**: 外部链接未添加安全属性
- **建议**: 添加 `rel="noopener noreferrer"`

```html
<!-- 推荐 -->
<a
  href="https://external.com"
  target="_blank"
  rel="noopener noreferrer nofollow"
  >外部链接</a
>
```

### 5. 内容安全策略（CSP）

#### CSP 配置

- **检查点**: Content Security Policy 设置
- **问题表现**: 未配置或配置不当的 CSP
- **建议**: 实施严格的内容安全策略

```html
<!-- 基础CSP配置 -->
<meta
  http-equiv="Content-Security-Policy"
  content="default-src 'self'; script-src 'self' https://trusted.cdn.com; style-src 'self' 'unsafe-inline';"
/>
```

#### CSP 非内联策略

- **检查点**: 内联脚本和样式
- **问题表现**: 使用内联 JavaScript 和 CSS
- **建议**: 避免内联代码，使用外部文件

### 6. 认证和授权

#### 客户端认证验证

- **检查点**: 认证状态管理
- **问题表现**: 认证逻辑完全依赖客户端
- **建议**: 关键认证逻辑应在服务端实现

#### JWT 安全使用

- **检查点**: JWT 令牌处理
- **问题表现**: JWT 存储在 localStorage，未验证签名
- **建议**: 安全存储 JWT，验证令牌有效性

### 7. 数据传输安全

#### HTTPS 强制

- **检查点**: 安全传输协议
- **问题表现**: 混合内容或使用 HTTP
- **建议**: 强制使用 HTTPS，避免混合内容

#### 安全头设置

- **检查点**: HTTP 安全头
- **问题表现**: 缺少安全头配置
- **建议**: 配置适当的安全头

```javascript
// 服务器端设置安全头（Express示例）
app.use((req, res, next) => {
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader(
    "Strict-Transport-Security",
    "max-age=31536000; includeSubDomains"
  );
  next();
});
```

### 8. 代码执行安全

#### eval 和 Function 构造函数

- **检查点**: 动态代码执行
- **问题表现**: 使用 `eval` 或 `Function` 构造函数执行动态代码
- **建议**: 避免使用 eval，使用安全的替代方案

#### 动态导入安全

- **检查点**: 动态模块加载
- **问题表现**: 从用户输入动态导入模块
- **建议**: 限制动态导入的来源

## 框架特定安全考虑

### Vue.js 安全

#### v-html 使用

- **检查点**: `v-html` 指令使用
- **问题表现**: 不当使用 `v-html` 渲染用户输入
- **建议**: 避免直接使用 `v-html`，或进行严格的输入净化

### React 安全

#### dangerouslySetInnerHTML 使用

- **检查点**: `dangerouslySetInnerHTML` 使用
- **问题表现**: 不当使用 `dangerouslySetInnerHTML`
- **建议**: 避免直接使用，或进行严格的输入净化

```jsx
// 推荐 - 使用净化库
import DOMPurify from "dompurify";

function MyComponent({ content }) {
  const sanitizedContent = DOMPurify.sanitize(content);
  return <div dangerouslySetInnerHTML={{ __html: sanitizedContent }} />;
}
```

## 安全开发最佳实践

### 依赖安全

- 定期更新依赖包，修复已知漏洞
- 使用 `npm audit` 或 `yarn audit` 检查安全漏洞
- 使用 Snyk 或 Dependabot 进行依赖安全扫描

### 错误处理安全

- 避免在错误信息中泄露敏感信息
- 使用统一的错误处理机制
- 记录安全相关错误到安全日志

## 检视工具支持

本检视工具会自动检测常见的安全问题，包括：

- XSS 漏洞风险
- CSRF 防护缺失
- 敏感信息泄露
- 不安全的内容加载
- 不当的第三方资源使用
- 安全头配置问题