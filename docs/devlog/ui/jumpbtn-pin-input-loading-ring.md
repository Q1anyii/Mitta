# 跳底按钮钉输入框正上方 + 输出态加载环（H-20260919-10）

docs_sync: none

日期：2026-09-19
类别：ui / chat 交互

## 需求

跳底按钮原在 `.messages-container`（overflow:auto 滚动容器）内部 absolute 定位，
会随消息内容一起滚动、位置漂移；改为钉在输入框正上方固定不动，并在模型输出时带旋转加载环。

## 改动

### DOM（app.js）
- 按钮从 `.messages-container` 内部移出，放到 `.input-area` 内、`.input-card` 之前
- 显隐条件 `v-if="showScrollToBottom"` → `showScrollToBottom || isLoading`（输出中始终显示）
- 新增 `:class="{ 'is-generating': isLoading }"` 输出态绑定
- `.messages-container` 的 `@scroll="onMessagesScroll"` 保留（只负责监听显隐，阈值 200px 不变）

### CSS（style.css）
- `.input-area` 加 `position: relative`（按钮的定位基准）
- `.scroll-to-bottom-btn`：`bottom: 24px` → `bottom: calc(100% + 8px)`（钉在 input-area 顶部上方、输入框正上方 8px）
- 新增加载环：`.is-generating::after` 粗描边 3px `var(--ink)` 圆环、`border-top-color: transparent`，
  `@keyframes btn-spin 0.9s linear infinite` 围绕按钮旋转——贴合 Neo-Brutalism 硬描边主题，不用细线 Material spinner
- hover 仍 `translateX(-50%) translateY(-2px)` 叠加，不破坏水平居中

## 行为

- 非输出：距底 >200px 显示，滚到底（≤200px）消失（沿用 onMessagesScroll）
- 输出中（isLoading=true）：按钮始终显示，外圈加载环旋转；点击仍平滑滚底
- 输出结束：加载环停止，恢复非输出显隐规则
- 切会话：沿用现有 isLoading 按 _generatingThreads 重算，A 会话生成中切 B 不残留加载环

## 验证

- 纯前端改动，零后端风险
- DOM 回读确认按钮在 input-area 内、绑定正确
- CSS 回读确认定位/加载环落盘
- 移动端 padding 断点：bottom 用相对高度百分比，两端自适应
