import { state, projectMemory, safe } from '../state.js?v=20261122a';
import { home } from './home.js?v=20261122a';

// 提供返回已打开工作目录的原型入口。
export function openWorkspace() {
  const current = state.isDemo ? projectMemory.current : (state.hasProject ? state : null);
  return `${home()}<div class="overlay"><div class="dialog small-dialog"><div class="dialog-head"><div><h2>打开工作目录</h2><p class="muted">从最近项目中选择工作目录</p></div><a class="close" href="#home">×</a></div><div class="dialog-body"><h3>最近使用</h3><div class="list-row"><div><strong>苏州分行门户</strong><p class="hint">C:\\Workspace\\suzhou-portal</p></div><button data-open-demo="true">进入</button></div>${current?`<div class="list-row"><div><strong>${safe(current.name)}</strong><p class="hint">${safe(current.projectPath)}</p></div><button data-open-current="true">继续</button></div>`:''}</div><div class="dialog-foot"><a class="button" href="#home">关闭</a></div></div></div>`;
}
