// 读取当前原型页面路由，未知或空路由回到欢迎页。
export function currentRoute() { const route=location.hash.slice(1); return ['home','create','open','planning','workbench'].includes(route)?route:'home'; }
