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
                                '<div style="display:flex;min-height:100vh;align-items:center;justify-content:center;font-family:sans-serif;background:#F7F5F0;">',
                                '<div style="text-align:center;padding:40px;max-width:440px;">',
                                '<h2 style="margin-bottom:12px;color:#1A1A19;">页面加载失败</h2>',
                                '<p style="color:#6E6C66;line-height:1.7;font-size:14px;">',
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

        // 获取用户个人信息（含 avatar、assistant_style、theme）
        async function apiGetProfile(userId) {
            const response = await fetch(`${API_BASE}/api/users/${userId}/profile`, {
                headers: authHeaders()
            });
            syncTokenFromHeaders(response.headers);
            handleAuthError(response);
            if (!response.ok) return null;
            const { ok, data } = await parseApiResponse(response);
            return ok ? data : null;
        }

        async function apiChat(query, threadId, onStream, signal, onToolCall, fileIds) {
            const body = { query, thread_id: threadId };
            if (fileIds && fileIds.length > 0) {
                body.file_ids = fileIds;
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
            if (!response.ok) throw new Error(`请求失败: ${response.status}`);

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
                    // 工具调用开始事件：通知前端显示加载界面
                    if (chunk.tool_call_start && onToolCall) {
                        onToolCall({ type: 'start', name: chunk.tool_call_start.name, args: chunk.tool_call_start.args });
                    }
                    // 工具调用结束事件：通知前端隐藏加载界面
                    if (chunk.tool_call_end && onToolCall) {
                        onToolCall({ type: 'end', name: chunk.tool_call_end.name, content: chunk.tool_call_end.content });
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
            if (!response.ok) throw new Error(`获取历史失败: ${response.status}`);
            return response.json();
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
            if (!response.ok) throw new Error(`删除失败: ${response.status}`);
            return response.json();
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
            return { ok: false, message: message || `请求失败(${res.status})` };
        }

        // ============================================================
        // 认证相关组件
        // ============================================================
        const LoginForm = {
            template: `
                <div class="auth-form-wrapper">
                    <div class="auth-card">
                        <h1>欢迎回来</h1>
                        <p class="auth-subtitle">登录后继续与 Mitta 智能助理对话</p>
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
                                <span v-else>登录</span>
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
                    isSubmitting: false
                };
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
                                <span v-else>注册</span>
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
                        this.formMsg = message || (ok ? '注册成功' : '注册失败');
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
                    <div class="auth-card">
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
                                <span v-else>重置密码</span>
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

        const AuthLayout = {
            template: `
                <div class="auth-layout">
                    <div class="auth-brand">
                        <div class="auth-logo">
                            <div class="auth-logo-mark"><img src="/favicon.png" alt=""></div>
                            <span>Mitta AI</span>
                        </div>
                        <div class="auth-quote">
                            <h2>Mitta，你的元气智能助理 (๑•̀ㅂ•́)و✧</h2>
                            <p>我是 Mitta，一名元气 AI 助理呀～基于知识库为你提供准确客观的信息，可爱只是糖衣，内核是绝对可靠的知识管家呢 (｡•̀ᴗ-)✧</p>
                        </div>
                        <div class="auth-footer">© 2026 Mitta AI. All rights reserved.</div>
                    </div>
                    <router-view v-slot="{ Component }">
                        <!-- 不用 out-in 过渡：mode="out-in" 切换时计算 anchor 会触发 nextSibling 空指针崩溃 -->
                        <component v-if="Component" :is="Component" />
                    </router-view>
                </div>
            `
        };

        // ============================================================
        // 聊天应用组件
        // ============================================================
        const ChatApp = {
            template: `
                <div class="chat-layout">
                    <aside class="sidebar" :class="{ open: sidebarOpen }" role="navigation" aria-label="会话列表">
                        <div class="sidebar-header">
                            <div class="sidebar-brand">
                                <div class="sidebar-brand-mark"><img src="/favicon.png" alt=""></div>
                                <span>Mitta AI</span>
                            </div>
                            <button class="new-chat-btn" @click="createNewSession" title="新建会话" aria-label="新建会话">
                                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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

                    <main class="main-area">
                        <header class="main-header">
                            <div class="header-left">
                                <button class="menu-toggle" @click="openSidebar" aria-label="展开菜单">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                                        <line x1="3" y1="6" x2="21" y2="6"></line>
                                        <line x1="3" y1="12" x2="21" y2="12"></line>
                                        <line x1="3" y1="18" x2="21" y2="18"></line>
                                    </svg>
                                </button>
                                <h2 class="current-session-title">{{ currentSessionTitle }}</h2>
                            </div>
                            <div class="header-actions">
                                <button class="icon-btn" @click="clearCurrentChat" title="清空当前会话" aria-label="清空当前会话">
                                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="3 6 5 6 21 6"></polyline>
                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                    </svg>
                                </button>
                            </div>
                        </header>

                        <div class="messages-container" ref="messagesContainer">
                            <div class="messages-wrapper">
                                <div v-if="messages.length === 0" class="welcome-state">
                                    <div class="welcome-badge">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <circle cx="12" cy="12" r="10"></circle>
                                            <line x1="12" y1="16" x2="12" y2="12"></line>
                                            <line x1="12" y1="8" x2="12.01" y2="8"></line>
                                        </svg>
                                        智能助理已就绪
                                    </div>
                                    <h1>您好，{{ greetingName }}！有什么可以帮您的？</h1>
                                    <p>开始一段新的对话，或从左侧选择一个历史会话继续交流。</p>
                                    <div class="quick-actions">
                                        <button v-for="q in randomQuestions" :key="q" class="quick-action" @click="sendQuick(q)">{{ q }}</button>
                                    </div>
                                    <button class="refresh-questions-btn" @click="refreshQuestions()" title="换一批提示词">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                                        换一批
                                    </button>
                                </div>

                                <div
                                    v-for="(msg, index) in messages"
                                    :key="msg.id || index"
                                    class="message"
                                    :class="msg.role"
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
                                        <div class="message-content">
                                            <!-- v-show 双节点常驻：切换只改 display，不触发挂载/卸载，
                                                规避 v-if/v-else 静态块提升 + insertStaticContent 的
                                                nextSibling 空指针崩溃（Vue 3.5 流式切换白屏问题） -->
                                            <div v-show="msg.content" v-html="escapeHtml(msg.content)"></div>
                                            <div v-show="!msg.content" class="thinking-indicator">
                                                <div class="thinking-dots"><span></span><span></span><span></span></div>
                                                <span class="thinking-text">正在思考...</span>
                                            </div>
                                            <!-- 工具调用加载界面：当模型正在调用工具时显示 -->
                                            <div v-if="currentToolCall && msg === messages[messages.length - 1]" class="tool-call-indicator">
                                                <div class="tool-call-spinner"></div>
                                                <span class="tool-call-text">正在调用工具：<strong>{{ currentToolCall.name }}</strong></span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="input-area">
                            <!-- 已上传文件列表（输入框上方，卡片式，从左到右排列） -->
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
                                    <!-- + 号上传按钮 -->
                                    <div class="user-menu-wrapper" style="position: relative;">
                                        <button v-if="uploadMenuOpen" class="upload-dropdown" @click.stop>
                                            <label class="upload-dropdown-item" style="cursor: pointer;">
                                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
                                                上传文件
                                                <input type="file" ref="fileInput" @change="handleFileUpload" style="display: none;" multiple accept=".txt,.md,.csv,.json,.xml,.html,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.png,.jpg,.jpeg,.gif,.webp,.py,.js,.ts,.java,.zip,.rar,.7z">
                                            </label>
                                        </button>
                                        <button class="upload-btn" @click="toggleUploadMenu" title="上传文件" aria-label="上传文件">
                                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                                        </button>
                                    </div>
                                    <!-- 发送按钮 / 暂停按钮 -->
                                    <button v-if="!isLoading" class="send-btn" @click="sendMessage" :disabled="!canSend" aria-label="发送消息">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <line x1="22" y1="2" x2="11" y2="13"></line>
                                            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                                        </svg>
                                    </button>
                                    <button v-else class="stop-btn" @click="stopResponse" title="停止回复" aria-label="停止回复">
                                        <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>
                                    </button>
                                </div>
                            </div>
                            <p class="input-hint">按 Enter 发送，Shift + Enter 换行</p>
                        </div>
                    </main>

                    <!-- ===== 个人信息弹窗 ===== -->
                    <div v-if="profileModalOpen" class="modal-overlay" @click.self="closeProfileModal">
                        <div class="modal" @mousedown.stop>
                            <div class="modal-header">
                                <h3>个人信息</h3>
                                <button class="modal-close" @click="closeProfileModal" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <!-- 头像设置 -->
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
                                <!-- 用户名 -->
                                <div class="form-group">
                                    <label>用户名</label>
                                    <input v-model="profileForm.username" type="text" placeholder="请输入用户名" maxlength="32">
                                </div>
                                <!-- 助手风格 / 自定义设定 -->
                                <div class="form-group">
                                    <label>助手风格 / 自定义设定</label>
                                    <textarea v-model="profileForm.assistant_style" placeholder="描述你希望 AI 助手具备的风格、角色设定或特殊要求（如：你是一个专业的编程助手，回答简洁，多用代码示例）" rows="3" maxlength="500"></textarea>
                                    <div class="form-hint">此设定会作为 system prompt 的一部分，影响 AI 的回答风格（最多 500 字）</div>
                                </div>
                                <hr style="border: none; border-top: 1px solid var(--border); margin: 20px 0;">
                                <!-- 修改密码 -->
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
                                <button class="btn-primary" @click="saveProfile" :disabled="profileSaving">
                                    {{ profileSaving ? '保存中...' : '保存修改' }}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- ===== 系统设置弹窗 ===== -->
                    <div v-if="settingsModalOpen" class="modal-overlay" @click.self="closeSettingsModal">
                        <div class="modal" @mousedown.stop>
                            <div class="modal-header">
                                <h3>系统设置</h3>
                                <button class="modal-close" @click="closeSettingsModal" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <!-- 主题选择 -->
                                <div class="form-group">
                                    <label>主题配色</label>
                                    <div class="theme-grid">
                                        <div v-for="t in themes" :key="t.value" class="theme-option" :class="{ active: settingsForm.theme === t.value }" @click="settingsForm.theme = t.value; applyTheme(t.value)">
                                            <div class="theme-preview" :style="{ background: t.preview }"></div>
                                            <div class="theme-name">{{ t.name }}</div>
                                        </div>
                                    </div>
                                </div>
                                <hr style="border: none; border-top: 1px solid var(--border); margin: 20px 0;">
                                <!-- MCP 服务器配置 -->
                                <div class="form-group">
                                    <label>MCP 服务器配置</label>
                                    <div class="form-hint" style="margin-bottom: 10px;">配置存储在本地 JSON 文件中，可自定义存储路径。直接粘贴 JSON 数组，每项支持 name / command / args / cwd / type(stdio|sse) / url 等字段。</div>
                                    <!-- 配置文件路径 -->
                                    <div style="margin-bottom: 12px;">
                                        <label style="font-size: 13px; color: var(--text-secondary); display: block; margin-bottom: 6px;">配置文件路径</label>
                                        <input
                                            v-model="mcpConfigPath"
                                            type="text"
                                            placeholder="如：E:/工作文件/AgentProject/resources/config/mcp_servers.json"
                                            style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-family: 'Consolas', monospace; background: var(--bg-secondary); color: var(--text-primary);"
                                        >
                                        <div style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">允许路径：项目 resources/、config/ 目录，或用户主目录下任意路径</div>
                                    </div>
                                    <!-- JSON 配置编辑器 -->
                                    <textarea
                                        v-model="mcpJsonText"
                                        class="mcp-json-editor"
                                        placeholder='[&#10;  {&#10;    "name": "文件系统",&#10;    "command": "npx",&#10;    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"]&#10;  }&#10;]'
                                        rows="10"
                                        spellcheck="false"
                                        style="width: 100%; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-secondary); color: var(--text-primary); resize: vertical; line-height: 1.5;"
                                    ></textarea>
                                    <div style="display: flex; gap: 8px; margin-top: 8px;">
                                        <button type="button" class="btn-ghost" @click="formatMcpJson" style="flex: 1;">格式化 JSON</button>
                                        <button type="button" class="btn-ghost" @click="clearMcpJson" style="flex: 1;">清空</button>
                                    </div>
                                    <div v-if="mcpJsonError" style="color: var(--error, #ff4d4f); font-size: 12px; margin-top: 6px;">{{ mcpJsonError }}</div>
                                </div>
                            </div>
                            <div class="modal-footer">
                                <button class="btn-ghost" @click="closeSettingsModal">取消</button>
                                <button class="btn-primary" @click="saveSettings" :disabled="settingsSaving">
                                    {{ settingsSaving ? '保存中...' : '保存设置' }}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- ===== MCP 重启提示弹窗 ===== -->
                    <div v-if="restartNoticeOpen" class="modal-overlay" @click.self="restartNoticeOpen = false">
                        <div class="modal" @mousedown.stop style="max-width: 480px;">
                            <div class="modal-header">
                                <h3>配置已保存</h3>
                                <button class="modal-close" @click="restartNoticeOpen = false" aria-label="关闭">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                                </button>
                            </div>
                            <div class="modal-body">
                                <div style="display: flex; gap: 12px; align-items: flex-start;">
                                    <div style="flex-shrink: 0; width: 40px; height: 40px; border-radius: 50%; background: var(--warning-bg, #FFF4E5); display: flex; align-items: center; justify-content: center; font-size: 20px;">⚠️</div>
                                    <div>
                                        <p style="margin: 0 0 10px 0; font-weight: 600;">MCP 配置需重启后端生效</p>
                                        <p style="margin: 0 0 8px 0; color: var(--text-secondary); line-height: 1.6;">MCP 服务器在后端服务启动时初始化并编译进对话图，运行中修改配置不会自动热重载。</p>
                                        <p style="margin: 0; color: var(--text-secondary); line-height: 1.6;">请重启后端服务（<code style="background: var(--bg-secondary); padding: 2px 6px; border-radius: 4px; font-size: 12px;">python main.py</code>）后，新配置的 MCP 工具才会在对话中生效。</p>
                                        <p style="margin: 10px 0 0 0; color: var(--success, #52c41a); font-size: 13px;">✓ 主题配色已即时生效，无需重启。</p>
                                    </div>
                                </div>
                            </div>
                            <div class="modal-footer">
                                <button class="btn-primary" @click="confirmRestartNotice">知道了</button>
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

                const profileForm = ref({
                    username: '',
                    avatar: '',
                    assistant_style: '',
                    old_password: '',
                    new_password: '',
                    confirm_password: '',
                });

                const settingsForm = ref({
                    theme: 'default',
                    mcp_servers: [],
                });

                const themes = [
                    { value: 'default', name: '默认', preview: 'linear-gradient(135deg, #3D6B5B, #F7F5F0)' },
                    { value: 'dark', name: '深色', preview: 'linear-gradient(135deg, #6BA896, #1A1A19)' },
                    { value: 'ocean', name: '海洋', preview: 'linear-gradient(135deg, #2E6B9E, #F0F4F8)' },
                    { value: 'sunset', name: '日落', preview: 'linear-gradient(135deg, #C4622E, #FBF5F0)' },
                    { value: 'forest', name: '森林', preview: 'linear-gradient(135deg, #2E8B57, #F0F5F0)' },
                    { value: 'lavender', name: '薰衣草', preview: 'linear-gradient(135deg, #7B4FA8, #F5F0F8)' },
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
                        // 优先请求后端历史：同时完成归属校验（403 抛错），
                        // 不能先读本地缓存——他人会话的缓存消息会直接展示，造成"会话共享"假象
                        const history = await apiGetHistory(currentThreadId.value);
                        messages.value = parseHistory(history);
                        cache.setMessages(currentThreadId.value, messages.value);
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
                        messages.value = cached && cached.length > 0 ? cached : [];
                    }
                };

                const parseHistory = (history) => {
                    if (!Array.isArray(history)) return [];
                    return history.map(item => ({
                        id: generateId(), // 唯一 key，避免数组变更时 DOM 错乱
                        role: item.role === 'human' ? 'user' : 'assistant',
                        content: extractContentText(item.content),
                        time: formatTime()
                    }));
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

                const scrollToBottom = async () => {
                    await nextTick();
                    if (messagesContainer.value) {
                        messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
                    }
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
                    await loadCurrentMessages();
                    closeSidebar();
                    scrollToBottom();
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

                const clearCurrentChat = () => {
                    messages.value = [];
                    const session = sessions.value.find(s => s.id === currentThreadId.value);
                    if (session) {
                        session.messages = [];
                        session.title = '新会话';
                        session.isBlank = true;
                    }
                    saveMessages();
                    saveSessions();
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

                    try {
                        // 注意：push 后必须从响应式代理中取回引用。Vue 3 的 proxy 是惰性转换的，
                        // push 进数组的是原始对象，若直接持有它并赋值 content，不会触发响应式
                        // 更新（流式输出卡在"正在思考..."，刷新后从缓存整体赋值才显示）。
                        messages.value.push({ id: generateId(), role: 'assistant', content: '', time: formatTime() });
                        const aiMsg = messages.value[messages.value.length - 1];
                        saveMessages();
                        scrollToBottom();

                        // 流式节流：避免每个 token 都做全量 v-html 重渲染 + localStorage 序列化，
                        // 长回答下会阻塞主线程导致界面冻结/整页白屏。latestText 只保留最新文本，
                        // 定时器触发时渲染最新值（节流而非防抖，视觉上仍是平滑逐字输出）。
                        let latestText = '';
                        let renderTimer = null;
                        let saveTimer = null;

                        // 创建 AbortController 用于停止回复
                        abortController.value = new AbortController();

                        // file_ids 已在发送前提取（pendingFileIds），附件区已清空
                        const answer = await apiChat(content, currentThreadId.value, (text) => {
                            streaming.value = true;
                            latestText = text;
                            if (!renderTimer) {
                                renderTimer = setTimeout(() => {
                                    renderTimer = null;
                                    aiMsg.content = latestText;
                                    scrollToBottom();
                                }, 100);
                            }
                            if (!saveTimer) {
                                saveTimer = setTimeout(() => {
                                    saveTimer = null;
                                    saveMessages();
                                }, 500);
                            }
                        }, abortController.value.signal, (toolEvent) => {
                            // 工具调用事件：显示/隐藏加载界面
                            if (toolEvent.type === 'start') {
                                currentToolCall.value = { name: toolEvent.name, args: toolEvent.args };
                            } else if (toolEvent.type === 'end') {
                                currentToolCall.value = null;
                            }
                        }, pendingFileIds);

                        // 流结束：清掉未触发的节流器，确保最终内容一次性落库渲染
                        if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
                        if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
                        currentToolCall.value = null;  // 清除工具调用状态
                        aiMsg.content = answer || '（无回复）';
                        streaming.value = false;
                        saveMessages();
                        saveSessions();
                        scrollToBottom();
                    } catch (err) {
                        // 用户主动停止回复（AbortController.abort()）
                        if (err.name === 'AbortError') {
                            // 保留已生成的部分内容，不删除 AI 消息
                            if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
                            if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
                            aiMsg.content = aiMsg.content || '（已停止）';
                            streaming.value = false;
                            saveMessages();
                            scrollToBottom();
                            return;
                        }
                        messages.value.pop();
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
                            // 会话被判定为他人所有：从列表移除并新建会话，不再复用该 thread
                            const removed = currentThreadId.value;
                            sessions.value = sessions.value.filter(s => s.id !== removed);
                            cache.removeMessages(removed);
                            saveSessions();
                            createNewSession();
                            showToast('该会话不属于当前账号，已切换新会话', 'error');
                            return;
                        }
                        showToast('发送消息失败: ' + err.message, 'error');
                    } finally {
                        isLoading.value = false;
                        streaming.value = false;
                        currentToolCall.value = null;  // 确保异常时也清除工具调用状态
                        focusInput();
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

                const logout = () => {
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

                function openProfileModal() {
                    userMenuOpen.value = false;
                    // 加载当前用户信息
                    profileForm.value.username = user.value?.name || '';
                    profileForm.value.avatar = user.value?.avatar || '';
                    profileForm.value.assistant_style = user.value?.assistant_style || '';
                    profileForm.value.old_password = '';
                    profileForm.value.new_password = '';
                    profileForm.value.confirm_password = '';
                    profileModalOpen.value = true;
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
                }

                function closeSettingsModal() {
                    settingsModalOpen.value = false;
                }

                async function saveProfile() {
                    profileSaving.value = true;
                    try {
                        // 更新基本信息
                        const res = await fetch(`${API_BASE}/api/users/${user.value.userId}/profile`, {
                            method: 'PUT',
                            headers: authHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({
                                username: profileForm.value.username,
                                avatar: profileForm.value.avatar,
                                assistant_style: profileForm.value.assistant_style,
                            }),
                        });
                        syncTokenFromHeaders(res.headers);
                        if (res.ok) {
                            // 更新本地用户信息
                            user.value.name = profileForm.value.username;
                            user.value.avatar = profileForm.value.avatar;
                            user.value.assistant_style = profileForm.value.assistant_style;
                            cache.set(STORAGE_KEY.USER, user.value);
                            showToast('个人信息已更新', 'success');
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

                        // MCP 配置变更：弹出重启提示（不自动关闭设置弹窗）
                        if (mcpChanged) {
                            restartNoticeOpen.value = true;
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
                    clearCurrentChat, sendMessage, sendQuick,
                    handleKeydown, autoResize, openSidebar, closeSidebar,
                    logout, escapeHtml,
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
