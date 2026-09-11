        /**
         * 应用主脚本：等待 Vue/VueRouter CDN 加载完成后再执行
         */
        (function boot() {
            // 防重入：deps-ready 事件与 8s 兜底定时器可能先后触发本函数两次，
            // 二次 createApp().mount() 会在卸载旧树时触发 removeFragment 的
            // nextSibling 空指针崩溃（白屏）。已挂载过则直接返回。
            if (window.__mittaBooted) {
                return;
            }
            if (!window.Vue || !window.VueRouter) {
                // 依赖未就绪：监听加载完成事件，并设置超时兜底
                window.addEventListener('deps-ready', boot, { once: true });
                setTimeout(function () {
                    if (window.Vue && window.VueRouter) {
                        boot();
                    } else {
                        // 超时仍未就绪：显示错误提示而非空白页
                        var app = document.getElementById('app');
                        if (app) {
                            app.innerHTML = [
                                '<div style="display:flex;min-height:100vh;align-items:center;justify-content:center;font-family:sans-serif;background:#F4F7FE;">',
                                '<div style="text-align:center;padding:40px;max-width:440px;">',
                                '<h2 style="margin-bottom:12px;color:#071024;">页面加载失败</h2>',
                                '<p style="color:#4A5B7A;line-height:1.7;font-size:14px;">',
                                'Vue 框架资源加载失败，请检查网络连接后刷新重试。</p>',
                                '</div></div>'
                            ].join('');
                        }
                    }
                }, 8000);
                return;
            }

            window.__mittaBooted = true; // 仅允许挂载一次

            const { createApp, ref, computed, onMounted, onUnmounted, nextTick } = Vue;
        const { createRouter, createWebHistory, useRouter } = VueRouter;

        // ============================================================
        // 常量与配置
        // ============================================================
        const API_BASE = ''; // 相对路径，与后端同域
        const STORAGE_KEY = {
            USER: 'mitta_auth_user',
            SESSIONS: 'mitta_sessions',
            CURRENT_THREAD: 'mitta_current_thread',
            LAST_ACTIVE: 'mitta_last_active',
            AI_CALL_NAME: 'mitta_ai_call_name' // 注册时设置的用户名，AI 以此称呼用户
        };
        const CACHE_TTL_DAYS = 7;


        // ============================================================
        // 工具函数
        // ============================================================
        function generateId() {
            return 'thread_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8);
        }

        function formatTime(date = new Date()) {
            return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // 工具名 → 中文操作概要映射（不直接暴露英文工具名给用户）
        const TOOL_SUMMARY_MAP = {
            // 文件操作
            'list_directory': '浏览目录',
            'list_directory_with_sizes': '浏览目录',
            'directory_tree': '查看目录树',
            'read_file': '读取文件',
            'read_text_file': '读取文本文件',
            'read_media_file': '读取媒体文件',
            'read_multiple_files': '批量读取文件',
            'search_files': '搜索文件',
            'get_file_info': '查看文件信息',
            'create_directory': '创建目录',
            'write_file': '写入文件',
            'create_file': '创建文件',
            'list_allowed_directories': '查看可访问目录',
            // 网页抓取
            'fetch': '抓取网页内容',
            // Git 操作
            'git_status': '查看 Git 状态',
            'git_log': '查看提交历史',
            'git_show': '查看提交详情',
            'git_diff': '查看代码差异',
            'git_diff_staged': '查看暂存差异',
            'git_diff_unstaged': '查看未暂存差异',
            'git_add': '暂存文件',
            'git_commit': '提交代码',
            'git_branch': '查看分支',
            'git_checkout': '切换分支',
            'git_create_branch': '创建分支',
            // 数据库
            'query': '查询数据库',
            'create-table': '创建数据表',
            'update-record': '更新记录',
            'describe-table': '查看表结构',
            'transaction': '数据库事务',
            // 知识图谱
            'create_entities': '创建知识实体',
            'create_relations': '创建知识关系',
            'add_observations': '添加观察记录',
            'read_graph': '读取知识图谱',
            'search_nodes': '搜索图谱节点',
            'open_nodes': '打开节点详情',
            // 其他
            'sequentialthinking': '逐步推理',
        };
        // 按工具类型返回对应图标 SVG（搜索/文件/代码/图谱/完成）
        function toolIcon(name, status) {
            if (status === 'running') {
                return '<span class="tool-inline-spinner"></span>';
            }
            // 搜索/网页类
            if (name === 'fetch' || name === 'search_files' || name === 'search_nodes') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>';
            }
            // 文件类
            if (name.startsWith('read_') || name.startsWith('list_') || name === 'directory_tree' || name === 'get_file_info') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>';
            }
            // 代码/Git 类
            if (name.startsWith('git_') || name === 'transaction') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>';
            }
            // 图谱类
            if (name === 'read_graph' || name === 'open_nodes' || name === 'create_entities' || name === 'create_relations' || name === 'add_observations') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="3"></circle><circle cx="5" cy="19" r="3"></circle><circle cx="19" cy="19" r="3"></circle><line x1="12" y1="8" x2="5" y2="16"></line><line x1="12" y1="8" x2="19" y2="16"></line></svg>';
            }
            // 数据库类
            if (name === 'create-table' || name === 'update-record' || name === 'query' || name === 'describe-table') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path></svg>';
            }
            // 推理类
            if (name === 'sequentialthinking') {
                return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"></path></svg>';
            }
            // 默认：对勾（完成）
            return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
        }

        // 根据工具名+参数生成自然语言概要（行内显示用）
        function toolSummary(name, args) {
            if (!name) return '工具调用';
            const a = args || {};
            // 文件类工具：显示文件名
            if (name === 'read_file' || name === 'read_text_file' || name === 'read_media_file') {
                return '读取文件' + (a.file_path ? ': ' + a.file_path.split(/[\\/]/).pop() : '');
            }
            if (name === 'read_multiple_files') return '读取多个文件';
            if (name === 'list_directory' || name === 'list_directory_with_sizes') {
                return '浏览目录' + (a.path ? ': ' + a.path : '');
            }
            if (name === 'search_files') return '搜索文件';
            if (name === 'directory_tree') return '查看目录树';
            if (name === 'get_file_info') return '查看文件信息';
            if (name === 'create_directory') return '创建目录';
            if (name === 'list_allowed_directories') return '查看允许目录';
            // 网页/抓取类
            if (name === 'fetch') return '获取网页内容' + (a.url ? ': ' + a.url.slice(0, 50) : '');
            // 知识图谱类
            if (name === 'read_graph') return '读取知识图谱';
            if (name === 'search_nodes') return '搜索图谱节点';
            if (name === 'open_nodes') return '打开节点详情';
            if (name === 'create_entities') return '创建实体';
            if (name === 'create_relations') return '创建关系';
            if (name === 'add_observations') return '添加观察';
            // 数据库类
            if (name === 'create-table') return '创建数据表';
            if (name === 'update-record') return '更新记录';
            if (name === 'query') return '查询数据';
            if (name === 'describe-table') return '查看表结构';
            // Git 类
            if (name === 'git_status') return '查看 Git 状态';
            if (name === 'git_log') return '查看提交记录';
            if (name === 'git_branch') return '查看分支';
            if (name === 'git_diff' || name === 'git_diff_staged' || name === 'git_diff_unstaged') return '查看代码变更';
            if (name === 'git_add') return '暂存文件';
            if (name === 'git_commit') return '提交代码';
            if (name === 'git_checkout') return '切换分支';
            if (name === 'git_create_branch') return '创建分支';
            // 推理类
            if (name === 'sequentialthinking') return '逐步推理';
            // 事务
            if (name === 'transaction') return '执行事务';
            // 兜底：已有映射表
            if (TOOL_SUMMARY_MAP[name]) return TOOL_SUMMARY_MAP[name];
            return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        }

        // 代码块复制功能（全局，供 marked 渲染的 HTML onclick 调用）
        function copyCodeBlock(btn) {
            const wrapper = btn.closest('.code-block-wrapper');
            if (!wrapper) return;
            const code = wrapper.querySelector('code');
            if (!code) return;
            navigator.clipboard.writeText(code.innerText).then(() => {
                const original = btn.innerHTML;
                btn.innerHTML = '<svg width=\"12\" height=\"12\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.5\"><polyline points=\"20 6 9 17 4 12\"></polyline></svg>';
                btn.style.color = '#10b981';
                setTimeout(() => { btn.innerHTML = original; btn.style.color = ''; }, 1500);
            }).catch(() => {});
        }

        // Markdown 渲染：marked 解析 + 自定义代码块窗口（标题栏+复制按钮）+ highlight.js
        function renderMarkdown(text) {
            if (!text) return '';
            if (typeof marked === 'undefined') return escapeHtml(text);
            // 预处理：压缩连续空行（3个以上换行→2个），去除行尾空格，避免 AI 输出大量空行
            let cleaned = text
                .replace(/\r\n/g, '\n')
                .replace(/[ \t]+\n/g, '\n')
                .replace(/\n{3,}/g, '\n\n');
            // 自定义代码块渲染：包裹成带语言标题栏+复制按钮的窗口
            const renderer = new marked.Renderer();
            renderer.code = function(codeOrObj, langOrUndef) {
                // 兼容 marked v12 旧式三参数 (code, infostring, escaped) 与新版对象形态
                let text, lang;
                if (codeOrObj !== null && typeof codeOrObj === 'object') {
                    text = codeOrObj.text || '';
                    lang = codeOrObj.lang;
                } else {
                    text = codeOrObj || '';
                    lang = langOrUndef;
                }
                const language = lang || 'code';
                const escaped = escapeHtml(text);
                return '<div class=\"code-block-wrapper\">' +
                    '<div class=\"code-block-header\">' +
                    '<span class=\"code-block-lang\">' + escapeHtml(language) + '</span>' +
                    '<button class=\"code-block-copy\" onclick=\"copyCodeBlock(this)\" title=\"复制代码\">' +
                    '<svg width=\"13\" height=\"13\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><rect x=\"9\" y=\"9\" width=\"13\" height=\"13\" rx=\"2\" ry=\"2\"></rect><path d=\"M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1\"></path></svg>' +
                    '</button></div>' +
                    '<pre><code class=\"language-' + escapeHtml(language) + '\">' + escaped + '</code></pre>' +
                    '</div>';
            };
            marked.setOptions({
                gfm: true,
                breaks: false,
                renderer: renderer,
            });
            let html = marked.parse(cleaned);
            // highlight.js 代码高亮（渲染后处理）
            if (typeof hljs !== 'undefined') {
                const tmp = document.createElement('div');
                tmp.innerHTML = html;
                tmp.querySelectorAll('pre code').forEach(block => {
                    try { hljs.highlightElement(block); } catch(e) {}
                });
                html = tmp.innerHTML;
            }
            return html;
        }

        // 兼容 content 为字符串或列表（AIMessageChunk 多模态格式）的情况
        function extractContentText(content) {
            if (typeof content === 'string') return content;
            if (Array.isArray(content)) {
                return content.map(part => {
                    if (typeof part === 'string') return part;
                    if (part && typeof part === 'object') return part.text || '';
                    return '';
                }).join('');
            }
            return '';
        }

        // ============================================================
        // 缓存管理
        // ============================================================
        const cache = {
            get(key, defaultValue = null) {
                try {
                    const raw = localStorage.getItem(key);
                    if (!raw) return defaultValue;
                    const data = JSON.parse(raw);
                    if (data && data.__expires && Date.now() > data.__expires) {
                        localStorage.removeItem(key);
                        return defaultValue;
                    }
                    return data.value;
                } catch {
                    return defaultValue;
                }
            },
            set(key, value, ttlDays = CACHE_TTL_DAYS) {
                try {
                    localStorage.setItem(key, JSON.stringify({
                        value,
                        __expires: Date.now() + ttlDays * 24 * 60 * 60 * 1000
                    }));
                } catch (e) {
                    console.warn('localStorage 写入失败:', e);
                }
            },
            remove(key) {
                localStorage.removeItem(key);
            },
            messagesKey(threadId) {
                return `mitta_messages_${threadId}`;
            },
            getMessages(threadId) {
                return this.get(this.messagesKey(threadId), []);
            },
            setMessages(threadId, messages) {
                this.set(this.messagesKey(threadId), messages);
            },
            removeMessages(threadId) {
                this.remove(this.messagesKey(threadId));
            }
        };

        function updateLastActive() {
            cache.set(STORAGE_KEY.LAST_ACTIVE, Date.now(), CACHE_TTL_DAYS);
        }

        // 会话缓存 key 按用户隔离：不同账号各看各的会话列表/当前线程，
        // 否则切换账号后侧边栏会残留上一个账号的会话（点击后触发 403，造成"会话共享"假象）
        function sessionCacheKey(key) {
            const u = cache.get(STORAGE_KEY.USER, null);
            return u && u.userId ? `${key}_${u.userId}` : key;
        }

        // ============================================================
        // API 调用
        // ============================================================
        // 构造带 JWT 的请求头（登录时 token 存于 STORAGE_KEY.USER.token）
        function authHeaders(extra = {}) {
            const user = cache.get(STORAGE_KEY.USER, null);
            if (user && user.token) {
                return { Authorization: `Bearer ${user.token}`, ...extra };
            }
            return extra;
        }

        // 隐式续签：access token 过期时后端自动用 refresh（仅存 Redis）换新 token，
        // 通过响应头 X-New-Access-Token 回传，这里同步本地登录态，前端全程无感知
        function syncTokenFromHeaders(headers) {
            const newToken = headers.get('x-new-access-token');
            if (!newToken) return;
            const user = cache.get(STORAGE_KEY.USER, null);
            if (user) {
                user.token = newToken;
                cache.set(STORAGE_KEY.USER, user);
            }
        }

        // 统一处理 401：token 缺失/过期，清除登录态并回到登录页
        function handleAuthError(response) {
            if (response.status === 401) {
                cache.remove(STORAGE_KEY.USER);
                if (window.location.pathname !== '/api/login') {
                    window.location.href = '/api/login';
                }
                throw new Error('登录已过期，请重新登录');
            }
        }

        // 获取用户个人信息（含 avatar、theme）
        async function apiGetProfile(userId) {
            const response = await fetch(`${API_BASE}/api/users/${userId}/profile`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (!response.ok) return null;
            const { ok, data } = await parseApiResponse(response);
            // parseApiResponse 的 data 是整个响应体 {ok, data:{...}}，profile 字段在 data.data 里
            return ok ? (data.data ?? null) : null;
        }

        async function apiChat(query, threadId, onStream, signal, onToolCall, fileIds, onReasoning, thinkingMode, reasoningEffort, clientMessageId) {
            const body = { query, thread_id: threadId };
            if (fileIds && fileIds.length > 0) {
                body.file_ids = fileIds;
            }
            // 深度思考设置：随每次请求传入，后端 llm_node 动态 bind
            body.thinking_mode = thinkingMode;
            body.reasoning_effort = reasoningEffort;
            // 消息唯一 ID：后端按 (user_id, client_message_id) 幂等去重，
            // 刷新重试/多标签页重复 POST 时后端不重复创建生成任务
            if (clientMessageId) {
                body.client_message_id = clientMessageId;
            }
            const response = await fetch(`${API_BASE}/api/chat/`, {
                method: 'POST',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
                signal: signal,
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                // 会话归属校验：会话属于其他账号
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权使用该会话（会话可能属于其他账号）');
                err.status = 403;
                throw err;
            }
            if (!response.ok) throw new Error('请求失败，请稍后再试');

            const contentType = response.headers.get('content-type') || '';
            if (!contentType.includes('text/event-stream')) {
                const data = await response.json();
                return data.answer || '';
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';
            let answer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const events = buffer.split('\n\n');
                buffer = events.pop();
                for (const event of events) {
                    const line = event.trim();
                    if (!line.startsWith('data:')) continue;
                    const payload = line.slice(5).trim();
                    if (!payload || payload === '[DONE]') continue;
                    let chunk;
                    try {
                        chunk = JSON.parse(payload);
                    } catch {
                        continue; // 跳过损坏的 SSE 数据，不中断整个流
                    }
                    // 服务端图执行异常（工具执行失败等）：抛给调用方展示，不再静默断流
                    if (chunk.error) throw new Error(chunk.error);
                    // 幂等拦截：同一条消息（client_message_id）已被处理过。
                    // 抛特殊错误码 DUPLICATE，sendMessage catch 中回滚本地占位并触发重放，
                    // 避免界面再叠加一条重复消息（后端不会创建新生成任务）
                    if (chunk.duplicate) {
                        const err = new Error('该消息已发送过，正在恢复原回复');
                        err.code = 'DUPLICATE';
                        throw err;
                    }
                    // 工具调用开始事件：通知前端显示加载界面
                    if (chunk.tool_call_start && onToolCall) {
                        onToolCall({ type: 'start', name: chunk.tool_call_start.name, args: chunk.tool_call_start.args });
                    }
                    // 工具调用结束事件：通知前端隐藏加载界面
                    if (chunk.tool_call_end && onToolCall) {
                        onToolCall({ type: 'end', name: chunk.tool_call_end.name, content: chunk.tool_call_end.content });
                    }
                    // 深度思考内容：DeepSeek 推理模型的思考过程，前端折叠展示，不混入正文
                    if (chunk.reasoning && onReasoning) {
                        onReasoning(chunk.reasoning);
                    }
                    const text = extractContentText(chunk.content);
                    if (text) {
                        answer += text;
                        if (onStream) onStream(answer);
                    }
                }
            }
            return answer;
        }

        // 从后端恢复当前用户的会话列表（刷新/换浏览器/清缓存后 localStorage 为空时的兜底）
        async function apiListSessions() {
            const response = await fetch(`${API_BASE}/api/chat/sessions`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (!response.ok) throw new Error('获取会话列表失败，请稍后再试');
            const json = await response.json();
            return json && json.data ? json.data : [];
        }

        async function apiGetHistory(threadId) {
            const response = await fetch(`${API_BASE}/api/chat/${threadId}/history`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                // 会话归属校验：会话属于其他账号
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权访问该会话（会话可能属于其他账号）');
                err.status = 403;
                throw err;
            }
            if (!response.ok) throw new Error('获取历史消息失败，请稍后再试');
            return response.json();
        }

        // 查询会话是否仍在后台生成回复（刷新后用于自动续接未完成的 AI 回复）
        async function apiGetGenerationStatus(threadId) {
            const response = await fetch(`${API_BASE}/api/chat/${threadId}/generation-status`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权访问该会话');
                err.status = 403;
                throw err;
            }
            if (!response.ok) throw new Error('查询生成状态失败，请稍后再试');
            const json = await response.json();
            return json && json.data ? json.data.generating : false;
        }

        // 读取会话的流式事件流（序号 > after）：刷新后重放思考/工具/正文断点，
        // 再配合轮询续收新事件，实现"刷新后过程可见、继续流式"的体验
        async function apiGetEvents(threadId, after = -1) {
            const response = await fetch(`${API_BASE}/api/chat/${threadId}/events?after=${after}`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权访问该会话');
                err.status = 403;
                throw err;
            }
            if (!response.ok) throw new Error('读取事件流失败，请稍后再试');
            const json = await response.json();
            return json && json.data && Array.isArray(json.data.events) ? json.data.events : [];
        }

        async function apiDeleteSession(threadId) {
            const response = await fetch(`${API_BASE}/api/chat/${threadId}`, {
                method: 'DELETE',
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                // 会话归属校验：会话属于其他账号
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权删除该会话（会话可能属于其他账号）');
                err.status = 403;
                throw err;
            }
            if (!response.ok) throw new Error('删除会话失败，请稍后再试');
            return response.json();
        }

        // 回滚会话到指定轮次：删除该轮用户消息及其后的旧 AI 回复/工具链（重新生成前置步骤）。
        // 后端以 query 文本为锚点定位该轮起点（最后一个匹配的 HumanMessage），
        // 删除后 checkpoint 不再残留旧回复，重新生成时上下文干净、token 不重复累积。
        async function apiRollbackSession(threadId, query) {
            const response = await fetch(`${API_BASE}/api/chat/${threadId}/rollback`, {
                method: 'POST',
                headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                body: JSON.stringify({ query, thread_id: threadId })
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (response.status === 403) {
                const data = await response.json().catch(() => ({}));
                const err = new Error(data.detail || '无权操作该会话（会话可能属于其他账号）');
                err.status = 403;
                throw err;
            }
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.message || data.detail || '回滚失败，请稍后再试');
            }
            return response.json();
        }

        // ===== 知识库管理 API（仅管理员可执行写操作）=====
        // 文档列表：登录用户可读，用于前端展示知识库内容
        async function apiListKnowledge() {
            const response = await fetch(`${API_BASE}/api/knowledge/documents`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || data.message || '获取知识库列表失败，请稍后再试');
            }
            const { ok, data } = await parseApiResponse(response);
            // parseApiResponse 的 data 是整个响应体 {ok, data:{...}}，实际数据在 data.data 里
            return ok ? (data.data ?? { documents: [], total_chunks: 0 }) : { documents: [], total_chunks: 0 };
        }

        // 上传文档入库（仅管理员）：multipart/form-data
        async function apiUploadKnowledge(file) {
            const formData = new FormData();
            formData.append('file', file);
            const response = await fetch(`${API_BASE}/api/knowledge/upload`, {
                method: 'POST',
                headers: authHeaders(), // 不手动设 Content-Type，浏览器自动带 multipart boundary
                body: formData
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            const { ok, data, message } = await parseApiResponse(response);
            if (!response.ok || !ok) {
                const err = new Error(message || data?.detail || '上传失败，请稍后再试');
                err.status = response.status;
                throw err;
            }
            return data.data ?? data;
        }

        // 删除文档来源（仅管理员）：删除该 source 的全部 chunk
        async function apiDeleteKnowledgeSource(source) {
            const response = await fetch(`${API_BASE}/api/knowledge/source/${encodeURIComponent(source)}`, {
                method: 'DELETE',
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            const { ok, data, message } = await parseApiResponse(response);
            if (!response.ok || !ok) {
                const err = new Error(message || data?.detail || '删除失败，请稍后再试');
                err.status = response.status;
                throw err;
            }
            return data.data ?? data;
        }

        async function apiHealthCheck() {
            try {
                const response = await fetch(`${API_BASE}/health`, { method: 'GET' });
                if (!response.ok) return { status: 'degraded', db: false };
                return response.json();
            } catch {
                return { status: 'degraded', db: false };
            }
        }

        // 统一解析后端 JSON 响应：兼容 401/400 错误体与 422 校验错误的 detail 数组
        async function parseApiResponse(res) {
            let data = {};
            try {
                data = await res.json();
            } catch {
                // 非 JSON 响应（如 500 空页），走下方统一错误文案
            }
            if (data.ok) return { ok: true, data };
            let message = data.message;
            if (!message && Array.isArray(data.detail)) {
                message = data.detail.map(d => d.msg || '').filter(Boolean).join('；');
            }
            return { ok: false, message: message || '请求失败，请稍后再试' };
        }

        // ============================================================
        // 认证相关组件
        // ============================================================
        const LoginForm = {
            template: `
                <div class="auth-form-wrapper">
                    <div class="auth-card">
                        <div class="auth-tag">01 / SIGN IN</div>
                        <h1>欢迎回来</h1>
                        <p class="auth-subtitle">{{ subtitle }}</p>
                        <form @submit.prevent="handleLogin">
                            <div class="form-group">
                                <label class="form-label">用户 ID</label>
                                <input v-model="form.userId" type="text" class="form-input" placeholder="请输入用户 ID" autocomplete="username">
                                <div class="form-error">{{ errors.userId }}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">密码</label>
                                <input v-model="form.password" type="password" class="form-input" placeholder="请输入密码" autocomplete="current-password">
                                <div class="form-error">{{ errors.password }}</div>
                            </div>
                            <button type="submit" class="btn btn-primary auth-submit" :disabled="isSubmitting">
                                <span v-if="isSubmitting">登录中...</span>
                                <span v-else>登 录  →</span>
                            </button>
                        </form>
                        <div class="auth-links">
                            <router-link to="/api/register">注册账号</router-link>
                            <router-link to="/api/recover">忘记密码？</router-link>
                        </div>
                    </div>
                </div>
            `,
            data() {
                return {
                    form: { userId: '', password: '' },
                    errors: { userId: '', password: '' },
                    isSubmitting: false,
                    subtitle: '登录后继续与 Mitta 智能助理对话'
                };
            },
            mounted() {
                // 注册成功后跳转回来时，给出引导提示（贴合 Mitta 元气风格）
                if (this.$route.query.registered === '1') {
                    this.subtitle = '注册成功！请使用新账号登录 (๑•̀ㅂ•́)و✧';
                }
            },
            methods: {
                validate() {
                    let valid = true;
                    this.errors = { userId: '', password: '' };
                    if (!this.form.userId.trim()) {
                        this.errors.userId = '请输入用户 ID';
                        valid = false;
                    }
                    if (!this.form.password) {
                        this.errors.password = '请输入密码';
                        valid = false;
                    }
                    return valid;
                },
                async handleLogin() {
                    if (!this.validate()) return;
                    this.isSubmitting = true;
                    try {
                        // 后端认证：MySQL 用户表校验，成功返回 { ok, token, user_info }
                        const res = await fetch(`${API_BASE}/api/login`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ userId: this.form.userId.trim(), password: this.form.password })
                        });
                        const { ok, data, message } = await parseApiResponse(res);
                        // 后端契约：ok=true 且 user_info 非空才算登录成功（用户不存在时返回 401）
                        if (ok && data.user_info) {
                            // 只存展示所需字段并剔除密码等敏感信息，避免整行落 localStorage
                            const u = data.user_info;
                            const userData = {
                                userId: u.userId ?? u.user_id,
                                name: u.name ?? u.username ?? u.userName,
                                role: u.role || '学员',
                                token: data.token
                            };
                            // 先存 token 到 localStorage，否则 apiGetProfile 里的 authHeaders() 读不到 token 会 401
                            cache.set(STORAGE_KEY.USER, userData);
                            // 登录后获取用户 profile（含 avatar），避免重新登录后头像失效
                            try {
                                const profile = await apiGetProfile(userData.userId);
                                if (profile && profile.avatar) {
                                    userData.avatar = profile.avatar;
                                    cache.set(STORAGE_KEY.USER, userData);  // 拿到 avatar 后更新缓存
                                }
                            } catch (e) {
                                // profile 获取失败不影响登录，头像使用默认
                            }
                            updateLastActive();
                            this.$router.push('/chat');
                        } else {
                            this.errors.password = message || '用户 ID 或密码错误';
                        }
                    } catch (err) {
                        this.errors.password = '网络异常，请稍后重试';
                    } finally {
                        this.isSubmitting = false;
                    }
                }
            }
        };

        const RegisterForm = {
            template: `
                <div class="auth-form-wrapper">
                    <div class="auth-card">
                        <div class="auth-tag tag-mint">02 / JOIN US</div>
                        <h1>创建账号</h1>
                        <p class="auth-subtitle">注册后即可体验智能助理服务</p>
                        <form @submit.prevent="handleRegister">
                            <div class="form-group">
                                <label class="form-label">用户名</label>
                                <input v-model="form.name" type="text" class="form-input" placeholder="设置用户名，AI 将这样称呼您" maxlength="20">
                                <div class="form-error">{{ errors.name }}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">用户 ID</label>
                                <input v-model="form.userId" type="text" class="form-input" placeholder="设置用户 ID">
                                <div class="form-error">{{ errors.userId }}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">密码</label>
                                <input v-model="form.password" type="password" class="form-input" placeholder="设置密码">
                                <div class="form-error">{{ errors.password }}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">确认密码</label>
                                <input v-model="form.confirmPassword" type="password" class="form-input" placeholder="再次输入密码">
                                <div class="form-error">{{ errors.confirmPassword }}</div>
                            </div>
                            <div class="form-error auth-msg">{{ formMsg }}</div>
                            <button type="submit" class="btn btn-primary auth-submit" :disabled="isSubmitting">
                                <span v-if="isSubmitting">注册中...</span>
                                <span v-else>注 册  →</span>
                            </button>
                        </form>
                        <div class="auth-links">
                            <router-link to="/api/login">已有账号？登录</router-link>
                        </div>
                    </div>
                </div>
            `,
            data() {
                return {
                    form: { userId: '', password: '', confirmPassword: '', name: '' },
                    errors: { userId: '', password: '', confirmPassword: '', name: '' },
                    formMsg: '',
                    isSubmitting: false
                };
            },
            methods: {
                validate() {
                    let valid = true;
                    this.errors = { userId: '', password: '', confirmPassword: '', name: '' };
                    if (!this.form.userId.trim()) {
                        this.errors.userId = '请输入用户 ID';
                        valid = false;
                    }
                    if (this.form.password.length < 4) {
                        this.errors.password = '密码至少 4 位';
                        valid = false;
                    }
                    if (this.form.password !== this.form.confirmPassword) {
                        this.errors.confirmPassword = '两次输入密码不一致';
                        valid = false;
                    }
                    if (!this.form.name.trim()) {
                        this.errors.name = '请输入用户名（AI 称呼）';
                        valid = false;
                    }
                    return valid;
                },
                async handleRegister() {
                    if (!this.validate()) return;
                    this.isSubmitting = true;
                    // 本地保存 AI 称呼，注册成功后登录即生效
                    cache.set(STORAGE_KEY.AI_CALL_NAME, this.form.name.trim());
                    try {
                        // 字段与后端 RegisterRequest 契约一致：userName / userId / password
                        const res = await fetch(`${API_BASE}/api/register`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                userName: this.form.name.trim(),
                                userId: this.form.userId.trim(),
                                password: this.form.password
                            })
                        });
                        const { ok, message } = await parseApiResponse(res);
                        if (ok) {
                            this.formMsg = '注册成功，正在跳转登录...';
                            setTimeout(() => {
                                this.$router.push('/api/login?registered=1');
                            }, 1200);
                        } else {
                            this.formMsg = message || '注册失败';
                        }
                    } catch (err) {
                        this.formMsg = '网络异常，请稍后重试';
                    } finally {
                        this.isSubmitting = false;
                    }
                }
            }
        };

        const RecoverForm = {
            template: `
                <div class="auth-form-wrapper">
                    <div class="auth-card tag-violet">
                        <div class="auth-tag">03 / RESET</div>
                        <h1>找回密码</h1>
                        <p class="auth-subtitle">输入用户 ID，我们将为您重置密码</p>
                        <form @submit.prevent="handleRecover">
                            <div class="form-group">
                                <label class="form-label">用户 ID</label>
                                <input v-model="form.userId" type="text" class="form-input" placeholder="请输入用户 ID">
                                <div class="form-error">{{ errors.userId }}</div>
                            </div>
                            <div class="form-group">
                                <label class="form-label">新密码</label>
                                <input v-model="form.newPassword" type="password" class="form-input" placeholder="设置新密码">
                                <div class="form-error">{{ errors.newPassword }}</div>
                            </div>
                            <div class="form-error auth-msg">{{ formMsg }}</div>
                            <button type="submit" class="btn btn-primary auth-submit" :disabled="isSubmitting">
                                <span v-if="isSubmitting">处理中...</span>
                                <span v-else>重置密码  →</span>
                            </button>
                        </form>
                        <div class="auth-links">
                            <router-link to="/api/login">返回登录</router-link>
                        </div>
                    </div>
                </div>
            `,
            data() {
                return {
                    form: { userId: '', newPassword: '' },
                    errors: { userId: '', newPassword: '' },
                    formMsg: '',
                    isSubmitting: false
                };
            },
            methods: {
                validate() {
                    let valid = true;
                    this.errors = { userId: '', newPassword: '' };
                    if (!this.form.userId.trim()) {
                        this.errors.userId = '请输入用户 ID';
                        valid = false;
                    }
                    if (this.form.newPassword.length < 4) {
                        this.errors.newPassword = '新密码至少 4 位';
                        valid = false;
                    }
                    return valid;
                },
                async handleRecover() {
                    if (!this.validate()) return;
                    this.isSubmitting = true;
                    try {
                        // 字段与后端 RecoverRequest 契约一致：userId / newPassword
                        const res = await fetch(`${API_BASE}/api/recover`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ userId: this.form.userId.trim(), newPassword: this.form.newPassword })
                        });
                        const { ok, message } = await parseApiResponse(res);
                        this.formMsg = message || (ok ? '密码已重置' : '重置失败');
                    } catch (err) {
                        this.formMsg = '网络异常，请稍后重试';
                    } finally {
                        this.isSubmitting = false;
                    }
                }
            }
        };

        // 认证页左侧品牌区动态文案（随路由模式切换；人格标签已移除，统一为 Mitta，语气风格不变）
        const AUTH_QUOTES = {
            login: {
                title: '哈啊～欢迎回来… Mitta 等你等到好困了呢 (´-ω-｀)',
                desc: '困困的…不过你回来，我就打起精神啦～登录后，知识管家陪你继续大冒险哦 (。-ω-)'
            },
            register: {
                title: '欢迎加入 Mitta 的温柔小窝 (｡･ω･｡)ﾉ♡',
                desc: '牵住 Mitta 的手，创建账号，专属知识管家即刻上线～'
            },
            recover: {
                title: '密码想不起来了？哼哼，没有我的允许，你可别想跑 ♡',
                desc: 'Mitta 会一直守在这里盯着你，重置好密码，乖乖回来哦 (￢‿￢)'
            }
        };

        // 三态米塔形象素材（透明底 PNG；登录=睡衣米塔）
        const MITA_IMAGES = {
            login: { src: '/assets/img/mita_pajama.png',  name: 'Mitta' },
            register: { src: '/assets/img/mita_kind.png', name: 'Mitta' },
            recover: { src: '/assets/img/mita_crazy.png', name: 'Mitta' }
        };

        const AuthLayout = {
            template: `
                <div class="auth-layout">
                    <div class="auth-brand">
                        <div class="auth-side-deco">NEO · TOKYO</div>
                        <!-- 氛围弧线：红色/金色弧段作背景点缀（米塔为主体，弧线弱化氛围） -->
                        <svg class="auth-ring" :class="['ring-' + authMode, ringAnim]" viewBox="0 0 400 400" aria-hidden="true">
                            <circle class="ring-fan" cx="200" cy="200" r="150"/>
                            <circle class="ring-main" cx="200" cy="200" r="150"/>
                            <circle class="ring-sub" cx="200" cy="200" r="150"/>
                            <circle class="ring-dot" cx="200" cy="50" r="11"/>
                        </svg>
                        <!-- 米塔形象：容器按三态位移/缩放，img 交叉淡化换脸
                             登录=睡衣米塔（左下角），注册/找回右缘对齐右边线 -->
                        <div class="auth-mita" :class="'mita-' + authMode" aria-hidden="true">
                            <transition name="mita-fade">
                                <img :key="authMode" :src="mitaImg" :alt="mitaName">
                            </transition>
                        </div>
                        <div class="auth-logo">
                            <div class="auth-logo-mark"><img src="/favicon.png" alt=""></div>
                            <span>Mitta AI</span>
                        </div>
                        <transition :name="quoteAnim" mode="out-in">
                            <div class="auth-quote" :key="authMode">
                                <h2>{{ quote.title }}</h2>
                                <p>{{ quote.desc }}</p>
                            </div>
                        </transition>
                        <div class="auth-footer">© 2026 MITTA AI — TAKE YOUR HEART</div>
                    </div>
                    <router-view v-slot="{ Component }">
                        <!-- 不用 out-in 过渡：mode="out-in" 切换时计算 anchor 会触发 nextSibling 空指针崩溃 -->
                        <component v-if="Component" :is="Component" />
                    </router-view>
                </div>
            `,
            data() {
                return { ringAnim: '', quoteAnim: 'quote-swap' };
            },
            watch: {
                // 圆环切换方向动画：登录→注册=波动 / 登录→找回=变大缩回 / 返回登录=蓝色覆盖生长
                authMode(n, o) {
                    if (!o) return;
                    if (o === 'login' && n === 'register') this.ringAnim = 'ring-wave';
                    else if (o === 'login' && n === 'recover') this.ringAnim = 'ring-grow';
                    else if (o !== 'login' && n === 'login') this.ringAnim = 'ring-cover';
                    else this.ringAnim = '';
                    // 提示词滚轮方向：注册目标=上滑；找回回登录=上滑；其余=下滑
                    const rollUp = (n === 'register' && o !== 'register') || (o === 'recover' && n === 'login');
                    this.quoteAnim = rollUp ? 'quote-up' : 'quote-down';
                    if (this.ringAnim) {
                        clearTimeout(this._ringTimer);
                        this._ringTimer = setTimeout(() => { this.ringAnim = ''; }, 1100);
                    }
                }
            },
            computed: {
                // 由当前路由推导认证模式：login / register / recover
                authMode() {
                    const p = this.$route.path;
                    if (p.includes('register')) return 'register';
                    if (p.includes('recover')) return 'recover';
                    return 'login';
                },
                quote() {
                    return AUTH_QUOTES[this.authMode] || AUTH_QUOTES.login;
                },
                mitaImg() {
                    return (MITA_IMAGES[this.authMode] || MITA_IMAGES.login).src;
                },
                mitaName() {
                    return (MITA_IMAGES[this.authMode] || MITA_IMAGES.login).name;
                }
            }
        };

        // ============================================================
        // 聊天应用组件
        // ============================================================
        const ChatApp = {
            template: `
                <div class="chat-layout">
                    <!-- ══════════ 侧边栏 ══════════ -->
                    <aside class="sidebar" :class="{ open: sidebarOpen }" role="navigation" aria-label="会话列表">
                        <div class="sidebar-vert-deco">MITTA</div>
                        <div class="sidebar-header">
                            <div class="sidebar-brand">
                                <div class="sidebar-brand-mark"><img src="/favicon.png" alt=""></div>
                                <span>Mitta AI</span>
                            </div>
                            <button class="new-chat-btn" @click="createNewSession" title="新建会话" aria-label="新建会话">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                                    <line x1="12" y1="5" x2="12" y2="19"></line>
                                    <line x1="5" y1="12" x2="19" y2="12"></line>
                                </svg>
                            </button>
                        </div>
                        <div class="session-list">
                            <div v-if="sessions.length === 0" class="empty-state">
                                <p>暂无会话</p>
                            </div>
                            <button
                                v-for="session in sessions"
                                :key="session.id"
                                class="session-item"
                                :class="{ active: session.id === currentThreadId }"
                                @click="switchSession(session.id)"
                                :aria-label="'切换到会话: ' + session.title"
                            >
                                <svg class="session-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                                </svg>
                                <span class="session-name">{{ session.title }}</span>
                                <span class="session-delete" @click.stop="deleteSession(session.id)" role="button" aria-label="删除会话">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                                        <line x1="18" y1="6" x2="6" y2="18"></line>
                                        <line x1="6" y1="6" x2="18" y2="18"></line>
                                    </svg>
                                </span>
                            </button>
                        </div>
                        <div class="sidebar-footer">
                            <div class="user-menu-wrapper">
                                <div v-if="userMenuOpen" class="user-dropdown" @click.stop>
                                    <button class="user-dropdown-item" @click="openProfileModal">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                                        个人信息
                                    </button>
                                    <button class="user-dropdown-item" @click="openSettingsModal">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
                                        系统设置
                                    </button>
                                </div>
                                <div class="user-card">
                                    <button class="user-avatar-btn" @click="toggleUserMenu">
                                        <div class="user-avatar">
                                            <img v-if="user?.avatar" :src="user.avatar" alt="头像">
                                            <span v-else>{{ userAvatar }}</span>
                                        </div>
                                        <div class="user-info">
                                            <div class="user-name">{{ user?.name || '用户' }}</div>
                                            <div class="user-role">{{ user?.role || '学员' }}</div>
                                        </div>
                                    </button>
                                    <button class="logout-btn" @click="logout" title="退出登录" aria-label="退出登录">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
                                            <polyline points="16 17 21 12 16 7"></polyline>
                                            <line x1="21" y1="12" x2="9" y2="12"></line>
                                        </svg>
                                    </button>
                                </div>
                            </div>
                            <div class="health-status">
                                <span class="health-dot" :class="healthStatus"></span>
                                <span>{{ healthText }}</span>
                            </div>
                        </div>
                    </aside>

                    <div class="overlay" :class="{ active: sidebarOpen }" @click="closeSidebar"></div>

                    <!-- ══════════ 主区域 ══════════ -->
                    <main class="main-area">
                        <header class="main-header">
                            <div class="header-left">
                                <button class="menu-toggle" @click="openSidebar" aria-label="展开菜单">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
                                        <line x1="3" y1="6" x2="21" y2="6"></line>
                                        <line x1="3" y1="12" x2="21" y2="12"></line>
                                        <line x1="3" y1="18" x2="21" y2="18"></line>
                                    </svg>
                                </button>
                                <h2 class="current-session-title">{{ currentSessionTitle }}</h2>
                            </div>
                            <div class="header-actions">
                                <button class="icon-btn" @click="deleteSession(currentThreadId)" title="删除当前会话" aria-label="清空当前会话">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="3 6 5 6 21 6"></polyline>
                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                    </svg>
                                </button>
                            </div>
                        </header>

                        <div class="messages-container" ref="messagesContainer">
                            <div class="messages-wrapper">
                                <!-- 欢迎空态 -->
                                <div v-if="messages.length === 0" class="welcome-state">
                                    <div class="welcome-badge">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
                                            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                                        </svg>
                                        智能助理已就绪
                                    </div>
                                    <h1>您好，{{ greetingName }}！<br>有什么可以帮您的？</h1>
                                    <p>开始一段新的对话，或从左侧选择一个历史会话继续交流。</p>
                                    <div class="quick-actions">
                                        <button v-for="q in randomQuestions" :key="q" class="quick-action" @click="sendQuick(q)">{{ q }}</button>
                                    </div>
                                    <button class="refresh-questions-btn" @click="refreshQuestions()" title="换一批提示词">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                                        换一批
                                    </button>
                                </div>

                                <!-- 消息列表 -->
                                <div
                                    v-for="(msg, index) in messages"
                                    :key="msg.id || index"
                                    class="message"
                                    :class="msg.role"
                                    :data-msg-id="msg.id"
                                >
                                    <div class="message-avatar" aria-hidden="true">
                                        <img v-if="msg.role === 'assistant'" src="/favicon.png" alt="AI">
                                        <img v-else-if="user?.avatar" :src="user.avatar" alt="用户">
                                        <span v-else>{{ msg.role === 'user' ? '我' : 'AI' }}</span>
                                    </div>
                                    <div class="message-content-wrapper">
                                        <div class="message-meta">
                                            <span>{{ msg.role === 'user' ? '您' : 'AI 助手' }}</span>
                                            <span>·</span>
                                            <span>{{ msg.time }}</span>
                                        </div>
                                        <!-- AI 消息：blocks 穿插渲染 -->
                                        <template v-if="msg.role === 'assistant'">
                                            <div class="message-content">
                                                <template v-if="msg.blocks && msg.blocks.length > 0">
                                                    <template v-for="(block, bIdx) in msg.blocks" :key="bIdx">
                                                        <!-- 深度思考块：独立折叠（同工具调用），流式中展开，结束后自动收起 -->
                                                        <div v-if="block.type === 'reasoning' && block.content" class="reasoning-inline" :class="{ collapsed: !block.expanded }">
                                                            <div class="reasoning-inline-header" @click="block.expanded = !block.expanded">
                                                                <svg class="reasoning-inline-bulb" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
                                                                <span class="reasoning-inline-title">深度思考</span>
                                                                <svg class="reasoning-inline-arrow" :class="{ expanded: block.expanded }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
                                                            </div>
                                                            <div v-show="block.expanded" class="reasoning-inline-text" v-html="escapeHtml(block.content)"></div>
                                                        </div>
                                                        <div v-if="block.type === 'text' && block.content" class="markdown-body" v-html="renderMarkdown(block.content)"></div>
                                                        <div v-else-if="block.type === 'tool'" class="tool-call-inline" :class="{ running: block.status === 'running', done: block.status === 'done' }">
                                                            <span class="tool-inline-icon" v-html="toolIcon(block.name, block.status)"></span>
                                                            <span class="tool-inline-summary">{{ toolSummary(block.name, block.args) }}</span>
                                                            <span class="tool-inline-toggle" @click="block.expanded = !block.expanded">
                                                                <svg :class="{ expanded: block.expanded }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
                                                            </span>
                                                            <div v-show="block.expanded && block.result" class="tool-inline-result-block">
                                                                <pre class="tool-inline-result-text">{{ block.result }}</pre>
                                                            </div>
                                                            <div v-show="block.expanded && block.args && Object.keys(block.args).length > 0" class="tool-inline-detail">
                                                                <div class="tool-inline-label">参数</div>
                                                                <pre class="tool-inline-json">{{ JSON.stringify(block.args, null, 2) }}</pre>
                                                            </div>
                                                        </div>
                                                    </template>
                                                </template>
                                                <div v-else-if="msg.content" class="markdown-body" v-html="renderMarkdown(msg.content)"></div>
                                                <div v-show="!msg.content" class="thinking-indicator">
                                                    <div class="thinking-dots"><span></span><span></span><span></span></div>
                                                    <span class="thinking-text">正在思考...</span>
                                                </div>
                                                <div v-if="currentToolCall && msg === messages[messages.length - 1]" class="tool-call-indicator">
                                                    <div class="tool-call-spinner"></div>
                                                    <span class="tool-call-text">正在调用工具：<strong>{{ currentToolCall.name }}</strong></span>
                                                </div>
                                            </div>
                                        </template>
                                        <!-- 用户消息：纯文本 -->
                                        <div v-else class="message-content user-text">{{ msg.content }}</div>
                                        <!-- AI 消息操作栏 -->
                                        <div v-if="msg.role === 'assistant' && msg.content && !isLoading" class="message-actions">
                                            <button class="msg-action-btn" @click="copyMessage(msg)" title="复制">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                                <span>复制</span>
                                            </button>
                                            <button class="msg-action-btn" @click="shareMessage(msg)" title="分享">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>
                                                <span>分享</span>
                                            </button>
                                            <button class="msg-action-btn" @click="regenerateMessage(msg)" title="重新生成">
                                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
                                                <span>重新生成</span>
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- ══════════ 输入区 ══════════ -->
                        <div class="input-area">
                            <div v-if="uploadedFiles.length > 0" class="uploaded-files">
                                <div v-for="(file, idx) in uploadedFiles" :key="idx" class="uploaded-file-card">
                                    <div class="file-icon">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="9" y1="15" x2="15" y2="15"></line></svg>
                                    </div>
                                    <div class="file-info">
                                        <span class="file-name">{{ file.name }}</span>
                                        <span class="file-type">File</span>
                                    </div>
                                    <span class="remove" @click="removeUploadedFile(idx)">×</span>
                                </div>
                            </div>
                            <div class="input-card">
                                <textarea
                                    v-model="inputText"
                                    class="chat-input"
                                    placeholder="输入您的问题..."
                                    rows="1"
                                    aria-label="消息输入框"
                                    @keydown="handleKeydown"
                                    @input="autoResize"
                                    ref="textarea"
                                ></textarea>
                                <div class="input-actions">
                                    <!-- 深度思考 -->
                                    <div class="thinking-toggle-wrapper">
                                        <button
                                            class="thinking-btn"
                                            :class="{ active: thinkingMode }"
                                            @click="toggleThinkingMode"
                                            :title="thinkingMode ? '深度思考已开启' : '开启深度思考'"
                                        >
                                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"></path></svg>
                                            <span v-if="thinkingMode" class="thinking-label" @click.stop="toggleEffortPanel">{{ reasoningEffort === 'low' ? '低' : reasoningEffort === 'high' ? '高' : 'max' }}</span>
                                        </button>
                                        <div v-if="thinkingMode && thinkingPanelOpen" class="thinking-effort-panel">
                                            <span
                                                v-for="effort in ['low', 'high', 'max']"
                                                :key="effort"
                                                class="effort-option"
                                                :class="{ selected: reasoningEffort === effort }"
                                                @click="setEffort(effort)"
                                            >{{ effort === 'low' ? '低' : effort === 'high' ? '高' : 'max' }}</span>
                                        </div>
                                    </div>
                                    <!-- 上传 -->
                                    <div class="user-menu-wrapper" style="position: relative;">
                                        <button v-if="uploadMenuOpen" class="upload-dropdown" @click.stop>
                                            <label class="upload-dropdown-item" style="cursor: pointer;">
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
                                                上传文件
                                                <input type="file" ref="fileInput" @change="handleFileUpload" style="display: none;" multiple accept=".txt,.md,.csv,.json,.xml,.html,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.gif,.webp,.py,.js,.ts,.java,.zip,.rar,.7z">
                                            </label>
                                        </button>
                                        <button class="upload-btn" @click="toggleUploadMenu" title="上传文件" aria-label="上传文件">
                                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                                        </button>
                                    </div>
                                    <!-- 发送 / 停止 -->
                                    <button v-if="!isLoading" class="send-btn" @click="sendMessage" :disabled="!canSend" aria-label="发送消息">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                                            <line x1="22" y1="2" x2="11" y2="13"></line>
                                            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                                        </svg>
                                    </button>
                                    <button v-else class="stop-btn" @click="stopResponse" title="停止回复" aria-label="停止回复">
                                        <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>
                                        <span>停止</span>
                                    </button>
                                </div>
                            </div>
                            <p class="input-hint">按 Enter 发送，Shift + Enter 换行</p>
                        </div>
                    </main>

                    <!-- ══════════ 个人信息弹窗 ══════════ -->
                    <div v-if="profileModalOpen" class="modal-overlay" @click.self="closeProfileModal">
                        <div class="modal" @mousedown.stop>
                            <div class="modal-header">
                                <h3>个人信息</h3>
                                <button class="modal-close" @click="closeProfileModal" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <div class="form-group">
                                    <label>头像</label>
                                    <div class="avatar-selector">
                                        <div class="avatar-preview">
                                            <img v-if="profileForm.avatar" :src="profileForm.avatar" alt="头像">
                                            <span v-else>{{ profileForm.username?.charAt(0)?.toUpperCase() || 'U' }}</span>
                                        </div>
                                        <div class="avatar-actions">
                                            <label class="btn-ghost" style="cursor: pointer;">
                                                上传头像
                                                <input type="file" accept="image/*" @change="handleAvatarUpload" style="display: none;">
                                            </label>
                                            <button class="btn-ghost" @click="profileForm.avatar = ''">使用默认</button>
                                        </div>
                                    </div>
                                </div>
                                <div class="form-group">
                                    <label>用户名</label>
                                    <input v-model="profileForm.username" type="text" placeholder="请输入用户名" maxlength="32">
                                </div>
                                <div class="form-group">
                                    <label>助手风格 / 自定义设定</label>
                                    <textarea v-model="profileForm.system_prompt" placeholder="描述你希望 AI 助手具备的风格、角色设定或特殊要求（如：你是一个专业的编程助手，回答简洁，多用代码示例）" rows="4" maxlength="3000"></textarea>
                                    <div class="form-hint">此设定会作为全局 system prompt 注入每次对话，影响 AI 的回答风格（最多 3000 字）</div>
                                </div>
                                <hr style="border: none; border-top: 2px dashed var(--line-soft); margin: 20px 0;">
                                <div class="form-group">
                                    <label>原密码</label>
                                    <input v-model="profileForm.old_password" type="password" placeholder="请输入原密码">
                                </div>
                                <div class="form-group">
                                    <label>新密码</label>
                                    <input v-model="profileForm.new_password" type="password" placeholder="请输入新密码（至少6位）">
                                </div>
                                <div class="form-group">
                                    <label>确认新密码</label>
                                    <input v-model="profileForm.confirm_password" type="password" placeholder="请再次输入新密码">
                                </div>
                            </div>
                            <div class="modal-footer">
                                <button class="btn-ghost" @click="closeProfileModal">取消</button>
                                <button class="btn btn-primary" @click="saveProfile" :disabled="profileSaving">
                                    {{ profileSaving ? '保存中...' : '保存修改' }}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- ══════════ 系统设置弹窗 ══════════ -->
                    <div v-if="settingsModalOpen" class="modal-overlay" @click.self="closeSettingsModal">
                        <div class="modal" @mousedown.stop>
                            <div class="modal-header">
                                <h3>系统设置</h3>
                                <button class="modal-close" @click="closeSettingsModal" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <div class="form-group">
                                    <label>主题配色</label>
                                    <div class="theme-grid">
                                        <div v-for="t in themes" :key="t.value" class="theme-option" :class="{ active: settingsForm.theme === t.value }" @click="settingsForm.theme = t.value; applyTheme(t.value)">
                                            <div class="theme-preview" :style="{ background: t.preview }"></div>
                                            <div class="theme-name">{{ t.name }}</div>
                                        </div>
                                    </div>
                                </div>
                                <hr style="border: none; border-top: 2px dashed var(--line-soft); margin: 20px 0;">
                                <div class="form-group">
                                    <label>MCP 服务器配置</label>
                                    <div class="form-hint" style="margin-bottom: 10px;">配置存储在本地 JSON 文件中，可自定义存储路径。直接粘贴 JSON 数组，每项支持 name / command / args / cwd / type(stdio|sse) / url 等字段。</div>
                                    <div style="margin-bottom: 12px;">
                                        <label style="font-size: 13px; color: var(--text-secondary); display: block; margin-bottom: 6px;">配置文件路径</label>
                                        <input
                                            v-model="mcpConfigPath"
                                            type="text"
                                            placeholder="如：E:/工作文件/AgentProject/resources/config/mcp_servers.json"
                                            class="mcp-path-input"
                                        >
                                        <div class="form-hint">允许路径：项目 resources/、config/ 目录，或用户主目录下任意路径</div>
                                    </div>
                                    <textarea
                                        v-model="mcpJsonText"
                                        class="mcp-json-editor"
                                        placeholder='[&#10;  {&#10;    "name": "文件系统",&#10;    "command": "npx",&#10;    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"]&#10;  }&#10;]'
                                        rows="10"
                                        spellcheck="false"
                                    ></textarea>
                                    <div style="display: flex; gap: 8px; margin-top: 8px;">
                                        <button type="button" class="btn-ghost" @click="formatMcpJson" style="flex: 1;">格式化 JSON</button>
                                        <button type="button" class="btn-ghost" @click="clearMcpJson" style="flex: 1;">清空</button>
                                    </div>
                                    <div v-if="mcpJsonError" class="form-error" style="margin-top: 6px;">{{ mcpJsonError }}</div>
                                </div>

                                <!-- 知识库管理（仅管理员可见） -->
                                <template v-if="isAdmin">
                                    <hr style="border: none; border-top: 2px dashed var(--line-soft); margin: 20px 0;">
                                    <div class="form-group">
                                        <label>知识库管理</label>
                                        <div class="form-hint" style="margin-bottom: 10px;">向知识库增量添加文档（RAG 检索 + BM25 全文双通道），支持 .md/.txt/.pdf，单文件 ≤ 10MB，同内容重复上传自动覆盖。</div>
                                        <div style="display: flex; gap: 8px; margin-bottom: 12px;">
                                            <input
                                                ref="kbUploadInput"
                                                type="file"
                                                accept=".md,.txt,.pdf"
                                                class="mcp-path-input"
                                                style="flex: 1;"
                                            >
                                            <button type="button" class="btn btn-primary" @click="uploadKnowledgeFile" :disabled="kbUploading" style="white-space: nowrap;">
                                                {{ kbUploading ? '上传中...' : '上传入库' }}
                                            </button>
                                        </div>
                                        <div v-if="kbLoading" class="form-hint">加载知识库列表...</div>
                                        <div v-else-if="kbDocs.length === 0" class="form-hint">知识库暂无文档，上传一个 .md/.txt/.pdf 开始构建。</div>
                                        <div v-else style="max-height: 220px; overflow-y: auto; border: 1px solid var(--line-soft); border-radius: 8px;">
                                            <div v-for="doc in kbDocs" :key="doc.source" style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-bottom: 1px solid var(--line-soft); gap: 8px;">
                                                <div style="min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">
                                                    <div style="font-size: 13px;">{{ doc.source }}</div>
                                                    <div class="form-hint" style="font-size: 12px;">{{ doc.chunks }} 个分块</div>
                                                </div>
                                                <button type="button" class="btn-ghost" style="flex-shrink: 0; font-size: 12px; padding: 4px 10px;" @click="deleteKnowledgeSource(doc.source)">删除</button>
                                            </div>
                                        </div>
                                        <div class="form-hint" style="margin-top: 8px;">共 {{ kbTotalChunks }} 个分块</div>
                                    </div>
                                </template>
                            </div>
                            <div class="modal-footer">
                                <button class="btn-ghost" @click="closeSettingsModal">取消</button>
                                <button class="btn btn-primary" @click="saveSettings" :disabled="settingsSaving">
                                    {{ settingsSaving ? '保存中...' : '保存设置' }}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- ══════════ MCP 重启提示弹窗 ══════════ -->
                    <div v-if="restartNoticeOpen" class="modal-overlay" @click.self="restartNoticeOpen = false">
                        <div class="modal" @mousedown.stop style="max-width: 480px;">
                            <div class="modal-header">
                                <h3>配置已保存</h3>
                                <button class="modal-close" @click="restartNoticeOpen = false" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <div style="display: flex; gap: 12px; align-items: flex-start;">
                                    <div class="restart-icon">!</div>
                                    <div>
                                        <p style="margin: 0 0 10px 0; font-weight: 800;">MCP 配置需重启后端生效</p>
                                        <p style="margin: 0 0 8px 0; color: var(--text-2); line-height: 1.6;">MCP 服务器在后端服务启动时初始化并编译进对话图，运行中修改配置不会自动热重载。</p>
                                        <p style="margin: 0; color: var(--text-2); line-height: 1.6;">请重启后端服务（<code class="inline-code">python main.py</code>）后，新配置的 MCP 工具才会在对话中生效。</p>
                                        <p style="margin: 10px 0 0 0; color: var(--mint); font-size: 13px; font-weight: 700;">✓ 主题配色已即时生效，无需重启。</p>
                                    </div>
                                </div>
                            </div>
                            <div class="modal-footer">
                                <button class="btn btn-primary" @click="confirmRestartNotice">知道了</button>
                            </div>
                        </div>
                    </div>
                </div>
            `,
            setup() {
                const router = useRouter();
                const user = ref(cache.get(STORAGE_KEY.USER, null));
                const sessions = ref(cache.get(sessionCacheKey(STORAGE_KEY.SESSIONS), []));
                const currentThreadId = ref(cache.get(sessionCacheKey(STORAGE_KEY.CURRENT_THREAD), null));
                const messages = ref([]);
                const inputText = ref('');
                const isLoading = ref(false);
                const streaming = ref(false);
                const currentToolCall = ref(null);  // 当前正在调用的工具 {name, args}，用于显示加载界面
                const sidebarOpen = ref(false);
                const healthStatus = ref('online');
                const messagesContainer = ref(null);
                const textarea = ref(null);
                const fileInput = ref(null);

                // ===== 新增：用户菜单 / 弹窗 / 上传 / 主题 =====
                const userMenuOpen = ref(false);
                const uploadMenuOpen = ref(false);
                const profileModalOpen = ref(false);
                const settingsModalOpen = ref(false);
                const restartNoticeOpen = ref(false);
                const profileSaving = ref(false);
                const settingsSaving = ref(false);
                const mcpJsonText = ref('');
                const mcpJsonError = ref('');
                const mcpConfigPath = ref('');
                const uploadedFiles = ref([]);
                const abortController = ref(null);
                // 正在生成回复的会话集合（按 thread_id 跟踪）：支持"生成中切换会话"。
                // isLoading 是"当前视图会话是否生成中"的快照，切会话时由 switchSession 重算，
                // 避免 A 会话生成中切到 B 导致 B 的发送按钮被全局 isLoading 锁死。
                const _generatingThreads = new Set();
                // 会话重命名：editingSessionId 记录当前编辑的会话 ID，null 表示无编辑
                const editingSessionId = ref(null);
                const renameInput = ref(null);
                function startRenameSession(sessionId) {
                    editingSessionId.value = sessionId;
                    Vue.nextTick(() => {
                        const inp = document.querySelector('.session-rename-input');
                        if (inp) { inp.focus(); inp.select(); }
                    });
                }
                function saveSessionTitle(sessionId, newTitle) {
                    const title = (newTitle || '').trim();
                    if (title) {
                        const s = sessions.value.find(s => s.id === sessionId);
                        if (s) s.title = title;
                        saveSessions();
                    }
                    editingSessionId.value = null;
                }
                function cancelRenameSession() {
                    editingSessionId.value = null;
                }
                // 深度思考设置：用户可在前端切换，状态持久化到 localStorage
                const thinkingMode = ref(localStorage.getItem('thinkingMode') === 'true');
                const reasoningEffort = ref(localStorage.getItem('reasoningEffort') || 'low');
                const thinkingPanelOpen = ref(false);  // 思考设置面板展开状态
                // 封装方法：Vue 模板不能直接访问全局 localStorage，必须通过 setup 方法调用
                function toggleThinkingMode() {
                    thinkingMode.value = !thinkingMode.value;
                    localStorage.setItem('thinkingMode', thinkingMode.value);
                    // 开启思考时自动展开强度选择面板，关闭时收起
                    thinkingPanelOpen.value = thinkingMode.value;
                }
                function setEffort(effort) {
                    reasoningEffort.value = effort;
                    localStorage.setItem('reasoningEffort', effort);
                    // 选择强度后自动收起面板
                    thinkingPanelOpen.value = false;
                }
                // 点击强度标签重新展开面板（不改变开关状态）
                function toggleEffortPanel() {
                    if (thinkingMode.value) {
                        thinkingPanelOpen.value = !thinkingPanelOpen.value;
                    }
                }

                // ── AI 消息操作：复制 / 分享 / 重新生成 ──
                function copyMessage(msg) {
                    // 去除 HTML 标签，复制纯文本；content 可能为空时防御
                    const text = (msg.content || '').replace(/<[^>]*>/g, '');
                    const fallbackCopy = () => {
                        // 降级：textarea + execCommand（HTTP/IP 非安全上下文必走此路径）
                        const ta = document.createElement('textarea');
                        ta.value = text;
                        document.body.appendChild(ta);
                        ta.select();
                        try { document.execCommand('copy'); } catch (e) {}
                        document.body.removeChild(ta);
                        showToast('已复制到剪贴板', 'success');
                    };
                    // HTTP/IP 非安全上下文下 navigator.clipboard 为 undefined，必须先判空，
                    // 否则直接访问 .writeText 会同步抛 TypeError（promise catch 捕获不到）
                    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                        navigator.clipboard.writeText(text).then(() => {
                            showToast('已复制到剪贴板', 'success');
                        }).catch(fallbackCopy);
                    } else {
                        fallbackCopy();
                    }
                }

                function shareMessage(msg) {
                    const text = msg.content.replace(/<[^>]*>/g, '');
                    if (navigator.share) {
                        // 移动端原生分享
                        navigator.share({ title: 'Mitta AI 回复', text }).catch(() => {});
                    } else {
                        // 桌面端：复制到剪贴板
                        copyMessage(msg);
                    }
                }

                async function regenerateMessage(msg) {
                    // 记录本次重新生成所属会话：后端回滚是异步的，期间用户可能切换到
                    // 其他会话，后续 splice/saveMessages/sendMessage 都必须校验"当前视图
                    // 仍是该会话"，否则旧问题会发到新会话、污染新会话缓存（与 sendMessage
                    // 的 sendThreadId 守卫同一模式，修复"重新生成时切走再切回旧回复仍在"）。
                    const regThreadId = currentThreadId.value;
                    // 找到这条 AI 消息对应的上一条用户消息
                    const idx = messages.value.findIndex(m => m.id === msg.id);
                    if (idx <= 0) return;
                    const userMsg = messages.value[idx - 1];
                    if (!userMsg || userMsg.role !== 'user') return;
                    if (isLoading.value) {
                        showToast('请等待当前回复完成后再重新生成', 'warning');
                        return;
                    }
                    // 【乐观删除】点击立即移除该轮消息并落缓存：即使后端回滚较慢，
                    // 界面也立刻清空旧回复；用户切走再切回时本地缓存已无旧回复。
                    const removed = messages.value.splice(idx - 1, 2);
                    saveMessages();
                    // 【后端回滚】并行删除 checkpoint 中该轮的旧 AI 回复与工具链（带超时），
                    // 以用户消息文本为锚点定位轮次（前端消息 id 与后端 checkpoint id 不对应）。
                    let rollbackOk = false;
                    try {
                        await Promise.race([
                            (async () => {
                                await apiRollbackSession(regThreadId, userMsg.content);
                                rollbackOk = true;
                            })(),
                            new Promise((_, reject) => setTimeout(() => reject(new Error('回滚超时')), 5000))
                        ]);
                    } catch (e) {
                        if (e && e.status === 403) {
                            showToast(e.message, 'error');
                            // 无权回滚：恢复本地消息，避免前后端不一致
                            if (currentThreadId.value === regThreadId) {
                                messages.value.splice(idx - 1, 0, ...removed);
                                saveMessages();
                            }
                            return;
                        }
                        console.warn('重新生成后端回滚失败:', e);
                    }
                    if (!rollbackOk) {
                        // 回滚失败/超时：后端 checkpoint 未删除，本地也不能删（否则刷新后
                        // 旧回复从后端恢复、与本地不一致）。恢复该轮消息并取消本次重新生成。
                        if (currentThreadId.value === regThreadId) {
                            messages.value.splice(idx - 1, 0, ...removed);
                            saveMessages();
                        }
                        showToast('后端历史回滚失败，已取消重新生成，请重试', 'warning');
                        return;
                    }
                    // 【会话守卫】回滚期间用户已切到其他会话：中止本次重新生成，
                    // 不把旧问题发送到新会话，也不污染新会话缓存。
                    if (currentThreadId.value !== regThreadId) return;
                    // 用用户消息的内容重新发送
                    inputText.value = userMsg.content;
                    sendMessage();
                }

                const profileForm = ref({
                    username: '',
                    avatar: '',
                    system_prompt: '',
                    old_password: '',
                    new_password: '',
                    confirm_password: '',
                });

                const settingsForm = ref({
                    theme: 'default',
                    mcp_servers: [],
                });

                // ===== 知识库管理（仅管理员可见/可操作）=====
                const kbDocs = ref([]);
                const kbTotalChunks = ref(0);
                const kbLoading = ref(false);
                const kbUploading = ref(false);
                const kbUploadInput = ref(null);
                const isAdmin = computed(() => user.value?.role === 'admin');
                async function loadKnowledgeDocs() {
                    if (!isAdmin.value) return;
                    kbLoading.value = true;
                    try {
                        const data = await apiListKnowledge();
                        kbDocs.value = data.documents || [];
                        kbTotalChunks.value = data.total_chunks || 0;
                    } catch (e) {
                        showToast(e.message || '获取知识库列表失败', 'error');
                    } finally {
                        kbLoading.value = false;
                    }
                }
                async function uploadKnowledgeFile() {
                    const input = kbUploadInput.value;
                    if (!input || !input.files || !input.files.length) {
                        showToast('请先选择要上传的文档', 'warning');
                        return;
                    }
                    const file = input.files[0];
                    if (!/\.(md|txt|pdf)$/i.test(file.name)) {
                        showToast('仅支持 .md / .txt / .pdf 格式', 'warning');
                        return;
                    }
                    if (file.size > 10 * 1024 * 1024) {
                        showToast('文件大小不能超过 10MB', 'warning');
                        return;
                    }
                    kbUploading.value = true;
                    try {
                        const result = await apiUploadKnowledge(file);
                        const written = result?.written ?? 0;
                        showToast(`入库成功：${file.name}（${written} 个分块）`, 'success');
                        input.value = '';
                        await loadKnowledgeDocs();
                    } catch (e) {
                        showToast(e.message || '上传失败', 'error');
                    } finally {
                        kbUploading.value = false;
                    }
                }
                async function deleteKnowledgeSource(source) {
                    if (!confirm(`确认删除知识库文档「${source}」的全部分块？\n删除后不可恢复。`)) return;
                    try {
                        const result = await apiDeleteKnowledgeSource(source);
                        showToast(`已删除 ${source}（${result?.deleted ?? 0} 个分块）`, 'success');
                        await loadKnowledgeDocs();
                    } catch (e) {
                        showToast(e.message || '删除失败', 'error');
                    }
                }

                const themes = [
                    { value: 'default', name: '默认', preview: 'linear-gradient(135deg, #00CFFD, #F4F7FE)' },
                    { value: 'dark', name: '深色', preview: 'linear-gradient(135deg, #00E0FF, #050913)' },
                    { value: 'ocean', name: '海洋', preview: 'linear-gradient(135deg, #0099FF, #071024)' },
                    { value: 'sunset', name: '日落', preview: 'linear-gradient(135deg, #FF5C9A, #071024)' },
                    { value: 'forest', name: '森林', preview: 'linear-gradient(135deg, #00D68F, #071024)' },
                    { value: 'lavender', name: '薰衣草', preview: 'linear-gradient(135deg, #9D7BFF, #071024)' },
                ];

                // 知识库主题提示词池：基于 AI Agent 全栈开发知识库（10大主题），每次随机选取 4 个展示
                const QUESTION_POOL = [
                    // Python 最佳实践
                    'Python 中 TypedDict 和 Pydantic 如何做结构化数据校验？',
                    'asyncio.to_thread 和 ThreadPoolExecutor 分别适用于什么场景？',
                    '如何设计自定义异常类和优雅降级策略？',
                    // FastAPI 后端
                    'FastAPI 的 lifespan 生命周期中应该初始化哪些资源？',
                    '如何用 Depends 实现 JWT 认证和资源归属校验？',
                    'FastAPI 中间件如何实现请求限流？',
                    // LangGraph 架构
                    'LangGraph 中 StateGraph、节点和条件边的关系是什么？',
                    'LangGraph 的 Send 扇出机制如何实现并行工具调用？',
                    '如何用 LangGraph 构建一个完整的检索增强对话图？',
                    // RAG 检索系统
                    'RAG 系统中文档切分策略有哪些，如何选择？',
                    'Query 改写为什么能提升检索效果，如何实现？',
                    'ChromaDB 如何初始化和批量入库文档？',
                    'bge-m3 嵌入模型在 RAG 中的优势是什么？',
                    // 数据库设计
                    '多存储架构中 MySQL、PostgreSQL、Redis 各自承担什么角色？',
                    '用户表和用户扩展信息表为什么要分表设计？',
                    'MySQL 连接池如何配置和管理？',
                    // 系统架构
                    '分层架构中各层的职责和依赖方向是什么？',
                    '服务层为什么要用单例模式，生命周期如何管理？',
                    '上下文管理在 AI Agent 系统中如何实现？',
                    // 异常处理
                    '异常处理分层架构中全局异常和业务异常如何分工？',
                    '为什么业务错误不应该都抛异常，什么时候用返回值？',
                    // 安全认证
                    'JWT + Redis 双 Token 机制如何实现无感续签？',
                    '密码为什么要用 bcrypt 哈希，如何防止彩虹表攻击？',
                    'Token 主动失效如何通过 Redis 实现？',
                    // 前端 Vue3
                    'Vue 3 Composition API 中 ref 和 reactive 如何选择？',
                    '前端如何用 Fetch + ReadableStream 接收 SSE 流式响应？',
                    // 工程化实践
                    'AI Agent 项目的目录结构应该如何设计？',
                    '日志管理中如何区分业务日志和调试日志？',
                    '代码规范中命名和注释有哪些最佳实践？',
                ];

                // 当前展示的随机提示词（ref，触发响应式更新）
                const randomQuestions = ref([]);

                // 从池中随机选取 n 个不重复的提示词
                const refreshQuestions = (n = 4) => {
                    const pool = [...QUESTION_POOL];
                    const result = [];
                    const count = Math.min(n, pool.length);
                    for (let i = 0; i < count; i++) {
                        const idx = Math.floor(Math.random() * pool.length);
                        result.push(pool.splice(idx, 1)[0]);
                    }
                    randomQuestions.value = result;
                };

                const currentSessionTitle = computed(() => {
                    const session = sessions.value.find(s => s.id === currentThreadId.value);
                    return session?.title || '新会话';
                });

                const userAvatar = computed(() => {
                    return user.value?.name?.charAt(0).toUpperCase() || 'U';
                });

                // AI 对用户的称呼：注册时设置的用户名优先，其次登录用户昵称
                const greetingName = computed(() => {
                    return cache.get(STORAGE_KEY.AI_CALL_NAME) || user.value?.name || '同学';
                });

                const canSend = computed(() => {
                    return inputText.value.trim() && !isLoading.value;
                });

                const healthText = computed(() => {
                    if (healthStatus.value === 'online') return '服务正常';
                    if (healthStatus.value === 'degraded') return '服务异常';
                    return '离线';
                });

                const loadCurrentMessages = async () => {
                    if (!currentThreadId.value) {
                        messages.value = [];
                        return;
                    }
                    try {
                        // 先请求后端历史做归属校验（403 抛错），不能跳过校验直接读本地
                        const history = await apiGetHistory(currentThreadId.value);
                        // 校验通过后优先用本地缓存：后端 checkpoint 只存标准消息，
                        // 不含前端扩展字段（blocks 穿插、reasoning 深度思考、tool_calls），
                        // 直接用后端历史会导致刷新后工具调用记录和深度思考全部丢失
                        const cached = cache.getMessages(currentThreadId.value);
                        const lastCached = cached && cached.length > 0 ? cached[cached.length - 1] : null;
                        // 未完成 AI（在途回复）判定，覆盖生成中刷新的四种形态：
                        // ① catch 打断标记 interrupted；② 占位文本（旧缓存无标记）；
                        // ③ 无正文且无 block（刚发送、模型尚未吐字）；
                        // ④ 只有 reasoning/tool block、尚无任何非空 text 块（思考/工具阶段，正文未产出）
                        const isIncompleteAiMsg = (m) => {
                            if (!m || m.role !== 'assistant') return false;
                            if (m.interrupted || m.content === '（回复中断，请重新生成）') return true;
                            if (m.content && m.content.trim()) return false; // 已有正文，本地视为展示完整
                            if (!m.blocks || m.blocks.length === 0) return true;
                            const hasText = m.blocks.some(b => b.type === 'text' && b.content && b.content.trim());
                            return !hasText; // 无正文文本块 = 仍在思考/工具阶段
                        };
                        const lastIncomplete = isIncompleteAiMsg(lastCached);
                        const remoteMsgs = parseHistory(history).map(normalizeMessage);
                        const lastRemote = remoteMsgs.length > 0 ? remoteMsgs[remoteMsgs.length - 1] : null;
                        // 后端最后一条是否为"已落库的完整 assistant 回复"（有正文）
                        const remoteReplyComplete = !!lastRemote && lastRemote.role === 'assistant'
                            && !!(lastRemote.content && lastRemote.content.trim());
                        if (lastIncomplete && remoteReplyComplete) {
                            // 本地在途，但后端"断连不中断"后台线程已补全落库 → 用后端完整回复
                            messages.value = remoteMsgs;
                        } else if (lastIncomplete) {
                            // 本地在途、后端尚未补全（生成刚开始 checkpoint 未提交，history 可能为 []）：
                            // 【关键】必须保留本地缓存（至少有用户消息 + AI 占位），
                            // 绝不能用空 history 把界面清成"新会话"
                            messages.value = cached && cached.length > 0 ? cached.map(normalizeMessage) : remoteMsgs;
                        } else if (cached && cached.length > 0) {
                            messages.value = cached.map(normalizeMessage);
                        } else {
                            messages.value = remoteMsgs;
                        }
                        // 【自动续接】本地末尾是在途 AI、且当前展示最后一条仍未拿到完整正文 →
                        // 轮询 history 直到完整回复落库。保留本地占位时 lastMsg 正是在途 AI，
                        // 不能因 messages 被清空（旧 bug）而让启动条件失效。
                        const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : null;
                        if (lastIncomplete && isIncompleteAiMsg(lastMsg)) {
                            startGenerationResume(currentThreadId.value);
                        } else if (lastCached && lastCached.role === 'assistant') {
                            // 兜底：本地末尾 assistant 已有部分正文（流式中途刷新），仅凭内容
                            // 无法判断是否完成，向后端确认生成状态，仍在生成则同样启动续接
                            const tid = currentThreadId.value;
                            apiGetGenerationStatus(tid).then(generating => {
                                if (generating && currentThreadId.value === tid) {
                                    startGenerationResume(tid);
                                }
                            }).catch(() => {});
                        }
                    } catch (err) {
                        if (err && err.status === 403) {
                            // 会话属于其他账号：从列表移除并提示，避免残留
                            const removed = currentThreadId.value;
                            sessions.value = sessions.value.filter(s => s.id !== removed);
                            cache.removeMessages(removed);
                            saveSessions();
                            if (sessions.value.length > 0) {
                                currentThreadId.value = sessions.value[0].id;
                                saveCurrentThread();
                                await loadCurrentMessages();
                            } else {
                                createNewSession();
                            }
                            showToast('该会话不属于当前账号，已移除', 'error');
                            return;
                        }
                        // 网络/服务异常：回退本地缓存兜底，保证弱网下仍可阅读
                        const cached = cache.getMessages(currentThreadId.value);
                        messages.value = cached && cached.length > 0 ? cached.map(normalizeMessage) : [];
                        // 弱网下 history 拉取失败也不能丢续接：本地末尾是 assistant（在途回复）时，
                        // 仍启动轮询，网络恢复、后端落库后自动补全
                        const lastCachedOnErr = cached && cached.length > 0 ? cached[cached.length - 1] : null;
                        if (lastCachedOnErr && lastCachedOnErr.role === 'assistant') {
                            const tid = currentThreadId.value;
                            apiGetGenerationStatus(tid).then(generating => {
                                if (generating && currentThreadId.value === tid) {
                                    startGenerationResume(tid);
                                }
                            }).catch(() => {});
                        }
                    }
                };

                // 消息归一化：给旧格式消息补充新字段，避免模板渲染时 undefined.length 报错
                const normalizeMessage = (msg) => {
                    if (msg.role === 'assistant') {
                        msg.tool_calls = msg.tool_calls || [];
                        msg.reasoning = msg.reasoning || '';
                        // blocks 不存在时（旧消息），用 content 初始化一个文本块
                        if (!msg.blocks) {
                            msg.blocks = msg.content ? [{ type: 'text', content: msg.content }] : [];
                        }
                    }
                    return msg;
                };

                // 【事件重放】把结构化流式事件应用到 AI 消息上（增量语义，与 SSE 实时路径同构）。
                // 事件来自后端 Redis 事件流（reasoning/content 为增量、tool_call_start/end 为状态点），
                // 刷新后按产生顺序重放即可重建思考块/工具块/正文到断点，再续收新事件继续流式。
                const _applyEventsToMsg = (aiMsg, events) => {
                    if (!aiMsg || !Array.isArray(events) || events.length === 0) return;
                    if (!aiMsg.blocks) aiMsg.blocks = [];
                    let latestText = aiMsg.content || '';
                    for (const item of events) {
                        const ev = item && item.event ? item.event : item;
                        // 深度思考增量：全量追加到 reasoning，再按增量同步 blocks
                        if (ev.reasoning) {
                            aiMsg.reasoning = (aiMsg.reasoning || '') + ev.reasoning;
                            _syncReasoningBlock(aiMsg, aiMsg.reasoning);
                        }
                        // 正文增量：全量追加到 content，再按增量同步 blocks
                        if (ev.content) {
                            latestText = (latestText || '') + ev.content;
                            aiMsg.content = latestText;
                            _syncTextBlock(aiMsg, latestText);
                        }
                        // 工具调用开始：插入 running 工具块
                        if (ev.tool_call_start) {
                            aiMsg.blocks.push({
                                type: 'tool',
                                name: ev.tool_call_start.name,
                                args: ev.tool_call_start.args || {},
                                result: '',
                                status: 'running',
                                expanded: false,
                                time: formatTime()
                            });
                        }
                        // 工具调用结束：更新最后一个 running 同名工具块
                        if (ev.tool_call_end) {
                            for (let i = aiMsg.blocks.length - 1; i >= 0; i--) {
                                const b = aiMsg.blocks[i];
                                if (b.type === 'tool' && b.status === 'running' && b.name === ev.tool_call_end.name) {
                                    b.status = 'done';
                                    b.result = ev.tool_call_end.content || '';
                                    break;
                                }
                            }
                        }
                    }
                };

                // 【自动续接】刷新/强刷后自动恢复未完成的 AI 回复（"刷新不断流"体验）。
                // 后端"断连不中断生成"：客户端刷新只停 SSE 推送，后台线程继续跑完图并提交
                // checkpoint；同时每个流式事件（思考/工具/正文增量）实时落库 Redis 事件流。
                // 前端刷新后：
                //   ① 重放已落库事件（after=-1 从头）→ 思考块/工具块/正文立即重建到断点；
                //   ② 每 2s 续收 after=lastSeq 的新事件 → 界面继续滚动（像没有断过流）；
                //   ③ history 兜底最终一致性：完整回复落库后整体替换。
                // 这样刷新后"过程可见 + 继续流式"，用户不会误判"没在回复"而重复发送。
                let _resumeTimer = null;
                let _resumeStopped = false;
                const startGenerationResume = (threadId) => {
                    // 幂等：已有轮询在跑或已被标记停止时不再重复启动
                    if (_resumeTimer || _resumeStopped) return;
                    // 轮询间隔 2s，最长等 5 分钟（后台生成可能含深度思考/多轮工具调用）
                    const RESUME_POLL_INTERVAL = 2000;
                    const RESUME_MAX_WAIT = 5 * 60 * 1000;
                    const deadline = Date.now() + RESUME_MAX_WAIT;
                    // 记录当前续接的会话：用户中途切换会话则停止旧轮询
                    const targetThreadId = threadId;
                    // 已重放到的最大事件序号（-1 = 尚未重放任何事件）
                    let lastAppliedSeq = -1;
                    showToast('回复仍在生成中，正在自动续接…', 'info');
                    // 单轮续接：重放新事件 → history 兜底 → 生成状态判定。
                    // 首次立即执行（刷新后马上重建思考/工具断点），之后每 2s 续收
                    const pollOnce = async () => {
                        // 用户已切到其他会话：停止续接（保留当前会话内容）
                        if (currentThreadId.value !== targetThreadId) {
                            stopGenerationResume();
                            return;
                        }
                        const [history, generating, newEvents] = await Promise.all([
                            apiGetHistory(targetThreadId),
                            apiGetGenerationStatus(targetThreadId),
                            apiGetEvents(targetThreadId, lastAppliedSeq),
                        ]);

                        // ① 事件续收：把 lastSeq 之后的新事件重放到当前 AI 消息
                        if (newEvents && newEvents.length > 0) {
                            // 定位要重放的目标：messages 末尾的 assistant 消息。
                            // 本地无占位（缓存丢失/跨浏览器刷新/空 AI 被过滤）时新建一个，
                            // 让重放的事件有承载对象——刷新后思考/工具过程因此可见
                            let aiMsg = messages.value.length > 0
                                ? messages.value[messages.value.length - 1]
                                : null;
                            if (!aiMsg || aiMsg.role !== 'assistant') {
                                aiMsg = { id: generateId(), role: 'assistant', content: '', reasoning: '', tool_calls: [], blocks: [], time: formatTime() };
                                messages.value.push(aiMsg);
                            }
                            // 首次重放（从 -1 开始）从零重建：事件流是完整增量序列
                            // （与 SSE 同源），本地 blocks 只是不完整投影，直接重置后
                            // 重放可保证与后端状态一致，避免叠加重复
                            if (lastAppliedSeq === -1) {
                                aiMsg.reasoning = '';
                                aiMsg.content = '';
                                aiMsg.blocks = [];
                            }
                            _applyEventsToMsg(aiMsg, newEvents);
                            lastAppliedSeq = newEvents[newEvents.length - 1].seq;
                            saveMessages();
                            scrollToBottom();
                        }

                        // ② history 最终一致性兜底：完整回复落库后整体替换
                        const msgs = parseHistory(history).map(normalizeMessage);
                        const lastMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
                        const replyComplete = lastMsg && lastMsg.role === 'assistant'
                            && (lastMsg.content || (lastMsg.blocks && lastMsg.blocks.length > 0));
                        if (replyComplete) {
                            // 后端已完成并落库：渲染完整回复并更新本地缓存，停止轮询
                            messages.value = msgs;
                            saveMessages();
                            scrollToBottom();
                            stopGenerationResume();
                            showToast('回复已续接完成', 'success');
                            return;
                        }
                        if (!generating) {
                            // 后台生成已结束但 history 仍无完整回复（生成异常/被中止）。
                            // 【关键】parseHistory 会过滤空正文的 ai（仅挂载 tool_calls 的中转
                            // 消息），此时 msgs 最后一条是 human，replyComplete 恒为 false——
                            // 若没有本分支，轮询会干等到 5 分钟超时，用户误以为"还没生成完"
                            // 而反复发送同一问题（实测同一会话累积 6 条 human）。
                            // 这里把本地未完成占位标记为失败并明确提示，让用户点重新生成。
                            const cached = cache.getMessages(targetThreadId);
                            const lastC = cached && cached.length > 0 ? cached[cached.length - 1] : null;
                            if (lastC && lastC.role === 'assistant') {
                                lastC.interrupted = true;
                                if (!lastC.content || !lastC.content.trim()) {
                                    lastC.content = '（回复生成失败，请点击重新生成）';
                                    lastC.blocks = [];
                                }
                                cache.setMessages(targetThreadId, cached);
                                messages.value = cached.map(normalizeMessage);
                                saveMessages();
                            }
                            stopGenerationResume();
                            showToast('回复生成未成功，请点击重新生成', 'warning');
                            return;
                        }
                        if (Date.now() > deadline) {
                            stopGenerationResume();
                            showToast('等待回复超时，请稍后重新加载', 'warning');
                        }
                    };
                    // 立即重放一次（重建断点），再进入周期续收
                    pollOnce().catch(() => { /* 单轮失败由下一轮重试 */ });
                    _resumeTimer = setInterval(() => {
                        pollOnce().catch(() => {
                            // 轮询期间网络抖动/接口异常：继续下一次轮询（不中断续接）
                            if (Date.now() > deadline) {
                                stopGenerationResume();
                            }
                        });
                    }, RESUME_POLL_INTERVAL);
                };

                // 停止自动续接轮询（切换会话/续接完成/超时时调用）
                const stopGenerationResume = () => {
                    if (_resumeTimer) {
                        clearInterval(_resumeTimer);
                        _resumeTimer = null;
                    }
                };

                // 同步文本块：保证 blocks 中文本块与流式全文一致
                // 最后一个块是 text → 更新它；最后一个块是 tool（工具后新文本）→ 新建文本块
                // 从而实现"文本-工具-文本"的豆包式穿插
                const _syncTextBlock = (aiMsg, fullText) => {
                    if (!aiMsg.blocks) aiMsg.blocks = [];
                    const blocks = aiMsg.blocks;
                    const lastBlock = blocks[blocks.length - 1];
                    // 统计指定下标之前所有 text 块的总长度
                    const prevLenBefore = (endIdx) => {
                        let len = 0;
                        for (let i = 0; i < endIdx; i++) {
                            if (blocks[i].type === 'text') len += blocks[i].content.length;
                        }
                        return len;
                    };
                    if (lastBlock && lastBlock.type === 'text') {
                        // 最后是文本块：用全文减去之前文本块长度，得到当前块增量
                        lastBlock.content = fullText.slice(prevLenBefore(blocks.length - 1)) || '';
                    } else if (fullText) {
                        // 最后是工具/思考块（或空）：新建文本块，只装之后新增的文本
                        const newContent = fullText.slice(prevLenBefore(blocks.length));
                        if (newContent) blocks.push({ type: 'text', content: newContent });
                    }
                };

                // 深度思考块同步：与 _syncTextBlock 对称，把全量 reasoning 按增量
                // 拆成独立 reasoning block 插入 blocks。当最后一个 block 是 text/tool
                // 时新建 reasoning block，是 reasoning 时追加——从而形成
                // "思考-回答-思考-回答"的豆包式交替穿插。
                const _syncReasoningBlock = (aiMsg, fullReasoning) => {
                    if (!aiMsg.blocks) aiMsg.blocks = [];
                    const blocks = aiMsg.blocks;
                    const lastBlock = blocks[blocks.length - 1];
                    const prevReasoningLen = (endIdx) => {
                        let len = 0;
                        for (let i = 0; i < endIdx; i++) {
                            if (blocks[i].type === 'reasoning') len += blocks[i].content.length;
                        }
                        return len;
                    };
                    if (lastBlock && lastBlock.type === 'reasoning') {
                        lastBlock.content = fullReasoning.slice(prevReasoningLen(blocks.length - 1)) || '';
                    } else if (fullReasoning) {
                        const newContent = fullReasoning.slice(prevReasoningLen(blocks.length));
                        if (newContent) blocks.push({ type: 'reasoning', content: newContent, expanded: true });
                    }
                };

                // 流结束后统一收起深度思考块（回答完成后自动隐藏，可手动展开）。
                // 不做"按段落拆段交替"重排：DeepSeek 先全部思考再全部回答，
                // 强拆会把单个思考块拆成多段塞进回答中间，造成思考碎片化堆积底部。
                const _interleaveReasoningAndContent = (aiMsg) => {
                    if (!aiMsg.reasoning || !aiMsg.reasoning.trim()) return;
                    // 统一收起所有深度思考块（回答结束后自动隐藏，可手动展开）
                    aiMsg.blocks.forEach(b => { if (b.type === 'reasoning') b.expanded = false; });
                    // 若 blocks 中尚未有文本块（异常兜底），把完整回答追加为文本块
                    if (!aiMsg.blocks.some(b => b.type === 'text') && aiMsg.content && aiMsg.content.trim()) {
                        aiMsg.blocks.push({ type: 'text', content: aiMsg.content });
                    }
                    return;
                };

                const parseHistory = (history) => {
                    if (!Array.isArray(history)) return [];
                    const result = [];
                    for (const item of history) {
                        // 后端 history 只存 {role, content}，不含 tool_calls 结构。
                        // LangGraph 一次工具调用的消息序列为：
                        //   human → ai(空正文, 仅发起 tool_call) → tool(结果JSON) ×N → ai(最终正文)
                        // 这里必须过滤两类，否则刷新/续接后会把工具原始 JSON 和空中转消息
                        // 渲染成一条条独立"AI 助手"气泡：
                        // ① ToolMessage（role='tool'，工具结果）与 SystemMessage（系统提示词）不展示
                        if (item.role === 'tool' || item.role === 'system') continue;
                        const role = item.role === 'human' ? 'user' : 'assistant';
                        const content = extractContentText(item.content) || '';
                        // ② 无正文的中转 AIMessage（仅挂载 tool_calls、content 为空）没有可展示内容
                        if (role === 'assistant' && !content.trim()) continue;
                        result.push(normalizeMessage({
                            id: generateId(), // 唯一 key，避免数组变更时 DOM 错乱
                            role,
                            content,
                            time: formatTime()
                        }));
                    }
                    return result;
                };

                const saveSessions = () => {
                    cache.set(sessionCacheKey(STORAGE_KEY.SESSIONS), sessions.value);
                };

                const saveCurrentThread = () => {
                    cache.set(sessionCacheKey(STORAGE_KEY.CURRENT_THREAD), currentThreadId.value);
                };

                const saveMessages = () => {
                    if (currentThreadId.value) {
                        cache.setMessages(currentThreadId.value, messages.value);
                    }
                };

                // 滚动合并标记：一次动画帧只执行一次滚动，避免流式高频调用
                // 让浏览器 smooth 动画排队堆积（表现为抽搐/永远追不上/卡顿）
                let _scrollRafPending = false;
                // 接近底部阈值（px）：用户正在上方翻看时，自动跟随不抢滚
                const SCROLL_FOLLOW_THRESHOLD = 120;
                const scrollToBottom = async (opts = {}) => {
                    await nextTick();
                    const el = messagesContainer.value;
                    if (!el) return;
                    const { force = false } = opts;
                    if (!force) {
                        // 智能跟随：仅当用户接近底部时才滚动，避免打断阅读
                        const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
                        if (dist > SCROLL_FOLLOW_THRESHOLD) return;
                    }
                    // rAF 合并：流式渲染 100ms 节流 + 思考/正文/工具多次触发时，
                    // 同一帧只执行一次平滑滚动，目标始终是最新底部
                    if (_scrollRafPending) return;
                    _scrollRafPending = true;
                    requestAnimationFrame(() => {
                        _scrollRafPending = false;
                        const c = messagesContainer.value;
                        if (c) {
                            c.scrollTo({ top: c.scrollHeight, behavior: 'smooth' });
                        }
                    });
                };

                const autoResize = () => {
                    const el = textarea.value;
                    if (!el) return;
                    el.style.height = 'auto';
                    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
                };

                const createNewSession = () => {
                    sessions.value = sessions.value.filter(s => !s.isBlank);
                    const id = generateId();
                    const session = { id, title: '新会话', messages: [], isBlank: true, createdAt: Date.now() };
                    sessions.value.unshift(session);
                    currentThreadId.value = id;
                    messages.value = [];
                    // 新建会话必然未在生成中：解禁发送按钮（生成中新建会话的场景），
                    // 并清除旧会话的工具调用状态残留
                    isLoading.value = false;
                    streaming.value = false;
                    currentToolCall.value = null;
                    stopGenerationResume();
                    saveSessions();
                    saveCurrentThread();
                    cache.setMessages(id, []);
                    refreshQuestions();  // 新建会话时刷新随机提示词
                    closeSidebar();
                    focusInput();
                };

                const switchSession = async (id) => {
                    if (id === currentThreadId.value) return;
                    currentThreadId.value = id;
                    saveCurrentThread();
                    // 切换会话：停止旧会话的续接轮询，并按新会话是否在生成中重置
                    // isLoading（否则 A 会话生成中切到 B，B 的发送按钮被锁死）
                    stopGenerationResume();
                    isLoading.value = _generatingThreads.has(id);
                    currentToolCall.value = null;  // 旧会话的工具调用状态不带到新会话
                    await loadCurrentMessages();
                    closeSidebar();
                    scrollToBottom({ force: true });  // 切换会话：无条件滚到最新消息
                };

                const deleteSession = async (id) => {
                    try {
                        await apiDeleteSession(id);
                    } catch (err) {
                        showToast('删除会话失败: ' + err.message, 'error');
                        return;
                    }
                    sessions.value = sessions.value.filter(s => s.id !== id);
                    cache.removeMessages(id);
                    saveSessions();
                    if (currentThreadId.value === id) {
                        if (sessions.value.length > 0) {
                            await switchSession(sessions.value[0].id);
                        } else {
                            createNewSession();
                        }
                    }
                };


                const sendQuick = (text) => {
                    inputText.value = text;
                    sendMessage();
                };

                const sendMessage = async () => {
                    const content = inputText.value.trim();
                    // 有文件时允许空消息（纯文件发送），无文件时必须输入文字
                    const hasFiles = uploadedFiles.value.length > 0;
                    if ((!content && !hasFiles) || isLoading.value) return;

                    // 用户发起新消息：停止可能存在的"刷新后自动续接"轮询，避免与新生成冲突
                    stopGenerationResume();

                    if (!currentThreadId.value) {
                        createNewSession();
                    }

                    const session = sessions.value.find(s => s.id === currentThreadId.value);

                    // 发送前提取 fileIds 并立即清空附件区（防止重复发送）
                    const pendingFileIds = uploadedFiles.value
                        .map(f => f.file_id)
                        .filter(id => id != null);
                    const pendingFileNames = uploadedFiles.value.map(f => f.name);
                    uploadedFiles.value = [];

                    // 用户消息：文本 + 附件文件名展示
                    const displayContent = content + (pendingFileNames.length > 0
                        ? '\n\n📎 ' + pendingFileNames.map(n => `[${n}]`).join(' ')
                        : '');

                    // 【防重复发送】上一条回复尚未完成、且用户再次发送完全相同的问题时拦截。
                    // 背景：流式生成中刷新/续接期间，AI 占位为空，用户误以为没发出去而反复
                    // 按 Enter，导致后端同一会话累积 N 条相同 human（实测 6 连发、界面 6 个
                    // 重复气泡）。相同内容应等待续接，而不是再追加一轮生成。
                    const lastMsg = messages.value.length > 0 ? messages.value[messages.value.length - 1] : null;
                    const prevUser = messages.value.length > 1 ? messages.value[messages.value.length - 2] : null;
                    const lastAiIncomplete = lastMsg && lastMsg.role === 'assistant'
                        && !(lastMsg.content && lastMsg.content.trim())
                        && !(lastMsg.blocks && lastMsg.blocks.some(b => b.type === 'text' && b.content && b.content.trim()));
                    if (lastAiIncomplete && prevUser && prevUser.role === 'user'
                        && prevUser.content === displayContent) {
                        showToast('该问题回复仍在生成中，请勿重复发送，稍后会自动续接', 'warning');
                        return;
                    }
                    const userMsg = { id: generateId(), role: 'user', content: displayContent, time: formatTime() };
                    messages.value.push(userMsg);

                    if (session && session.title === '新会话') {
                        const titleText = content || (pendingFileNames.length > 0 ? `📎 ${pendingFileNames[0]}` : '新会话');
                        session.title = titleText.length > 20 ? titleText.slice(0, 20) + '...' : titleText;
                    }
                    if (session) {
                        session.isBlank = false;
                        session.lastMessageAt = Date.now();
                    }

                    inputText.value = '';
                    resetTextarea();
                    saveMessages();
                    saveSessions();
                    scrollToBottom();

                    isLoading.value = true;
                    streaming.value = false;

                    // 记录本次发送所属会话：流式回调/结束/finally 都要校验"当前视图仍是该会话"，
                    // 否则生成中切换到其他会话时，旧会话的流式内容会污染新会话界面/缓存（会话错乱）
                    const sendThreadId = currentThreadId.value;
                    _generatingThreads.add(sendThreadId);

                    // 变量必须声明在 try 之外（函数作用域），否则 catch 块访问不到
                    // try 内的 let 变量，导致停止回复时报 renderTimer is not defined
                    let aiMsg = null;
                    let latestText = '';
                    let renderTimer = null;
                    let saveTimer = null;
                    // 标记自上次渲染以来是否有 reasoning 更新——只有思考/工具更新才自动滚底，
                    // 正式回答内容不强制滚动，避免打断用户阅读
                    let dirtyReasoning = false;
                    // 消息唯一 ID：后端幂等去重键（user_id + client_message_id）。
                    // 刷新重试/多标签页重复 POST 同一消息时，后端 SETNX 拦截，不重复生成
                    const clientMessageId = `cm_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;

                    try {
                        // 注意：push 后必须从响应式代理中取回引用。Vue 3 的 proxy 是惰性转换的，
                        // push 进数组的是原始对象，若直接持有它并赋值 content，不会触发响应式
                        // 更新（流式输出卡在"正在思考..."，刷新后从缓存整体赋值才显示）。
                        messages.value.push({ id: generateId(), role: 'assistant', content: '', reasoning: '', tool_calls: [], blocks: [], time: formatTime() });
                        aiMsg = messages.value[messages.value.length - 1];
                        saveMessages();
                        scrollToBottom();

                        // 创建 AbortController 用于停止回复
                        abortController.value = new AbortController();

                        // file_ids 已在发送前提取（pendingFileIds），附件区已清空
                        const answer = await apiChat(content, currentThreadId.value, (text) => {
                            // 生成中切换了会话：不再更新旧会话的 AI 占位（后台线程继续跑完落库，
                            // 切回该会话时 loadCurrentMessages 从后端/缓存恢复完整回复）
                            if (currentThreadId.value !== sendThreadId) return;
                            streaming.value = true;
                            latestText = text;
                            if (!renderTimer) {
                                renderTimer = setTimeout(() => {
                                    renderTimer = null;
                                    const shouldScroll = dirtyReasoning;  // 只有思考更新才滚
                                    dirtyReasoning = false;
                                    aiMsg.content = latestText;
                                    _syncReasoningBlock(aiMsg, aiMsg.reasoning);
                                    _syncTextBlock(aiMsg, latestText);
                                    if (shouldScroll) scrollToBottom();
                                }, 100);
                            }
                            if (!saveTimer) {
                                saveTimer = setTimeout(() => {
                                    saveTimer = null;
                                    saveMessages();
                                }, 500);
                            }
                        }, abortController.value.signal, (toolEvent) => {
                            // 生成中切换了会话：工具事件只属于原会话，不更新当前视图
                            if (currentThreadId.value !== sendThreadId) return;
                            // 工具调用事件：加载界面 + 写入 blocks 实现穿插
                            if (toolEvent.type === 'start') {
                                currentToolCall.value = { name: toolEvent.name, args: toolEvent.args };
                                // 新建工具调用块，插入到当前文本块之后
                                aiMsg.blocks.push({
                                    type: 'tool',
                                    name: toolEvent.name,
                                    args: toolEvent.args || {},
                                    result: '',
                                    status: 'running',
                                    expanded: false,
                                    time: formatTime()
                                });
                                scrollToBottom();  // 工具调用出现时滚底
                            } else if (toolEvent.type === 'end') {
                                currentToolCall.value = null;
                                // 更新最后一个 running 状态的同名工具块
                                for (let i = aiMsg.blocks.length - 1; i >= 0; i--) {
                                    const b = aiMsg.blocks[i];
                                    if (b.type === 'tool' && b.status === 'running' && b.name === toolEvent.name) {
                                        b.status = 'done';
                                        b.result = toolEvent.content || '';
                                        break;
                                    }
                                }
                                scrollToBottom();  // 工具结果返回时滚底
                            }
                        }, pendingFileIds, (reasoningText) => {
                            // 生成中切换了会话：深度思考内容只属于原会话
                            if (currentThreadId.value !== sendThreadId) return;
                            // 深度思考内容：追加到 aiMsg.reasoning（全量保留），
                            // 节流时按增量插入 blocks，与 text 块交替形成穿插效果
                            aiMsg.reasoning += reasoningText;
                            dirtyReasoning = true;  // 思考更新需要自动滚底
                            if (!renderTimer) {
                                renderTimer = setTimeout(() => {
                                    renderTimer = null;
                                    // 生成中切换了会话：不再把旧会话的流式内容刷进当前视图
                                    if (currentThreadId.value !== sendThreadId) return;
                                    const shouldScroll = dirtyReasoning;
                                    dirtyReasoning = false;
                                    aiMsg.content = latestText;
                                    _syncReasoningBlock(aiMsg, aiMsg.reasoning);
                                    _syncTextBlock(aiMsg, latestText);
                                    if (shouldScroll) scrollToBottom();
                                }, 100);
                            }
                        }, thinkingMode.value, reasoningEffort.value, clientMessageId);

                        // 流结束：清掉未触发的节流器，确保最终内容一次性落库渲染。
                        // 若已切换到其他会话，跳过 UI 更新（后台 checkpoint 已提交，
                        // 切回时 loadCurrentMessages 从后端恢复完整回复）
                        if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
                        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
                        if (currentThreadId.value === sendThreadId) {
                            currentToolCall.value = null;  // 清除工具调用状态
                            aiMsg.content = answer || '（无回复）';
                            _syncReasoningBlock(aiMsg, aiMsg.reasoning);
                            _syncTextBlock(aiMsg, answer || '（无回复）');
                            // 流结束后按段落交替重排（DeepSeek 先全部思考再全部回答，需手动穿插）
                            _interleaveReasoningAndContent(aiMsg);  // 内部已把思考块设为收起
                            streaming.value = false;
                            saveMessages();
                            saveSessions();
                            scrollToBottom({ force: true });  // 回复完成：无条件滚到底展示完整回复
                        }
                    } catch (err) {
                        // 用户主动停止回复（AbortController.abort()）
                        if (err.name === 'AbortError') {
                            // 保留已生成的部分内容，不删除 AI 消息
                            if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
                            if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
                            if (aiMsg && currentThreadId.value === sendThreadId) {
                                aiMsg.content = aiMsg.content || '（已停止）';
                            }
                            if (currentThreadId.value === sendThreadId) {
                                streaming.value = false;
                                isLoading.value = false;
                                currentToolCall.value = null;
                                saveMessages();
                                scrollToBottom();
                            }
                            return;
                        }
                        // 幂等拦截：这条消息（client_message_id）后端已处理过
                        //（此前已提交过同一条消息，生成进行中或已完成）。
                        // 回滚本次本地 push 的 userMsg + AI 占位，避免界面叠加重复气泡，
                        // 然后触发事件重放/自动续接，把既有的回复渲染回来。
                        if (err && err.code === 'DUPLICATE') {
                            // 回滚：删除刚 push 的用户消息和 AI 占位（都在消息列表末尾）。
                            // 若已切换到其他会话，不操作当前视图（该会话消息由后端幂等保证）
                            if (currentThreadId.value !== sendThreadId) return;
                            if (aiMsg) {
                                const idx = messages.value.indexOf(aiMsg);
                                if (idx > -1) messages.value.splice(idx, 1);
                            }
                            if (messages.value.length > 0) {
                                const last = messages.value[messages.value.length - 1];
                                if (last && last.role === 'user' && last.content === displayContent) {
                                    messages.value.splice(messages.value.length - 1, 1);
                                }
                            }
                            isLoading.value = false;
                            streaming.value = false;
                            currentToolCall.value = null;
                            saveMessages();
                            showToast('该消息已发送过，正在恢复原回复', 'info');
                            // 该会话当前展示的就是这条消息的回复（可能仍在后台生成）：
                            // 刷新加载流程会重放事件流并自动续接，这里直接触发
                            const tid = currentThreadId.value;
                            await loadCurrentMessages();
                            return;
                        }                        // 非主动停止（网络中断/页面刷新/服务端异常）：不再 pop 掉 AI 消息。
                        // 此前 pop + saveMessages 会把本地缓存里的 AI 回复删掉，刷新后只剩
                        // 用户消息——"刷新后会话内容清空"的根因之一。现在保留 AI 占位消息，
                        // 后端已改为断连不中断生成（后台线程跑完图提交 checkpoint），
                        // 刷新后 loadCurrentMessages 会用后端 history 兜底补全完整回复。
                        if (aiMsg && currentThreadId.value === sendThreadId) {
                            // 【续接标记】标记该消息为"未完成的中断回复"，
                            // 自动续接的检测条件识别 interrupted 标记（而非只看 content 是否为空）。
                            aiMsg.interrupted = true;
                            // 【关键修复】旧逻辑在 catch 里无条件执行：
                            //   aiMsg.content = '（回复中断，请重新生成）'
                            //   _syncTextBlock(...)   → 把流式中已生成的正文块覆盖成占位文本
                            //   _syncReasoningBlock(...) → 若最后块不是 reasoning，会在占位块之后
                            //      追加"后半段思考"，形成【深度思考块-占位文本-深度思考块】双块错乱。
                            // 现在区分两种情况：
                            // ① 已有正文：保留正文与 blocks（流式穿插状态本就正确），只标记中断；
                            if (aiMsg.content && aiMsg.content.trim()) {
                                // blocks 保持原样，不做任何覆盖/追加
                            } else {
                                // ② 完全没有正文：写占位文本并清空 blocks，
                                //    让占位通过模板 v-else-if="msg.content" 单独渲染，
                                //    不残留半截 reasoning 块造成困惑
                                aiMsg.content = '（回复中断，请重新生成）';
                                aiMsg.blocks = [];
                            }
                        }
                        saveMessages();
                        // 发送失败：恢复附件列表，让用户可以重试
                        if (pendingFileIds.length > 0) {
                            uploadedFiles.value = pendingFileIds.map((id, i) => ({
                                name: pendingFileNames[i] || `file_${id}`,
                                size: 0,
                                file_id: id,
                            }));
                        }
                        if (err && err.status === 403) {
                            // 会话被判定为他人所有：从列表移除并新建会话，不再复用该 thread。
                            // 移除的是"发送时的会话"（可能已切换视图，不能误删当前会话）
                            const removed = sendThreadId;
                            sessions.value = sessions.value.filter(s => s.id !== removed);
                            cache.removeMessages(removed);
                            saveSessions();
                            if (currentThreadId.value === removed) {
                                createNewSession();
                            }
                            showToast('该会话不属于当前账号，已切换新会话', 'error');
                            return;
                        }
                        showToast('发送消息失败: ' + err.message, 'error');
                    } finally {
                        // 生成结束（无论成功/失败/停止）：从生成集合移除该会话。
                        // isLoading 只在当前视图仍是该会话时复位；若已切走，保持新会话
                        // 自己的生成状态（switchSession 已按 _generatingThreads 重算）
                        _generatingThreads.delete(sendThreadId);
                        if (currentThreadId.value === sendThreadId) {
                            isLoading.value = false;
                            streaming.value = false;
                            currentToolCall.value = null;  // 确保异常时也清除工具调用状态
                            focusInput();
                        }
                    }
                };

                const handleKeydown = (e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        sendMessage();
                    }
                };

                const resetTextarea = () => {
                    const el = textarea.value;
                    if (el) {
                        el.style.height = 'auto';
                    }
                };

                const focusInput = () => {
                    setTimeout(() => textarea.value?.focus(), 50);
                };

                const openSidebar = () => { sidebarOpen.value = true; };
                const closeSidebar = () => { sidebarOpen.value = false; };

                const logout = async () => {
                    // 先调用后端登出接口，删除 Redis 中的 access + refresh token，实现即时失效
                    // 无状态 JWT 本身无法作废，通过 Redis 白名单删除实现登出即失效
                    try {
                        await fetch(`${API_BASE}/api/logout`, {
                            method: 'POST',
                            headers: authHeaders(),
                        });
                    } catch (e) {
                        // 网络错误不阻塞本地登出，用户仍需回到登录页
                    }
                    // 会话缓存按 userId 隔离（sessionCacheKey），登出无需清理：
                    // 同一账号重新登录后仍能恢复自己的会话列表，不同账号之间天然隔离
                    cache.remove(STORAGE_KEY.USER);
                    router.push('/api/login');
                };

                const checkHealth = async () => {
                    const result = await apiHealthCheck();
                    if (result.status === 'ok' && result.db) {
                        healthStatus.value = 'online';
                    } else {
                        healthStatus.value = 'degraded';
                    }
                };

                let healthTimer = null;

                onMounted(async () => {
                    updateLastActive();
                    refreshQuestions();
                    // 【会话后端恢复】localStorage 会话列表为空时（刷新/换浏览器/清缓存/
                    // 7 天 TTL 过期），从后端 checkpoint 按 user_id 恢复，避免误判为
                    // "会话丢失"而直接新建对话
                    if ((!sessions.value || sessions.value.length === 0) && user.value && user.value.userId) {
                        try {
                            const remote = await apiListSessions();
                            if (remote && remote.length > 0) {
                                sessions.value = remote.map(s => ({
                                    id: s.thread_id,
                                    title: s.title || '新会话',
                                    messages: [],
                                    isBlank: false,
                                    createdAt: s.last_updated ? new Date(s.last_updated).getTime() : Date.now()
                                }));
                                saveSessions();
                            }
                        } catch (e) {
                            // 网络异常/401 已由 handleAuthError 处理，这里静默降级到本地缓存逻辑
                            console.warn('从后端恢复会话列表失败:', e.message);
                        }
                    }
                    if (!currentThreadId.value || !sessions.value.find(s => s.id === currentThreadId.value)) {
                        if (sessions.value.length > 0) {
                            currentThreadId.value = sessions.value[0].id;
                            saveCurrentThread();
                        } else {
                            createNewSession();
                        }
                    }
                    await loadCurrentMessages();
                    scrollToBottom();
                    focusInput();
                    checkHealth();
                    healthTimer = setInterval(checkHealth, 30000);
                });

                // ===== 新增：用户菜单 / 弹窗 / 上传 / 主题 方法 =====

                function toggleUserMenu() {
                    userMenuOpen.value = !userMenuOpen.value;
                    if (userMenuOpen.value) uploadMenuOpen.value = false;
                }

                async function openProfileModal() {
                    userMenuOpen.value = false;
                    // 加载当前用户信息
                    profileForm.value.username = user.value?.name || '';
                    profileForm.value.avatar = user.value?.avatar || '';
                    profileForm.value.old_password = '';
                    profileForm.value.new_password = '';
                    profileForm.value.confirm_password = '';
                    profileModalOpen.value = true;
                    // 从 profile 接口回显全局 system prompt（合并后统一走 /profile）
                    try {
                        const p = await apiGetProfile(user.value.userId);
                        profileForm.value.system_prompt = (p && p.system_prompt) || '';
                    } catch (e) {
                        profileForm.value.system_prompt = '';
                    }
                }

                function closeProfileModal() {
                    profileModalOpen.value = false;
                }

                async function openSettingsModal() {
                    userMenuOpen.value = false;
                    // 加载当前设置
                    settingsForm.value.theme = cache.get('theme') || 'default';
                    settingsModalOpen.value = true;

                    // 从后端全局接口加载 MCP 配置（本地文件存储）
                    try {
                        const res = await fetch(`${API_BASE}/api/mcp/config`, {
                            headers: authHeaders({}),
                        });
                        syncTokenFromHeaders(res.headers);
                        if (res.ok) {
                            const data = await res.json();
                            if (data.ok && data.data) {
                                mcpConfigPath.value = data.data.path || '';
                                settingsForm.value.mcp_servers = data.data.mcp_servers || [];
                                try {
                                    mcpJsonText.value = JSON.stringify(data.data.mcp_servers || [], null, 2);
                                    mcpJsonError.value = '';
                                } catch (e) {
                                    mcpJsonText.value = '[]';
                                }
                            }
                        }
                    } catch (err) {
                        // 接口失败时回退到本地缓存
                        settingsForm.value.mcp_servers = cache.get('mcp_servers') || [];
                        mcpJsonText.value = JSON.stringify(settingsForm.value.mcp_servers, null, 2);
                        console.warn('加载 MCP 配置失败，使用本地缓存:', err);
                    }

                    // 管理员打开设置时加载知识库文档列表
                    if (user.value?.role === 'admin') {
                        loadKnowledgeDocs();
                    }
                }

                function closeSettingsModal() {
                    settingsModalOpen.value = false;
                }

                async function saveProfile() {
                    profileSaving.value = true;
                    try {
                        // 统一更新个人信息 + 全局 system prompt（一个接口）
                        const res = await fetch(`${API_BASE}/api/users/${user.value.userId}/profile`, {
                            method: 'PUT',
                            headers: authHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({
                                username: profileForm.value.username,
                                avatar: profileForm.value.avatar,
                                system_prompt: profileForm.value.system_prompt || '',
                            }),
                        });
                        syncTokenFromHeaders(res.headers);
                        if (res.ok) {
                            // 更新本地用户信息
                            user.value.name = profileForm.value.username;
                            user.value.avatar = profileForm.value.avatar;
                            // 同步刷新 AI 称呼缓存（greetingName 优先读它，否则主页仍显示旧名字）
                            cache.set(STORAGE_KEY.AI_CALL_NAME, profileForm.value.username.trim());
                            cache.set(STORAGE_KEY.USER, user.value);
                            showToast('个人信息已更新', 'success');
                        } else {
                            const pd = await res.json().catch(() => ({}));
                            showToast(pd.detail || '保存失败', 'error');
                        }

                        // 修改密码（如果填写了）
                        if (profileForm.value.new_password) {
                            if (profileForm.value.new_password !== profileForm.value.confirm_password) {
                                showToast('两次输入的新密码不一致', 'error');
                                return;
                            }
                            if (profileForm.value.new_password.length < 6) {
                                showToast('新密码至少6位', 'error');
                                return;
                            }
                            const pwdRes = await fetch(`${API_BASE}/api/users/${user.value.userId}/password`, {
                                method: 'PUT',
                                headers: authHeaders({ 'Content-Type': 'application/json' }),
                                body: JSON.stringify({
                                    old_password: profileForm.value.old_password,
                                    new_password: profileForm.value.new_password,
                                }),
                            });
                            if (pwdRes.ok) {
                                showToast('密码已更新，请重新登录', 'success');
                                setTimeout(() => logout(), 1500);
                            } else {
                                const data = await pwdRes.json().catch(() => ({}));
                                showToast(data.detail || '密码修改失败', 'error');
                            }
                        }
                        closeProfileModal();
                    } catch (err) {
                        showToast('保存失败：' + err.message, 'error');
                    } finally {
                        profileSaving.value = false;
                    }
                }

                function handleAvatarUpload(event) {
                    const file = event.target.files[0];
                    if (!file) return;
                    if (!file.type.startsWith('image/')) {
                        showToast('请选择图片文件', 'error');
                        return;
                    }
                    if (file.size > 2 * 1024 * 1024) {
                        showToast('头像不能超过 2MB', 'error');
                        return;
                    }
                    const reader = new FileReader();
                    reader.onload = (e) => {
                        profileForm.value.avatar = e.target.result;
                    };
                    reader.readAsDataURL(file);
                }

                function applyTheme(theme) {
                    document.documentElement.setAttribute('data-theme', theme);
                    cache.set('theme', theme);
                }

                async function saveSettings() {
                    settingsSaving.value = true;
                    try {
                        // 解析 MCP JSON 配置
                        let parsedMcp = [];
                        const mcpText = mcpJsonText.value.trim();
                        if (mcpText) {
                            try {
                                parsedMcp = JSON.parse(mcpText);
                                if (!Array.isArray(parsedMcp)) {
                                    mcpJsonError.value = 'MCP 配置必须是 JSON 数组（[] 包裹）';
                                    showToast('MCP 配置格式错误：必须是 JSON 数组', 'error');
                                    return;
                                }
                                // 校验每项必须是对象且有 command 或 url
                                for (let i = 0; i < parsedMcp.length; i++) {
                                    const item = parsedMcp[i];
                                    if (typeof item !== 'object' || item === null) {
                                        mcpJsonError.value = `第 ${i + 1} 项必须是对象`;
                                        showToast(`MCP 配置第 ${i + 1} 项必须是对象`, 'error');
                                        return;
                                    }
                                    const type = item.type || 'stdio';
                                    if (type === 'stdio' && !item.command) {
                                        mcpJsonError.value = `第 ${i + 1} 项（stdio 类型）缺少 command 字段`;
                                        showToast(`MCP 配置第 ${i + 1} 项缺少 command`, 'error');
                                        return;
                                    }
                                    if (type === 'sse' && !item.url) {
                                        mcpJsonError.value = `第 ${i + 1} 项（sse 类型）缺少 url 字段`;
                                        showToast(`MCP 配置第 ${i + 1} 项缺少 url`, 'error');
                                        return;
                                    }
                                }
                                mcpJsonError.value = '';
                            } catch (e) {
                                mcpJsonError.value = 'JSON 解析失败：' + e.message;
                                showToast('MCP 配置 JSON 解析失败：' + e.message, 'error');
                                return;
                            }
                        }

                        // 保存前记录原始 MCP 配置，用于判断是否需要重启提示
                        const originalMcp = JSON.stringify(cache.get('mcp_servers') || []);
                        const newMcp = JSON.stringify(parsedMcp);
                        const mcpChanged = originalMcp !== newMcp;

                        // 保存主题到后端
                        await fetch(`${API_BASE}/api/users/${user.value.userId}/theme`, {
                            method: 'PUT',
                            headers: authHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({ theme: settingsForm.value.theme }),
                        });
                        // 保存 MCP 配置到后端（全局接口，本地文件存储）
                        const mcpRes = await fetch(`${API_BASE}/api/mcp/config`, {
                            method: 'PUT',
                            headers: authHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({
                                path: mcpConfigPath.value.trim() || undefined,
                                mcp_servers: parsedMcp,
                            }),
                        });
                        syncTokenFromHeaders(mcpRes.headers);
                        if (!mcpRes.ok) {
                            const errData = await mcpRes.json().catch(() => ({}));
                            throw new Error(errData.detail || errData.message || 'MCP 配置保存失败');
                        }
                        // 本地缓存
                        cache.set('theme', settingsForm.value.theme);
                        cache.set('mcp_servers', parsedMcp);
                        settingsForm.value.mcp_servers = parsedMcp;
                        applyTheme(settingsForm.value.theme);

                        // MCP 配置变更：调用后端重载接口立即生效（清除图缓存+关闭旧连接），
                        // 不再弹"需重启后端"提示——后端 _get_user_graph 已通过 hash 检测自动热重载
                        if (mcpChanged) {
                            try {
                                const reloadRes = await fetch(`${API_BASE}/api/mcp/reload`, {
                                    method: 'POST',
                                    headers: authHeaders(),
                                });
                                syncTokenFromHeaders(reloadRes.headers);
                                // 重载接口失败不影响：hash 检测仍会在下次对话自动重建
                            } catch (e) {}
                            showToast('MCP 配置已保存并生效', 'success');
                            closeSettingsModal();
                        } else {
                            // 仅主题变更：即时生效，直接提示成功
                            showToast('设置已保存', 'success');
                            closeSettingsModal();
                        }
                    } catch (err) {
                        showToast('保存失败：' + err.message, 'error');
                    } finally {
                        settingsSaving.value = false;
                    }
                }

                function formatMcpJson() {
                    try {
                        const text = mcpJsonText.value.trim();
                        if (!text) {
                            mcpJsonText.value = '[]';
                            mcpJsonError.value = '';
                            return;
                        }
                        const parsed = JSON.parse(text);
                        mcpJsonText.value = JSON.stringify(parsed, null, 2);
                        mcpJsonError.value = '';
                        showToast('JSON 格式化成功', 'success');
                    } catch (e) {
                        mcpJsonError.value = 'JSON 解析失败：' + e.message;
                        showToast('JSON 格式化失败：' + e.message, 'error');
                    }
                }

                function clearMcpJson() {
                    mcpJsonText.value = '[]';
                    mcpJsonError.value = '';
                }

                function confirmRestartNotice() {
                    restartNoticeOpen.value = false;
                    closeSettingsModal();
                    showToast('设置已保存，重启后生效', 'success');
                }

                function toggleUploadMenu() {
                    uploadMenuOpen.value = !uploadMenuOpen.value;
                    if (uploadMenuOpen.value) userMenuOpen.value = false;
                }

                async function handleFileUpload(event) {
                    const files = event.target.files;
                    if (!files || files.length === 0) return;
                    uploadMenuOpen.value = false;

                    for (const file of files) {
                        if (file.size > 10 * 1024 * 1024) {
                            showToast(`文件 ${file.name} 超过 10MB，已跳过`, 'error');
                            continue;
                        }
                        try {
                            const formData = new FormData();
                            formData.append('file', file);
                            if (currentThreadId.value) {
                                formData.append('thread_id', currentThreadId.value);
                            }
                            const res = await fetch(`${API_BASE}/api/chat/upload`, {
                                method: 'POST',
                                headers: authHeaders({}),
                                body: formData,
                            });
                            if (res.ok) {
                                const data = await res.json();
                                uploadedFiles.value.push({
                                    name: file.name,
                                    size: file.size,
                                    file_id: data.data?.file_id,
                                });
                                showToast(`文件 ${file.name} 上传成功`, 'success');
                            } else {
                                const data = await res.json().catch(() => ({}));
                                showToast(data.detail || `文件 ${file.name} 上传失败`, 'error');
                            }
                        } catch (err) {
                            showToast(`文件 ${file.name} 上传失败：${err.message}`, 'error');
                        }
                    }
                    // 清空 input，允许重复上传同一文件
                    if (fileInput.value) fileInput.value.value = '';
                }

                function removeUploadedFile(index) {
                    uploadedFiles.value.splice(index, 1);
                }

                function stopResponse() {
                    if (abortController.value) {
                        abortController.value.abort();
                        abortController.value = null;
                    }
                    isLoading.value = false;
                    streaming.value = false;
                    showToast('已停止回复', 'info');
                }

                // 点击外部关闭菜单
                document.addEventListener('click', (e) => {
                    if (!e.target.closest('.user-menu-wrapper')) {
                        userMenuOpen.value = false;
                        uploadMenuOpen.value = false;
                    }
                });

                // 初始化主题
                const savedTheme = cache.get('theme') || 'default';
                if (savedTheme !== 'default') {
                    applyTheme(savedTheme);
                }

                onUnmounted(() => {
                    if (healthTimer) clearInterval(healthTimer);
                });

                return {
                    user, sessions, currentThreadId, messages, inputText,
                    isLoading, streaming, currentToolCall, sidebarOpen, healthStatus, healthText,
                    messagesContainer, textarea, fileInput, randomQuestions, refreshQuestions,
                    currentSessionTitle, userAvatar, greetingName, canSend,
                    createNewSession, switchSession, deleteSession,
                    sendMessage, sendQuick,
                    handleKeydown, autoResize, openSidebar, closeSidebar,
                    logout, escapeHtml, renderMarkdown, toolSummary, toolIcon, copyCodeBlock,
                    // 新增
                    userMenuOpen, uploadMenuOpen, profileModalOpen, settingsModalOpen,
                    profileForm, settingsForm, profileSaving, settingsSaving,
                    uploadedFiles, themes,
                    toggleUserMenu, openProfileModal, closeProfileModal,
                    openSettingsModal, closeSettingsModal, saveProfile,
                    handleAvatarUpload, saveSettings, applyTheme,
                    toggleUploadMenu, handleFileUpload, removeUploadedFile,
                    stopResponse, restartNoticeOpen, confirmRestartNotice,
                    mcpJsonText, mcpJsonError, formatMcpJson, clearMcpJson, mcpConfigPath,
                    // 会话重命名
                    editingSessionId, renameInput, startRenameSession, saveSessionTitle, cancelRenameSession,
                    // 深度思考
                    thinkingMode, reasoningEffort, thinkingPanelOpen,
                    toggleThinkingMode, setEffort, toggleEffortPanel,
                    // 消息操作
                    copyMessage, shareMessage, regenerateMessage,
                    // 知识库管理（仅管理员）
                    isAdmin, kbDocs, kbTotalChunks, kbLoading, kbUploading, kbUploadInput,
                    loadKnowledgeDocs, uploadKnowledgeFile, deleteKnowledgeSource,
                };
            }
        };

        // ============================================================
        // Toast 提示（全局）
        // ============================================================
        const toastState = Vue.reactive({ message: '', type: 'info', show: false });
        let toastTimer = null;

        function showToast(message, type = 'info') {
            toastState.message = message;
            toastState.type = type;
            toastState.show = true;
            if (toastTimer) clearTimeout(toastTimer);
            toastTimer = setTimeout(() => {
                toastState.show = false;
            }, 3000);
        }

        // ============================================================
        // 路由与根组件
        // ============================================================
        const routes = [
            {
                path: '/api',
                component: AuthLayout,
                children: [
                    { path: 'login', component: LoginForm },
                    { path: 'register', component: RegisterForm },
                    { path: 'recover', component: RecoverForm }
                ]
            },
            { path: '/chat', component: ChatApp },
            { path: '/', redirect: '/chat' }
        ];

        const router = createRouter({
            history: createWebHistory(),
            routes
        });

        router.beforeEach((to, from, next) => {
            const isLoggedIn = !!cache.get(STORAGE_KEY.USER);
            const isAuthRoute = to.path.startsWith('/api');
            if (!isLoggedIn && !isAuthRoute) {
                next('/api/login');
            } else if (isLoggedIn && isAuthRoute) {
                next('/chat');
            } else {
                next();
            }
        });

        const App = {
            template: `
                <div class="app-root">
                    <router-view v-slot="{ Component }">
                        <!-- v-if 判空：路由解析完成前 Component 为 undefined，避免渲染异常节点 -->
                        <component v-if="Component" :is="Component" />
                    </router-view>
                    <div class="toast" :class="[toast.type, { show: toast.show }]">{{ toast.message }}</div>
                </div>
            `,
            setup() {
                return { toast: toastState };
            }
        };

            const app = createApp(App);
            // 全局错误捕获：任何渲染/运行时错误都会提示而不是静默白屏，便于定位问题
            app.config.errorHandler = (err, instance, info) => {
                console.error('[Vue error]', info, err);
                showToast('页面渲染出错: ' + (err && err.message ? err.message : err), 'error');
            };
            app.use(router).mount('#app');
        })(); // end boot
