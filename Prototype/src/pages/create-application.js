import { state, safe } from '../state.js?v=20261122a';
import { home } from './home.js?v=20261123d';
import { icon } from '../components/icons.js?v=20261123d';

// 按教学视频展示项目目录、应用基本信息和需求描述。
export function create() {
  const currentPath=state.projectPath.trim();
  const projectDirectory=currentPath && state.hasProject ? `${currentPath}-new` : 'D:\\ExampleDevWorkspace\\suzhou-portal';
  return `${home()}<div class="overlay"><form id="create-form" class="dialog">
    <div class="dialog-head"><div><h2>新建应用</h2><p class="muted">定义应用骨架、创建位置和基础能力</p></div><a href="#home" class="close" aria-label="关闭">×</a></div>
    <div class="dialog-body create-body">
      <section><h3>项目目录</h3><p class="hint">在创建应用时选择，保存后无法修改。</p><div class="row"><input id="project-path" aria-label="项目目录" required value="${safe(projectDirectory)}"><button type="button" data-action="choose-folder">选择文件夹</button></div><p class="hint">请输入一个新的项目目录，或选择一个空目录；已有应用目录不能复用。</p></section>
      <section><h3>基础信息</h3><div class="grid"><div><label for="name">应用名称 *</label><input id="name" required value="${safe(state.name)}"></div><div><label>终端类型</label><div class="inline-options"><label><input type="radio" name="terminal" value="PC" checked>PC 端</label><label><input type="radio" name="terminal" value="移动端">移动端</label></div></div></div>
      <label>应用图标</label><div class="icon-choices" aria-label="应用图标"><label><input type="radio" name="app-icon" value="应用" checked> ${icon('grid')}</label><label><input type="radio" name="app-icon" value="桌面"> ${icon('monitor')}</label><label><input type="radio" name="app-icon" value="数据"> ${icon('data')}</label><label><input type="radio" name="app-icon" value="团队"> ${icon('users')}</label></div>
      <label for="description">应用需求描述 *</label><p class="hint">描述应用目标、用户、页面、业务信息及主要操作。</p><textarea id="description" rows="8" required>${safe(state.description)}</textarea></section>
      <section><h3>导航模式</h3><label>布局方式</label><div class="inline-options"><label><input type="radio" name="layout" value="左侧导航" checked>左侧导航</label><label><input type="radio" name="layout" value="顶部导航">顶部导航</label><label><input type="radio" name="layout" value="混合导航">混合导航</label></div><label class="check"><input type="checkbox" name="header" checked>开启头部</label><label class="check"><input type="checkbox" name="menu" checked>启用菜单</label><label for="root-path">页面根路由</label><input id="root-path" value="/page"></section>
      <section><h3>认证</h3><label class="check"><input type="checkbox" name="auth">启用认证</label></section>
    </div>
    <div class="dialog-foot"><a class="button" href="#home">取消</a><button class="primary">创建并规划页面</button></div>
  </form></div>`;
}
