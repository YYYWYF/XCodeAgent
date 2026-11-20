// 显示原型操作反馈。
export function toast(message) { const el=document.querySelector('#toast'); el.textContent=message; el.style.display='block'; clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.style.display='none',2600); }
