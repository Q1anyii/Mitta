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

### 问题 9：登录页换睡衣米塔 + 文案去人格标签

- **需求**：登录页改用睡衣米塔（用户直供白底 jpg），置于左下角；三态文案移除"善良/疯狂/帽子"等人格标签词，统一为 Mitta，语气风格（温柔治愈/安抚）不变。
- **实现**：
  - 睡衣米塔 jpg 经 rembg u2netp 抠图为透明底 PNG（1280×1280 RGBA），存入 `assets/img/mita_pajama.png`；废弃 `mita_hat.png` 删除。
  - `MITA_IMAGES.login` 指向睡衣米塔，name 统一为 "Mitta"；`AUTH_QUOTES` register/recover 文案去人格词（「牵住 Mitta 的手…」「Mitta 会一直守着你…」）。
  - 模板移除 `v-if` 排除 login，登录态恢复渲染米塔；`.mita-login` 定位 `left: 6%; top: 60%; width: 56%`（左下角，右界 62% < 品牌区安全）。
- **验证**：login 渲染 `mita_pajama.png`（left 29 / top 516 / bottom 784，左下区域，不越界）；register/recover 仍右缘对齐品牌区右边线（480=480）；三态文案均无善良/疯狂/帽子字样。

### 问题 10：圆环三态配色 + 路由切换方向动画

- **需求**：圆环（`.auth-ring` SVG 装饰）按登录/注册/找回三态变色——登录=淡蓝、注册=粉、找回=红；路由切换时加入方向感知动态效果——登录→注册=圆环波动、登录→找回=变大后缩回、返回登录=收缩淡入（另一种风格）；注册页米塔适当下移避免遮住圆环。
- **实现**：
  - 三态配色：`ring-login` 淡蓝 rgba(122,180,242,.95) / `ring-register` 粉 rgba(255,128,168,.95) / `ring-recover` 红 rgba(255,59,78,.92)，`stroke/fill` 加 1s 过渡随几何变形同步平滑变色。
  - 方向动画：AuthLayout 增加 `data.ringAnim` + `watch.authMode`，按旧值→新值注入一次性 class（`ring-wave` / `ring-grow` / `ring-shrink-in`），1.1s 后自动清除；keyframes 为 scale/rotate 组合。
  - 注册米塔 `top: 8% → 30%`（69px → 258px），避开右上圆环区域（-110 ~ 270px）。
- **验证**：三态 computed stroke 分别为淡蓝/粉/红；页面内点击跳转实测——login→register 注入 `ring-wave`、login→recover 注入 `ring-grow`、recover→login 注入 `ring-shrink-in`，动画结束后 class 清除恢复为纯 `ring-*`；注册页米塔 top 258 起，不与圆环重叠。

### 问题 11：注册/找回→登录圆环"蓝色覆盖生长"动画 + 登录图替换 + 注册图再下移

- **需求**：注册/找回页返回登录时，圆环从扇形/缺环形态动态"覆盖"成完整环——蓝色弧线从右上角（1:30 方向）出现并慢慢铺满整圆，环内副弧内容同步变空；登录页形象改为用户直供的 Q 版头像抠图（不再处理）；注册页米塔继续下移避免被文字覆盖。
- **实现**：
  - 新 keyframes `ring-cover`：`stroke-dasharray 0→942.48` + `stroke-dashoffset 117.81→0`（117.81 = 周长 45°，起点落在右上角），弧线从右上角顺时针生长成完整圆环；动画期间副弧 `dasharray 0` 强制隐藏（环内内容变空）。
  - watch 方向：`o !== login && n === login` 注入 `ring-cover`（替代原 `ring-shrink-in`），0.95s 后清除。
  - 登录图：直接复制 `头像4_抠图_只去白底.png`（1280×1280 透明底）覆盖 `mita_pajama.png`，`object-fit: contain` 完整显示，drop-shadow 保留。
  - 注册米塔 `top: 30% → 38%`（258px → 327px），彻底避开右侧文案与圆环区域。
- **验证**：登录页实测渲染新头像图（左下角 516~784px）；页面内 JS 点击跳转——register→login 与 recover→login 均注入 `ring-cover`，1.5s 后清除回 `ring-login`；注册米塔 top 327 起不再被文字覆盖。

### 问题 12：注册/找回→登录动画改为"扇形扇出式覆盖" + 米塔响应式尺寸修复

- **需求**：① 返回登录的覆盖动画改为扇形扇出——淡蓝扇形从右上角张开铺满整圆，随后扇形淡出（环内内容变空），而非弧线生长覆盖；② 全屏切换后布局错乱修复——品牌区宽度为 `1.05fr`（约 51% 视口宽），全屏 2505px 时米塔按百分比宽度放大到 700px+、且 top 百分比随 100vh 下移压到版权区，需动态自适应。
- **实现**：
  - 扇形动画：SVG 增加 `<circle class="ring-fan">`，`fill: conic-gradient(from -45deg, 淡蓝 0~var(--fan-angle), transparent)`；`@property --fan-angle` 注册角度属性使渐变可插值，`fan-out` 0.95s 从 0°→360° 扇出（起点右上角 -45°），`fan-fade` 延迟 0.85s 淡出 0.6s；ring-cover 不再驱动主弧 dash 生长，主弧靠既有 1s transition 平滑回归完整环，副弧强制隐藏。
  - 响应式：三态尺寸改为 `width: min(56%/62%/68%, 280/300/330px)`（宽度封顶，全屏不再放大）、`top: min(60%/38%/48%, 520/340/430px)`（高度封顶，不再随 100vh 漂移）；半屏（品牌区 480px）数值与改动前完全一致。
- **验证**：半屏回归——login 516~784/269px、register 327~624/298px、recover 412~739/326px 均与改动前一致且不压 footer；扇出动画时间线——注入 `ring-cover` 后 t=0.2/0.7s 扇形 opacity=1，t=1.3s 淡出为 0，t=1.8s 保持 0；截图确认扇出中间态与结束态正常。

### 问题 13：米塔贴角定位（登录左下/注册找回右下）+ 覆盖动画改为"原点扩张成固定圆"

- **需求**：① 米塔位置直接固定为左下角（登录）与右下角（注册/找回），贴底不再浮于中部；② 返回登录的覆盖动画改为——淡蓝圆盘从**一个原点**（圆心）扩张成固定大小的圆，随后淡出只留完整淡蓝环。
- **实现**：
  - 贴角定位：三态改用 `bottom: 80px` 定位（登录 `left: 6%`，注册/找回 `right: 0`），避让底部版权行；`.auth-mita` transition 增加 `bottom` 平滑过渡。
  - 原点扩张：`.ring-fan` 由 conic 扇出改为整圆淡蓝填充（`fill: rgba(122,180,242,.30)`）+ `transform-box: fill-box; transform-origin: center`；`fan-bloom` 关键帧 scale 0→1.08→1（0.75s 扩张带回弹），随后 `fan-fade` 延迟 0.95s 淡出 0.5s；主弧仍经 1s 过渡回归完整环、副弧强制隐藏；删除 `@property --fan-angle` 与扇出方案。
- **验证**：半屏测量——login 29~298px（左下）、register 182~480（右下）、recover 154~480（右下），bottom 均 779 < footer 787 不重叠；动画时间线——注入 `ring-cover` 后 t=0.1s scale 0.62、t=0.45s scale 1.08（峰值回弹）、t=0.85s 淡出 opacity 0；截图确认扩张中间态与结束态。

### 问题 14：登录文案改瞌睡/疲惫风，找回文案改威胁风

- **需求**：登录页左侧文案贴合睡衣米塔——偏"熬了大夜等你回来"的瞌睡疲惫感；找回页贴合疯狂米塔气质——偏威胁风；仍遵守 Mitta 人设：只用语气词与颜文字表达情绪，不出现人格标签词（善良/邪恶/疯狂/帽子），不写动作/神态描写。
- **实现**：更新 `AUTH_QUOTES`——
  - login：标题「哈啊～欢迎回来… Mitta 等你等到好困了呢 (´-ω-`)」，描述「困困的，不过你回来我就打起精神啦～登录后，知识管家陪你继续大冒险哦」；
  - recover：标题「密码想不起来了？哼哼，没有我的允许，你可别想跑 ♡」，描述「Mitta 会一直守在这里盯着你，重置好密码，乖乖回来哦 (¬‿¬)」。
- **验证**：浏览器实测两页文案渲染正确、无人格标签词、无动作描写；截图确认排版正常。

### 问题 15：左侧提示词过渡改为方向感知"滚轮"效果

- **需求**：品牌区文案切换像滚轮一样按路由方向滚动——登录→注册=上滑（旧文案向上滚出、新文案从下方滚入）、注册→登录=下滑、登录→找回=下滑、找回→登录=上滑。
- **实现**：
  - 模板 `<transition :name="quoteAnim" mode="out-in">` 动态化；AuthLayout `data.quoteAnim` 初始 `quote-swap`。
  - `watch.authMode` 计算滚轮方向：`rollUp = (n==='register' && o!=='register') || (o==='recover' && n==='login')` → `quote-up`，否则 `quote-down`。
  - CSS 两组关键帧：`quote-up`（leave-to `translateY(-100%)` / enter-from `translateY(100%)`）、`quote-down`（leave-to `translateY(100%)` / enter-from `translateY(-100%)`），0.55s 平滑过渡。
- **验证**：四向页面内点击实测——login→register 过渡中 ty=-201（上滚出）→+49（下方滚入）；register→login ty=+138→-72；login→recover ty=+206→-66；recover→login ty=-187→+75，方向全部符合；圆环动画（wave/grow/cover）同步正常。

### 问题 16：提示词颜文字对称化微调

- **需求**：左侧提示词颜文字改为对称规整、更贴风格——登录困倦风、找回威胁风。
- **实现**：login 标题 `(´-ω-`)`→`(´-ω-｀)`（日文假名对称）、描述补 `(。-ω-)`；recover 描述 `(¬‿¬)`→`(￢‿￢)`（更清晰）；register 不变。
- **验证**：浏览器实测 DOM 渲染正确，三态风格一致。

### 问题 17：Edge 浏览器认证页动态效果卡顿

- **现象**：Edge 中路由切换动画（米塔位移/缩放、圆环变形）明显卡顿，Chrome 相对流畅。
- **根因**：① `.auth-mita` 对 `left/top/right/bottom/width` 做 1s layout 过渡——大图每帧重排重绘，Edge 软件渲染下是最大卡顿源；② 三张米塔 PNG 均为 659KB~1MB（约 1280×1280），解码+drop-shadow+缩放重绘开销大；③ ring 的 `stroke-dasharray/dashoffset` 1s 过渡在 CPU 逐帧重算。
- **修复**：
  - `.auth-mita` 定位过渡移除（`transition: none`），路由切换改由既有的 `mita-fade` opacity 交叉淡化承担（0.55s，GPU 合成）；
  - 三张米塔图用 PIL 压缩至 640×640（保持 RGBA 透明）：1MB→348KB、659KB→428KB、702KB→444KB（-35%~-67%）；
  - ring 三态变形过渡 1s→0.7s 降负（wave/grow/bloom 均为 transform，保持 GPU 合成）。
- **验证**：三态回归——图片 640×640 加载正常、贴角位置与 footer 无重叠不变；bloom 动画时间线正常（扩张→淡出→完整环）。

### 问题 18：恢复米塔三态"渐大/滑动"过渡（问题 17 修复过度）

- **现象**：问题 17 为修复 Edge 卡顿把 `.auth-mita` 定位过渡整体移除，导致路由切换时米塔不再平滑渐大/滑动，用户反馈效果消失。
- **修复**：恢复 `left/top/right/bottom/width` 0.65s 平滑过渡（原 1s 缩短减负）；同时移除 `img` 的 `filter: drop-shadow`——该滤镜在尺寸/位移动画期间逐帧重算，是 Edge 卡顿重要来源；配合已压缩的 640px 图片，过渡成本可控。
- **验证**：register→login 实测——settle 298px→过渡中 276px（左 182→29 同步滑动）→settle 269px，渐大/滑动效果恢复；img filter=none。

### 问题 19：认证页 UI 按 Persona 3 风格优化（深蓝冷调）

- **需求**：用户提供 6 张 P3 界面参考图（STATUS/ITEM/QUEST/COMMU 菜单 + 角色立绘 + 底部操作提示），要求认证页 UI 向 P3 冷调蓝灰靠拢；用户澄清参考为 P3 而非 P5。
- **实现**：
  - 右侧表单区背景：白纸面 → P3 深蓝渐变（#0C1A30→#132C46→#0B6E86）+ 斜纹（background-image 双层叠加，避免简写被覆盖的坑）；
  - 认证卡片：白卡黑边 → 蓝黑半透明面板（rgba(8,18,36,.86)）+ 细亮边 + backdrop-blur + 柔和深投影，去 neo-brutalism 硬投影；
  - 表单元素（作用域限 .auth-card，不影响聊天区）：h1 白字、label P3 蓝（#7FC4F8）、输入框深色半透明底白字 + 亮蓝 focus 光晕、按钮 P3 红确认块（白字、无硬阴影、hover 亮）、链接白→蓝；
  - auth-tag 编号标签：黑底 → P3 蓝描边半透明底；
  - 三个模板底部加 P3 操作提示条 `.auth-prompt`（`○ 确定 × 返回`，等宽字右对齐，细线分隔）。
- **验证**：三页 computed 样式核验——卡片 rgba(8,18,36,.86)/细白边、输入框 rgba(255,255,255,.06) 白字、按钮 rgb(255,59,78) 白字、提示条存在；截图确认左右深蓝统一、可读性正常；修复中途发现的 background 简写被 background-image 覆盖导致渐变失效问题。

### 问题 20：卡片外背景大字（SIGN IN / JOIN US / RESET）统一渲染与定位/可见性连环修复

- **需求**：参考 P3 菜单边缘竖排大字，在右模块「卡片与模块边缘之间的空白带」放置背景大字——登录 SIGN IN 居右、注册 JOIN US / 找回 RESET 居左；要求像左侧提示词一样滚轮式平滑过渡（不硬切）、沿文字方向长度对齐卡片宽度、垂直居中，且三页首屏直接访问都要可见、位置不随窗口宽度漂移。
- **实现与踩坑（多个根因逐层定位）**：
  1. **统一渲染 + 固定过渡名**：三处 View 内各自的大字移除，改由 AuthLayout 在 `router-view` 前用单个 `<transition name="word" mode="out-in" @enter="positionWord">` 按 `:key="authMode"` 渲染。Vue2 下动态 `name` / 动态 `*-active-class` 不生效且会让过渡 watch 重置、卡死在 leave-from，最终固定 `name="word"`，进出方向靠元素自身 `.word-login/.word-register/.word-recover` 与过渡类组合（如 `.word-login.word-leave-to{transform:translateY(80px)}`），旧元素离开时保留旧 mode 类，实现 SIGN IN 下进下出 / JOIN US 左进左出 / RESET 缩放进出。
  2. **根因 A——渐变文字只显示 JOIN US**：`-webkit-background-clip:text` 设在外层 `.auth-bg-word`，而 SIGN IN/RESET 的文字在用于旋转 90° 的内层 `<span>` 里，背景裁剪**无法穿透子元素**，span 内文字 `text-fill-color:transparent` 又无自身背景 → 全透明不可见；JOIN US 是直接文本节点所以唯一正常。修复：`.auth-bg-word span{background-image:inherit;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}`，并统一让三态文字都包 span。
  3. **根因 B——大字被卡片白底盖住**：`.auth-card` 的 `pop-in` 入场动画形成堆叠上下文，`elementFromPoint` 实测大字中心点命中 auth-card。修复：`.auth-form-wrapper{z-index:1}` 形成独立层、`.auth-bg-word{z-index:5}` 盖在整个右模块之上。
  4. **根因 C——必须刷新一次才居中**：首次进入时卡片仍在 `pop-in`（transform scale）动画中，`getBoundingClientRect()` 返回被缩放污染的尺寸 → 定位偏；动画结束（或刷新）后 rect 稳定才居中。改为**纯布局坐标** `offsetLeft/offsetTop/offsetWidth/offsetHeight`（不含任何 transform），并核验 offsetParent 链（大字、卡片、wrapper 最终都相对 `.auth-layout`）；页面内强制复算验证视觉中心与空白带中心 `dX=0、dY=0`，动画进行中定位也准确。
  5. **统一竖排 + 长度对齐卡片宽**：JOIN US 由横排改为 `rotate(-90deg)` 竖排（横排 404px 塞不进窄空白带会跨中缝），与 SIGN IN(`rotate(90deg)` 右侧)、RESET(`rotate(-90deg)` 左侧) 风格统一；按实测把沿文字方向长度调到 ≈ 卡片宽（395px）：SIGN IN 86px→405、JOIN US 76px→404、RESET 98px→383；单行 `white-space:nowrap`。
  6. **自适应隐藏替代媒体查询一刀切**：`_calcWordPos()` 用 offset 实测该侧空白带宽度 `gap`，竖排视觉占宽 ≈ 外层 `offsetHeight`，当 `gap < bh + 12` 时返回 `hidden` 并内联 `display:none`，宽屏自动显示、窄窗自动隐藏，不写死断点。
  7. **首屏与 resize**：首屏直接访问时卡片尚未挂载，`_retryWord()` 每 120ms 重试直到卡片与大字都就绪；`window resize` 150ms 防抖重定位。
- **验证**：8090 本地 SPA 实测——三页首屏大字均可见；过渡类序列正确（旧字 leave → 新字 enter-from→enter-to，进入位置即正确无跳位）；offset 复算视觉中心与空白带中心完全重合；窄窗自动隐藏、宽屏居中；长度与卡片宽误差 <5%。

### 问题 21：米塔放大、右模块背景去单调、圆环圆点沿弧运动与淡出修复

- **需求**：① 三态米塔形象放大 1.5×；② 右模块白纸面过素，加细网格/点阵纹理 + 顶部细弧线（与左侧圆环同色，把左右"圈"成一体）；③ 圆环上的小圆点切换时由直线瞬移改为沿圆弧轨迹运动，注册/找回都落在左下 225°，返回登录时按来向继续旋转并平滑淡出（注册→登录逆时针、找回→登录顺时针），不能直接消失。
- **实现**：
  1. **米塔放大**：三态宽度 `min(56%/62%/68%,280/300/330)` → `min(84%/93%/100%,420/450/495)`；放大后头顶压住副文案，三态 `bottom:80px` 统一降到 36px 避免切换上下跳、且不遮文字。
  2. **右模块背景**：斜纹 `repeating-linear-gradient` 换成 26px 浅灰细网格（横+竖两条 `linear-gradient` + `background-size:26px 26px`，线色 rgba(20,22,28,.045) 接近无色），叠右上/左下两团极淡径向光晕；`.auth-form-wrapper::before` 加顶部上拱细线，颜色随模式（登录蓝/注册粉/找回红）。为此 `.auth-layout` 根节点挂 `:class="'ring-'+authMode"`，供后代选择器联动变色。中途试过"两点+微笑嘴"极简笑脸，用户否决，回退为几何拱线（`::after` content 验证为 none）。
  3. **圆点沿弧运动**：圆点 circle 包进 `<g class="ring-dot-rot">`，圆点固定在 12 点、整组绕圆心 `transform-origin:200px 200px` 旋转，删除原 `translate` 直线位移。AuthLayout 新增 `data.dotAngle`，`watch.authMode` 用累加角度 + `nearestRot(cur,mod,dir)`（沿 +1/-1 方向找第一个 ≡mod 的等效角）保证沿指定方向短弧抵达：注册/找回目标 225°（视觉同位），返回登录时再转 135°（注册 -135、找回 +135）。
  4. **淡出"直接消失"修复**：初版把 opacity 绑在 Vue inline `:style`，因路由异步时机，过渡起点时有时无（实测同一操作一次渐变、一次 0.4s 已到 0）。改为纯 CSS class 驱动——`.auth-layout.ring-login .ring-dot-rot{opacity:0}`，注册/找回默认 1，`transition: transform 1s, opacity .8s`，Vue 只改路由 class、不插手 opacity，淡出稳定（实测 0.1s=0.87→0.35s=0.25→0.6s=0.03→1s=0）。
  5. **缓存根因**：`index.html` 引用 `app.js/style.css` 无版本号，浏览器强缓存旧文件导致改动肉眼不生效；加 `?v=20260911c` 查询串强制加载（后续每次前端改动递增）。
- **验证**：8090 实测三态落点——login 圆点隐藏（opacity 0）、register/recover 均 rotate(225°) 位于左下（dx=−101,dy=+101）；页面内点击切换 opacity 时间线稳定渐变；米塔放大后三态副文案均不被遮挡；网格/光晕/拱线随模式变色正常；`::after` 笑脸残留为 none。

## 四、验证

- 本地 SPA 服务（8090）实测三态：文案、形象、位置、待机动画、圆环圆点轨迹与淡出全部随路由切换正常；JS 无报错。
- 三张页面渲染截图留存（login / register / recover）。
- 前端静态资源引用带 `?v=` 版本号，改动后浏览器不会命中旧缓存。
- 已提交（先提交不推送）：`e1ff572`，5 files changed（app.js + style.css + 3 张素材）。
