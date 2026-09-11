# 认证页米塔三态形象嵌入（登录=帽子 / 注册=善良 / 找回=疯狂）

> 分类：frontend ｜ 关联：认证页（login / register / recover）品牌区视觉升级

## 一、需求背景

Mitta 认证页左侧品牌区原本是 SVG 圆环动态装饰。需求升级为：将圆环主体替换为《MiSide》米塔角色形象，并保留红色弧线作氛围背景（米塔为主体、弧线做点缀）。

三态路由对应三个人格：

| 路由 | 人格 | 左侧文案风格 |
| --- | --- | --- |
| /api/login | 帽子米塔 | 元气俏皮（"帽子米塔，来迎接你啦！"） |
| /api/register | 善良米塔 | 温柔治愈（"欢迎加入 Mitta 的温柔小窝"） |
| /api/recover | 疯狂米塔 | 病娇安抚（"别想逃哦～我帮你找回密码 ♡"） |

路由切换时形象需平滑过渡：容器位移 + 换脸交叉淡化 + 人格化待机动画（浮动 / 呼吸 / 摇摆病娇抖动）。

## 二、遇到的问题与排查过程

### 问题 1：AIGC 生成形象不符合角色设定

- **现象**：按"米塔=低双马尾（垂顺发尾+蓝色发圈，非麻花辫）"生成多批形象，均出现麻花辫、长发、盘发细节跑偏；帽子米塔的"头发全盘进帽里"也反复画错。
- **排查**：逐轮对照角色参考图核对发型、发饰、表情，确认是生成模型对该角色细节还原不稳定，多次 prompt 调优无效。
- **结论**：放弃 AIGC，改用用户提供的原图素材抠图嵌入。

### 问题 2：颜色抠图法对这批素材不可靠

- **现象**：尝试连通域 + 边缘采样多参考色的颜色抠图 3 个版本，出现啃头发（邪恶紫发与紫黑背景色距仅约 22）、误删金色闪光/紫色外发光、某版本人物 100% 透明。
- **排查**：诊断确认背景与发丝色距过近，纯色分离无法区分。
- **结论**：改用 rembg（u2netp，4.4MB 模型）AI 抠图，一次成功输出 RGBA 透明底。

### 问题 3：模型下载通道不通

- **现象**：ghproxy.net 太慢（~1.1MB/30s）、hf-mirror.com 不通（0MB）、GitHub 直连 176MB u2net 太慢。
- **排查**：逐一测试各镜像通道与模型大小。
- **解决**：GitHub 直连 u2netp（4.4MB）下载成功，模型落盘 `%LocalAppData%\rembg_models\u2netp\u2netp.onnx`。

### 问题 4：前端骨架屏不消失（调试插曲）

- **现象**：嵌入后刷新页面长期停留在骨架屏（聊天页布局占位），认证页未渲染。
- **排查**：node --check 语法通过、CDN（jsdelivr/bootcdn）可达、服务器返回的 app.js 已含米塔代码；最终定位为本地 SPA 静态服务进程中途退出，浏览器请求全部失败导致加载中断。
- **解决**：以独立进程方式重启 SPA 静态服务（`Start-Process` 脱离后台任务管理，避免被回收）。

## 三、解决方案

### 素材：用户直供透明底成品

用户提供三张已处理的 RGBA 透明底 PNG（帽子 1439×1439 / 善良 850×850 / 邪恶 850×850），**不再做任何处理**，直接覆盖到前端固定引用路径：

- `assets/img/mita_hat.png`（登录）
- `assets/img/mita_kind.png`（注册）
- `assets/img/mita_crazy.png`（找回）

前端通过 `MITA_IMAGES` 映射固定路径引用，替换素材无需改代码。

### 前端实现（app.js + style.css）

**app.js**：
- 新增 `MITA_IMAGES` 映射（authMode → src/name）。
- `AuthLayout` 模板加入 `.auth-mita` 容器 + `<transition name="mita-fade">`（默认并行模式，旧图淡出 + 新图淡入）。
- computed 新增 `mitaImg` / `mitaName`，随 `authMode`（由路由 path 推导）切换。

**style.css**：
- `.auth-mita` 容器绝对定位，三态位置不同：登录居中偏右 400px、注册右上 300px、找回左下 360px；`left/top/width/height` 以 1s ease 过渡实现位移缩放平滑切换。
- 三套待机动画：`mita-float`（浮动）、`mita-breathe`（呼吸）、`mita-sway`（摇摆+病娇小抖动）。
- `.auth-ring` 降为 `opacity: .5` 氛围层。
- `prefers-reduced-motion: reduce` 时关闭全部动画与位移过渡（可访问性降级）。
- 认证组件未用 `mode="out-in"`（会触发 nextSibling 空指针崩溃，代码注释已说明）。

### 问题 5：米塔形象只显示头部，手势/身体被裁

- **现象**：注册页渲染出的善良米塔只剩头部特写，竖拇指手势与身体消失。
- **排查**：浏览器实测 .auth-mita 容器几何——登录态 400px、注册态 300px 容器用固定 px 宽 + 百分位 left 定位，右边界（login 596px / register 542px）超出品牌区 .auth-brand 宽度（约 466px），而 .auth-brand 为 overflow: hidden，容器右侧（手势所在区域）被整体裁掉。找回态（left 16% + 360px = 434px）未越界所以正常。
- **解决**：容器尺寸改为百分比体系——基础 width: 72%; aspect-ratio: 1/1; height: auto，三态仅微调 width（login 72% / register 62% / recover 68%）与 left（18% / 20% / 4%），保证任何视口下右边界都落在品牌区内（实测 login 432 < 480、register 394 < 480、recover 346 < 480）。同时本地 SPA 服务器加 Cache-Control: no-store 头，避免改样式后浏览器仍用旧 CSS。

### 问题 6：移除帽子米塔，其余两态右缘对齐品牌区右边线

- **需求变更**：登录态不再展示米塔形象（帽子米塔删除），保留红色弧线作氛围；注册（善良）/找回（疯狂）两态图片位置调整，右缘对齐品牌区右边线。
- **实现**：.auth-mita.mita-login { display: none; }；注册/找回改为 left: auto; right: 0 右对齐定位（右缘精确贴品牌区右边界，不受品牌区宽度变化影响）。
- **验证**：login 态 .auth-mita display:none（无米塔）；register 右缘 480 = 品牌右缘 480（gap 0）；recover 右缘 480 = 品牌右缘 480（gap 0）。截图存 auth_login/register/recover.png。

### 问题 7：登录页文案参照 Mitta system_prompt 重写

- **需求**：登录态已移除帽子米塔形象，左侧文案不再提及"帽子"，改为参照 Mitta 全局 system_prompt 的人设风格（元气、温柔、俏皮、可靠知识管家，语气词"呀/哦/呢" + 颜文字）。
- **实现**：`AUTH_QUOTES.login` 重写——title「欢迎回来呀～ Mitta 等你很久了呢 (｡•̀ᴗ-)✧」、desc「登录后继续知识大冒险，准确、可靠的知识管家随时待命哦 ♪(^∇^*)」。
- **验证**：登录页实测渲染新文案，无帽子相关措辞。

### 问题 8：路由切换过渡期间帽子米塔残留

- **现象**：从登录切到注册时，1s 过渡期间帽子米塔旧图短暂残留可见。
- **排查**：登录态此前仅用 CSS `display:none` 隐藏 `.auth-mita` 容器，容器与帽子 img 仍在 DOM；切到注册时 Vue transition 对旧 img（key=login）做 leave 淡出、对新 img 做 enter 淡入，两者并存导致帽子残留。
- **解决**：`.auth-mita` 容器加 `v-if="authMode !== 'login'"`——登录态容器整体不进 DOM，帽子图彻底不存在，切换时只挂载新图，无旧图 leave 过渡。
- **验证**：login 态 `document.querySelector('.auth-mita')` 为 null；login→register 过渡早期 DOM 仅 1 张 `mita_kind.png`；recover 过渡早期仅 `mita_crazy.png`；recover→login 容器被移除。

## 四、验证

- 本地 SPA 服务（8090）实测三态：文案、形象、位置、待机动画全部随路由切换正常；JS 无报错。
- 三张页面渲染截图留存（login / register / recover）。
- 已提交（先提交不推送）：`e1ff572`，5 files changed（app.js + style.css + 3 张素材）。
