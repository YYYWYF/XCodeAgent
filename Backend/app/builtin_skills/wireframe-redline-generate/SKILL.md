---
name: wireframe-redline-generate
description: >-
  生成带工程标注(redline)的低保真线框图。左侧为浏览器边框内的扁平灰盒
  线框页面,叠加单一强调色的编号标注 pin(①②③④⑤);右侧为 SPEC 面板,
  逐条列出每个编号 pin 对应的工程/UX 交付说明。即"线框图 + redline spec"
  风格——干净、扁平、低保真,非手绘。当需求提到"annotated wireframe"、
  "redline wireframe"、"wireframe with spec"、"lo-fi landing wireframe"、
  "low fidelity"、"线框图"、"标注线框"、"redline"时使用。
triggers:
  - "annotated wireframe"
  - "redline"
  - "wireframe spec"
  - "lo-fi landing"
  - "wireframe"
  - "low fidelity"
  - "标注线框图"
  - "redline 标注"
od:
  mode: prototype
  platform: desktop
  scenario: design
  fidelity: wireframe
  preview:
    type: html
    entry: index.html
  design_system:
    requires: false
    sections: [color, typography, layout, components]
  example_prompt: "Draw an annotated redline wireframe for a desktop landing page — greybox nav, hero, logo strip, 3-up feature grid and footer, numbered pins ①–⑤ and a right-hand spec panel with one engineering note per pin."
---

# Wireframe Redline Generate Skill

生成一张扁平、低保真的落地页线框图,并附带 redline 交付规格。核心目的是
"结构 + 交付说明,而非像素"——灰盒承载布局,编号 pin 标注区域,右侧 spec
面板把每个 pin 转成一条简短的工程/UX 说明。保持干净扁平;绝不手绘或潦草。

## 参考示例

同目录下的 `example.html` 是一份完整可运行的参考实现(Acme 落地页),
展示了双栏外壳、浏览器边框、灰盒原语、编号 pin、spec 面板与响应式断点的
完整写法。生成前应先参照它建立视觉基线,再按具体需求调整区块与 pin 数量。

## Workflow

1. **跳过精修 UI。** 本 skill 明确要求低保真灰盒外观。字体 token 只需松散
   遵循(一种干净 sans 如 Inter / system-ui 用于标签,一种 mono 如 IBM Plex
   Mono 用于 spec 说明与 pin 编号)。使用中等灰度填充并配明确的深色边框,
   使页面读起来像缩略图——避免近白底配近白块,否则会渲染成空白。

2. **搭建双栏外壳。** 左侧是线框画布:一个带浏览器边框的灰盒页面。右侧是
   窄的"ANNOTATIONS / SPEC"面板。外壳用 `display:flex`(左栏 `flex:1`,
   右栏 spec 固定约 220px),**不要用 grid**——grid 在窄容器里会塌成单列
   导致设计稿与说明上下堆叠。选定**一种**强调色(coral 或 blue),
   **仅**用于编号 pin 与对应的 spec 编号——其余一切保持灰度。

3. **自上而下布局画布**,每个区块是一个灰盒,上面绝对定位一个编号 pin:
   - **顶部导航** —— logo 灰盒 + 导航 lorem 条 + 一个主按钮块。Pin ①。
   - **Hero** —— 左侧大标题 lorem 条 + 副标题 + 两个 CTA 按钮块;右侧一个
     大图占位符(带对角 X 的矩形)。Pin ②。
   - **Logo 条** —— 一行 5 个小灰盒合作方 logo。Pin ③。
   - **特性栅格** —— 3 张卡片,每张含图标方块 + 标题条 + 2 条文本条。Pin ④。
   - **页脚** —— 多列 lorem 链接条。Pin ⑤。

4. **在 spec 面板中镜像每个 pin。** 每条 spec 行 = 强调色的圆圈编号 + 一条
   简短 mono/sans 说明,例如"① Sticky nav, 64px, condenses on scroll"、
   "② Hero H1 48/56, CTA pair primary+ghost"、"③ 5 partner logos, greyscale"、
   "④ 3-up at ≥960px → 1-up mobile"、"⑤ 4-col footer, legal row"。
   为面板及每一行标记 `data-od-id`。

5. **自检**:
   - 画布上每个编号 pin 都恰好对应一条 spec 行。
   - 强调色**仅**出现在 pin + spec 编号上;其余全为灰度。
   - 页面在缩略图尺寸下应清晰可读;若区块融入背景看不见,加深填充/边框。
   - 绝不能看起来像素级精确或手绘——只用扁平灰盒。

## Output contract

在 `<artifact>` 标签之间输出:

```
<artifact identifier="wireframe-slug" type="text/html" title="Annotated Wireframe — Title">
<!doctype html>
<html>...</html>
</artifact>
```

artifact 之前一句说明,之后无内容。
