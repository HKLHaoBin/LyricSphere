// 端口切换相关
async function updatePortUI() {
    try {
        const res = await fetch('/get_port_status');
        const data = await res.json();
        console.log('[updatePortUI] 端口状态', data);
        currentPortMode = data.mode === 'random' ? 'random' : 'fixed'
        const portBtn = document.getElementById('portSwitchBtn');

        // 仅本机或系统管理员可切换端口
        let canSwitchPort = false;
        try {
            const authData = await fetchAuthStatus();
            canSwitchPort = authData.status === 'success' && (Boolean(authData.is_local) || Boolean(authData.is_system_admin));
        } catch (e) {}
        if (!canSwitchPort) {
            portBtn.disabled = true;
            portBtn.style.background = '#e9ecef';
            portBtn.style.color = '#bbb';
            portBtn.style.cursor = 'not-allowed';
            portBtn.title = t('port.onlyLocalOrSystemAdmin');
        } else {
            portBtn.disabled = false;
            portBtn.style.background = '';
            portBtn.style.color = '';
            portBtn.style.cursor = '';
            portBtn.title = '';
        }

        // 歌词样式按钮
        document.querySelectorAll('.am-style, .fs-style, .fslr-style').forEach(btn => {
            console.log('[updatePortUI] 处理按钮', btn, '当前mode:', data.mode, 'classList:', btn.classList.value);
            // 检查是否为 AMLL 规则编写按钮（通过 onclick 属性判断）
            const isAMLLButton = btn.getAttribute('onclick') && btn.getAttribute('onclick').includes("'amll'");

            if (data.mode === 'random') {
                // AMLL 规则编写按钮在随机端口下仍然可用
                if (isAMLLButton) {
                    btn.disabled = false;
                    btn.classList.remove('disabled-style');
                    btn.title = '';
                    //btn.textContent = 'AMLL规则编写';
                } else {
                    btn.disabled = true;
                    btn.classList.add('disabled-style');
                    btn.title = t('port.unavailable');
                    btn.addEventListener('mouseenter', function() {
                        btn.textContent = t('song.unavailable');
                    });
                    btn.addEventListener('mouseleave', function() {
                        btn.textContent = btn.classList.contains('am-style') ? t('song.amStyle') : (btn.classList.contains('fs-style') ? t('song.fullscreenStyle') : t('song.fullscreenDuetStyle'));
                    });
                }
            } else {
                btn.disabled = false;
                btn.classList.remove('disabled-style');
                btn.title = '';
                if (isAMLLButton) {
                    //btn.textContent = 'AMLL规则编写';
                } else {
                    btn.textContent = btn.classList.contains('am-style') ? t('song.amStyle') : (btn.classList.contains('fs-style') ? t('song.fullscreenStyle') : t('song.fullscreenDuetStyle'));
                }
            }
            // 变色后立即打印样式
            console.log('[updatePortUI] 变色后 style.background:', btn.style.background, 'computed:', getComputedStyle(btn).background);
        });
        if (data.mode === 'random') {
            portBtn.textContent = t('btn.restorePort');
        } else {
            portBtn.textContent = t('btn.randomPort');
        }
        applyPortModeToAllSongItems()
    } catch (e) {console.error('[updatePortUI] error', e);}
}
async function switchPortMode() {
    const portBtn = document.getElementById('portSwitchBtn');
    portBtn.disabled = true;
    portBtn.textContent = t('btn.portSwitching');
    try {
        const res = await fetch('/get_port_status');
        const data = await res.json();
        if (data.mode === 'random') {
            // 恢复
            const resp = await fetch('/restore_port', {method: 'POST'});
            const d = await resp.json();
            if (d.status === 'success') {
                setTimeout(() => {
                    const target = `${window.location.protocol}//${window.location.hostname}:5000/`;
                    window.location.href = target;
                }, 1200);
            }
        } else {
            // 随机端口
            const resp = await fetch('/switch_port', {method: 'POST'});
            const d = await resp.json();
            if (d.status === 'success') {
                setTimeout(() => {
                    const target = `${window.location.protocol}//${window.location.hostname}:${d.port}/`;
                    window.location.href = target;
                }, 1200);
            }
        }
    } catch (e) {
        alert(t('port.switchFailed'));
    }
}

// 页面加载时自动刷新端口UI和安全状态
window.addEventListener('DOMContentLoaded', async function() {
    updatePortUI();
    await updateSecurityUI();
    await updateAuthUI();
});

// 更新安全保护UI状态
async function updateSecurityUI() {
    try {
        const res = await fetch('/get_security_status');
        const data = await res.json();
        const securityBtn = document.getElementById('securityToggleBtn');
        
        if (data.security_enabled) {
            securityBtn.textContent = '🔒 ' + t('btn.security');
            securityBtn.title = t('btn.security.title.on');
            securityBtn.dataset.baseTitle = t('btn.security.title.on');
            securityBtn.style.background = '#37b24d';
        } else {
            securityBtn.textContent = '🔓 ' + t('btn.securityOff');
            securityBtn.title = t('btn.security.title.off');
            securityBtn.dataset.baseTitle = t('btn.security.title.off');
            securityBtn.style.background = '#f03e3e';
        }
    } catch (e) {
        console.error('获取安全状态失败:', e);
    }
}

// 更新认证UI状态
async function fetchAuthStatus() {
    const res = await fetch('/auth/status');
    return await res.json();
}

async function updateAuthUI() {
    try {
        const data = await fetchAuthStatus();
        const authBtn = document.getElementById('authToggleBtn');
        const securityBtn = document.getElementById('securityToggleBtn');
        
        if (data.status === 'success') {
            const baseSecurityTitle = securityBtn ? (securityBtn.dataset.baseTitle || securityBtn.title || '') : '';
            const statusSuffix = data.is_local
                ? t('btn.auth.title.local')
                : data.is_system_admin
                    ? t('btn.auth.title.unlockedSystem')
                    : data.trusted
                        ? t('btn.auth.title.unlockedShared')
                        : data.authenticated
                            ? t('btn.auth.title.loggedInShared')
                            : t('btn.auth.title.locked');
            if (data.is_local) {
                authBtn.textContent = '🔓 ' + t('btn.auth.local');
                authBtn.title = t('btn.auth.title.local');
                authBtn.style.background = '#37b24d';
            } else if (data.is_system_admin) {
                authBtn.textContent = '🔓 ' + t('btn.auth.unlockedSystem');
                authBtn.title = t('btn.auth.title.unlockedSystem');
                authBtn.style.background = '#37b24d';
            } else if (data.trusted) {
                authBtn.textContent = '🔓 ' + t('btn.auth.unlockedShared');
                authBtn.title = t('btn.auth.title.unlockedShared');
                authBtn.style.background = '#37b24d';
            } else if (data.authenticated) {
                authBtn.textContent = '🔑 ' + t('btn.auth.loggedInShared');
                authBtn.title = t('btn.auth.title.loggedInShared');
                authBtn.style.background = '';
            } else {
                authBtn.textContent = '🔑 ' + t('btn.auth.locked');
                authBtn.title = t('btn.auth.title.locked');
                authBtn.style.background = '';
            }
            
            // 更新安全保护按钮的提示信息
            if (securityBtn) {
                securityBtn.title = baseSecurityTitle ? `${baseSecurityTitle} | ${statusSuffix}` : statusSuffix;
            }
        }
    } catch (e) {
        console.error('获取认证状态失败:', e);
        const authBtn = document.getElementById('authToggleBtn');
        authBtn.textContent = '🔑 ' + t('btn.auth.locked');
        authBtn.title = t('btn.auth.title.locked');
    }
}

function updateAuthSuccessMessage(authData) {
    const helpText = document.querySelector('#authSuccess .form-help-text');
    const titleText = document.getElementById('authSuccessTitle');
    if (!helpText || !titleText) return;

    if (authData && authData.is_local) {
        titleText.textContent = t('auth.localSuccess');
        helpText.textContent = t('auth.localDetail');
    } else if (authData && authData.is_system_admin) {
        titleText.textContent = t('auth.systemSuccess');
        helpText.textContent = t('auth.systemDetail');
    } else if (authData && authData.trusted) {
        titleText.textContent = t('auth.sharedSuccess');
        helpText.textContent = t('auth.sharedTrustedDetail');
    } else if (authData && authData.authenticated) {
        titleText.textContent = t('auth.sharedSuccess');
        helpText.textContent = t('auth.sharedLimitedDetail');
    } else {
        titleText.textContent = t('auth.success');
        helpText.textContent = t('auth.canEdit');
    }
}

// 打开认证模态框
async function toggleAuthModal() {
    const modal = document.getElementById('authModal');
    const authStatus = document.getElementById('authStatus');
    const authForm = document.getElementById('authForm');
    const authSuccess = document.getElementById('authSuccess');
    const authLogoutBtn = document.getElementById('authLogoutBtn');
    const authModeHint = document.getElementById('authModeHint');
    
    // 重置模态框状态
    authStatus.style.display = 'block';
    authForm.style.display = 'none';
    authSuccess.style.display = 'none';
    if (authModeHint) authModeHint.style.display = 'block';
    authStatus.textContent = t('auth.checking');
    
    modal.style.display = 'block';
    
    try {
        const data = await fetchAuthStatus();
        
        if (data.status === 'success') {
            if (data.is_local) {
                authStatus.style.display = 'none';
                if (authModeHint) authModeHint.style.display = 'none';
                authSuccess.style.display = 'block';
                authLogoutBtn.style.display = 'none';
                updateAuthSuccessMessage(data);
            } else if (data.authenticated) {
                // 设备已认证
                authStatus.style.display = 'none';
                if (authModeHint) authModeHint.style.display = 'none';
                authSuccess.style.display = 'block';
                authLogoutBtn.style.display = 'inline-block';
                updateAuthSuccessMessage(data);
            } else {
                // 设备已认证但没有写入权限，或未认证
                const passwordHint = data.has_system_password && data.has_shared_credentials
                    ? t('auth.enterPasswordEither')
                    : data.has_system_password
                        ? t('auth.enterPasswordSystem')
                        : data.has_shared_credentials
                            ? t('auth.enterPasswordShared')
                            : t('auth.noPasswordSet');
                authStatus.textContent = data.authenticated
                    ? t('auth.sharedLimitedDetail')
                    : passwordHint;
                authStatus.style.display = 'block';
                authForm.style.display = data.has_password && !data.authenticated ? 'block' : 'none';
                authLogoutBtn.style.display = 'none';
                if (authModeHint) {
                    authModeHint.style.display = 'block';
                }
            }
        }
    } catch (e) {
        authStatus.textContent = t('status.checkFailed');
        console.error('检查认证状态失败:', e);
    }
}

// 关闭认证模态框
function closeAuthModal() {
    pendingFullStaticExportStart = false
    pendingFullStaticExportDownloadTaskId = ''
    document.getElementById('authModal').style.display = 'none';
}

// 设备登录
async function authLogin() {
    const password = document.getElementById('authPassword').value;
    if (!password) {
        alert(t('alert.enterPassword'));
        return;
    }
    
    const authStatus = document.getElementById('authStatus');
    authStatus.textContent = t('status.verifyingPassword');
    
    try {
        const res = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            // 认证成功
            document.getElementById('authStatus').style.display = 'none';
            const authModeHint = document.getElementById('authModeHint');
            if (authModeHint) authModeHint.style.display = 'none';
            document.getElementById('authForm').style.display = 'none';
            document.getElementById('authSuccess').style.display = 'block';
            document.getElementById('authLogoutBtn').style.display = 'inline-block';
            updateAuthSuccessMessage(data);
            
            // 更新UI状态
            await updateAuthUI();
            await updateWriteButtons();
            await updatePortUI();

            if (pendingFullStaticExportStart) {
                pendingFullStaticExportStart = false
                closeAuthModal()
                try {
                    await beginStaticExportTask()
                } catch (exportError) {
                    const exportMessage = exportError && exportError.message ? exportError.message : t('staticExport.createFailed')
                    alert(exportMessage)
                }
                return
            }

            if (pendingFullStaticExportDownloadTaskId) {
                const pendingDownloadTaskId = pendingFullStaticExportDownloadTaskId
                pendingFullStaticExportDownloadTaskId = ''
                closeAuthModal()
                await downloadFullStaticExport(pendingDownloadTaskId)
                return
            }
        } else {
            authStatus.textContent = t('status.wrongPassword');
            authStatus.style.color = '#f03e3e';
            setTimeout(() => {
                authStatus.style.color = '';
            }, 2000);
        }
    } catch (e) {
        authStatus.textContent = t('status.loginFailed');
        console.error('登录失败:', e);
    }
}

// 设备登出
async function authLogout() {
    try {
        const res = await fetch('/auth/logout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            // 登出成功
            pendingFullStaticExportStart = false
            pendingFullStaticExportDownloadTaskId = ''
            document.getElementById('authModal').style.display = 'none';
            
            // 更新UI状态
            await updateAuthUI();
            await updateWriteButtons();
            await updatePortUI();
            
            alert(t('alert.deviceLoggedOut'));
        }
    } catch (e) {
        console.error('登出失败:', e);
        alert(t('alert.logoutFailed'));
    }
}

// 切换安全保护模式
async function toggleSecurityMode() {
    if (!confirm(t('confirm.switchSecurityWarning'))) {
        return;
    }
    
    const securityBtn = document.getElementById('securityToggleBtn');
    securityBtn.disabled = true;
    securityBtn.textContent = t('status.switching');
    
    try {
        const response = await fetch('/toggle_security', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        if (data.status === 'success') {
            await updateSecurityUI();
            alert(t('runtime.securityToggled') + (data.security_enabled ? t('runtime.enabled') : t('runtime.disabled')));
            location.reload(); // 重新加载页面以应用新的安全设置
        } else {
            alert(t('alert.switchFailedPrefix') + data.message);
        }
    } catch (error) {
        alert(t('alert.switchSecurityFailedPrefix') + error);
    } finally {
        securityBtn.disabled = false;
        await updateSecurityUI();
    }
}

async function updateWriteButtons() {
    try {
        const authData = await fetchAuthStatus();
        const isLocal = authData.status === 'success' && Boolean(authData.is_local);
        const securityEnabled = authData.status === 'success' ? Boolean(authData.security_enabled) : true;
        const isSystemAdmin = authData.status === 'success' && Boolean(authData.is_system_admin);
        const isTrusted = authData.status === 'success' && Boolean(authData.trusted);
        const shouldLockWriteButtons = Boolean(securityEnabled && !isLocal && !isSystemAdmin && !isTrusted)
        currentWriteLockEnabled = shouldLockWriteButtons
        
        // 如果安全保护启用且不是本地访问且设备不受信任，则禁用修改功能
        if (shouldLockWriteButtons) {
            var selectors = [
                '.file-actions .action-button',
                '.style-buttons .style-button',
                '.save-btn',
                '.save-all-btn',
                '.path-update-btn',
                '.close-btn',
                '.lyrics-action-btn',
                '.editor-buttons button',
                '.copy-btn',
                '.modal-content button',
                '.modal-content input',
                '.modal-content textarea'
            ];
            selectors.forEach(function(sel){
                document.querySelectorAll(sel).forEach(function(btn){
                    if(btn.hasAttribute('data-write-lock')){
                        btn.disabled = true;
                        btn.style.pointerEvents = 'none';
                        btn.style.opacity = 0.5;
                    }
                });
            });
        } else {
            // 恢复所有按钮状态
            var selectors = [
                '.file-actions .action-button',
                '.style-buttons .style-button',
                '.save-btn',
                '.save-all-btn',
                '.path-update-btn',
                '.close-btn',
                '.lyrics-action-btn',
                '.editor-buttons button',
                '.copy-btn',
                '.modal-content button',
                '.modal-content input',
                '.modal-content textarea'
            ];
            selectors.forEach(function(sel){
                document.querySelectorAll(sel).forEach(function(btn){
                    btn.disabled = false;
                    btn.style.pointerEvents = '';
                    btn.style.opacity = '';
                });
            });
        }
        applyWriteLockToAllSongItems()
    } catch (e) {}
}

function formatPermissionSummary(permissions) {
    const perms = permissions || {};
    const labels = {
        ai_use: 'AI 使用',
        ai_view_provider: '查看提供商',
        ai_view_base_url: '查看 Base URL',
        ai_view_model: '查看模型',
        ai_view_prompts: '查看提示词',
        ai_edit_preset: '编辑预设',
        write_access: '写权限',
    };
    const enabled = Object.keys(labels).filter(key => perms[key]).map(key => labels[key]);
    return enabled.length ? enabled.join('、') : '-';
}

function formatTrustedAuthType(authType, systemAdmin) {
    if (systemAdmin || authType === 'system') {
        return t('device.authType.system')
    }
    if (authType === 'credential') {
        return t('device.authType.shared')
    }
    return t('device.authType.unknown')
}

function formatCredentialMaxUses(maxUses) {
    if (maxUses === null || maxUses === undefined || maxUses === '' || Number(maxUses) === 0) {
        return '不限';
    }
    return String(maxUses);
}

function buildCredentialShareNote(credential) {
    const maxUsesText = formatCredentialMaxUses(credential?.max_uses);
    return [
        '凭据分享说明',
        `备注：${credential?.remark || '-'}`,
        `有效期：${credential?.expires_at || '不限'}`,
        `次数上限：${maxUsesText}`,
        `权限：${formatPermissionSummary(credential?.permissions)}`,
        '',
        '共享凭据密码必须全局唯一，不能与系统密码重复。',
        '对方需要输入你单独发送的密码。',
        '不要分享 credential_id 或其他内部编号。',
        '如果需要重新发给别人，请重新设置密码。',
    ].join('\n');
}

function buildCredentialSharePayload(credential, plainPassword) {
    const maxUsesText = formatCredentialMaxUses(credential?.max_uses);
    return [
        '凭据分享内容',
        `密码：${plainPassword || '-'}`,
        `备注：${credential?.remark || '-'}`,
        `有效期：${credential?.expires_at || '不限'}`,
        `次数上限：${maxUsesText}`,
        `权限：${formatPermissionSummary(credential?.permissions)}`,
        '',
        '共享凭据密码必须全局唯一，不能与系统密码重复。',
        '对方只需要密码，不要分享 credential_id 或其他内部编号。',
    ].join('\n');
}

async function copyTextWithFallback(text, promptTitle) {
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return 'clipboard';
        }
    } catch (error) {
        console.warn('复制文本失败:', error);
    }
    const manualCopy = window.prompt(promptTitle, text);
    return manualCopy === null ? 'cancelled' : 'prompt';
}

async function shareCredentialInfo(credential) {
    const shareText = buildCredentialShareNote(credential);
    const copyResult = await copyTextWithFallback(shareText, '复制下面的凭据分享说明');
    if (copyResult === 'clipboard') {
        alert('已复制凭据分享说明到剪贴板');
        return;
    }
    if (copyResult === 'prompt') {
        alert('请手动复制上方内容');
    }
}

// 显示认证设备列表
async function showTrustedDevices() {
    try {
        const res = await fetch('/auth/trusted');
        const data = await res.json();
        
        if (data.status === 'success') {
            if (data.devices.length === 0) {
                alert(t('device.noDevices'));
                return;
            }
            
            let message = t('device.trustedList', {count: data.total}) + '\n\n';
            data.devices.forEach(device => {
                message += t('device.idLabel') + device.device_id + '\n';
                message += `IP：${device.ip}\n`;
                message += t('device.createdAtLabel') + device.created_at + '\n';
                message += t('device.lastSeenLabel') + device.last_seen + '\n';
                message += `认证类型：${formatTrustedAuthType(device.auth_type, device.system_admin)}\n`;
                message += `凭据：${device.credential_id || '-'}\n`;
                message += `备注：${device.remark || '-'}\n`;
                message += `权限：${formatPermissionSummary(device.permissions)}\n`;
                message += t('device.uaHashLabel') + device.ua_hash + '\n';
                message += '─'.repeat(30) + '\n';
            });
            
            alert(message);
        } else {
            alert(t('alert.getDeviceListFailed'));
        }
    } catch (e) {
        console.error('获取设备列表失败:', e);
        alert(t('alert.getDeviceListFailedNoPermission'));
    }
}

// 清空所有设备
async function revokeAllDevices() {
    if (!confirm(t('confirm.clearAllDevicesWarning'))) {
        return;
    }
    
    try {
        const res = await fetch('/auth/revoke_all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            alert(t('runtime.clearedDevices', {count: data.revoked_count}));
            // 更新UI状态
            await updateAuthUI();
            await updateWriteButtons();
        } else {
            alert(t('alert.clearDevicesFailed'));
        }
    } catch (e) {
        console.error('清空认证设备失败:', e);
        alert(t('alert.clearDevicesFailedNoPermission'));
    }
}

function closeCredentialManagerModal() {
    const modal = document.getElementById('credentialManagerModal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function closeAiUsageMonitorModal() {
    const modal = document.getElementById('aiUsageMonitorModal');
    if (modal) modal.style.display = 'none';
}

async function showAiUsageMonitorModal() {
    const modal = document.getElementById('aiUsageMonitorModal');
    if (modal) modal.style.display = 'block';
    await refreshAiUsageMonitor();
}

function formatAiUsageTokens(n) {
    return (n == null || n === '') ? '-' : String(n);
}

function renderAiUsageSummary(summary) {
    const list = document.getElementById('aiUsageSummaryList');
    if (!list) return;
    list.innerHTML = '';
    (Array.isArray(summary) ? summary : []).forEach(item => {
        const el = document.createElement('div');
        el.style.cssText = 'border:1px solid var(--border-color); border-radius:10px; padding:10px; background: var(--card-bg, #fff);';
        const primary = item.credential_primary_label
            || (item.credential_remark ? item.credential_remark : (item.credential_id === 'system' ? '系统管理员' : '未备注'));
        const secondary = item.credential_secondary_label || item.credential_id || '';
        el.innerHTML = `
            <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
                <div>
                    <div style="font-weight:600;">${escapeHtml(primary)}</div>
                    ${secondary ? `<div style="font-size:11px; color:var(--text-secondary); margin-top:2px;">${escapeHtml(secondary)}</div>` : ''}
                </div>
                <button style="font-size:12px; padding:4px 8px; flex-shrink:0;" onclick="refreshAiUsageRecent('${(item.credential_id || '').replace(/'/g, "\\'")}')">只看此凭据</button>
            </div>
            <div style="font-size:12px; color:var(--text-secondary); margin-top:6px; line-height:1.6;">
                总计: ${item.total || 0} ｜ 成功: ${item.success || 0} ｜ 失败: ${item.failure || 0}<br>
                预设数: ${item.preset_count != null ? item.preset_count : '-'} ｜ 输入Token: ${formatAiUsageTokens(item.prompt_tokens_total)} ｜ 输出Token: ${formatAiUsageTokens(item.completion_tokens_total)}
            </div>
        `;
        list.appendChild(el);
    });
}

function aiUsageSongDisplay(ev) {
    const preview = (ev.song_names_preview || ev.song_name || '').toString().trim();
    if (preview) return preview;
    const jsonFile = (ev.jsonFile || '').toString().trim();
    if (jsonFile) return jsonFile.replace(/\.json$/i, '');
    return '';
}

function renderAiUsageRecent(events) {
    const list = document.getElementById('aiUsageRecentList');
    if (!list) return;
    list.innerHTML = '';
    (Array.isArray(events) ? events : []).slice(0, 200).forEach(ev => {
        const el = document.createElement('div');
        el.style.cssText = 'border:1px solid var(--border-color); border-radius:10px; padding:10px; background: var(--card-bg, #fff);';
        const ts = ev.ts ? new Date(ev.ts).toLocaleString() : '';
        const ok = ev.success === true ? '✅' : (ev.success === false ? '❌' : '⏺');
        const effectiveModel = (ev.effective_model || ev.model || '').toString();
        const title = `${ok} ${ts} ｜ ${ev.credential_id || 'unknown'} ｜ ${effectiveModel}`;
        const sub = [
            ev.preset_id ? `preset=${ev.preset_id}` : '',
            ev.source_mode ? `source=${ev.source_mode}` : '',
            ev.resolved_from ? `from=${ev.resolved_from}` : '',
            ev.mode ? `mode=${ev.mode}` : '',
            (ev.item_count != null) ? `items=${ev.item_count}` : '',
            (ev.duration_ms != null) ? `dur=${ev.duration_ms}ms` : '',
        ].filter(Boolean).join(' ｜ ');
        const songLine = aiUsageSongDisplay(ev) || '-';
        const tokenLine = `输入Token: ${formatAiUsageTokens(ev.prompt_tokens)} ｜ 输出Token: ${formatAiUsageTokens(ev.completion_tokens)}`;
        let preview = (ev.content_preview || '').toString();
        if (!preview && Array.isArray(ev.items) && ev.items.length) {
            const lines = ev.items.slice(0, 8).map(item => {
                const label = (item.song_name || item.jsonFile || item.id || '').toString();
                const p = (item.content_preview || '').toString().replace(/\r/g, '').split('\n').slice(0, 2).join(' / ');
                return `${label}${p ? `: ${p}` : ''}`;
            });
            preview = lines.join('\n');
        }
        preview = preview.slice(0, 420);
        const modelDetail = [
            ev.translation_model ? `翻译=${ev.translation_model}` : '',
            ev.thinking_model ? `思考=${ev.thinking_model}` : '',
        ].filter(Boolean).join(' ｜ ');
        el.innerHTML = `
            <div style="font-weight:600; margin-bottom:6px;">${escapeHtml(title)}</div>
            <div style="font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${escapeHtml(sub)}</div>
            ${modelDetail ? `<div style="font-size:12px; color:var(--text-secondary); margin-bottom:4px;">${escapeHtml(modelDetail)}</div>` : ''}
            <div style="font-size:12px; margin-bottom:4px;">歌曲：${escapeHtml(songLine)}</div>
            <div style="font-size:12px; color:var(--text-secondary); margin-bottom:6px;">${escapeHtml(tokenLine)}</div>
            <div style="font-size:12px; white-space:pre-wrap; opacity:.95;">${escapeHtml(preview || '-')}</div>
            ${ev.error ? `<div style="margin-top:6px; font-size:12px; color:#b91c1c; white-space:pre-wrap;">${escapeHtml(String(ev.error))}</div>` : ''}
        `;
        list.appendChild(el);
    });
}

function getAiUsageDays() {
    const daysEl = document.getElementById('aiUsageDays');
    const val = daysEl ? Number(daysEl.value) : 7;
    return Number.isFinite(val) ? val : 7;
}

async function refreshAiUsageRecent(credentialId = '') {
    const qEl = document.getElementById('aiUsageQuery');
    const q = qEl ? String(qEl.value || '').trim() : '';
    const days = getAiUsageDays();
    const status = document.getElementById('aiUsageMonitorStatus');
    if (status) status.textContent = '正在加载最近请求...';
    const url = new URL('/admin/ai-usage/recent', window.location.origin);
    url.searchParams.set('days', String(days));
    url.searchParams.set('limit', '200');
    if (credentialId) url.searchParams.set('credential_id', credentialId);
    if (q) url.searchParams.set('q', q);
    const res = await fetch(url.toString());
    const data = await res.json();
    if (data.status !== 'success') {
        throw new Error(data.message || '加载失败');
    }
    renderAiUsageRecent(data.events || []);
    if (status) status.textContent = `已加载：${Math.min((data.events || []).length, 200)} 条`;
}

async function refreshAiUsageMonitor() {
    const days = getAiUsageDays();
    const status = document.getElementById('aiUsageMonitorStatus');
    if (status) status.textContent = '正在加载...';
    const res = await fetch(`/admin/ai-usage/summary?days=${encodeURIComponent(days)}`);
    const data = await res.json();
    if (data.status !== 'success') {
        if (status) status.textContent = data.message || '加载失败';
        return;
    }
    renderAiUsageSummary(data.summary || []);
    await refreshAiUsageRecent('');
}

function exportAiUsage(format) {
    const days = getAiUsageDays();
    const url = `/admin/ai-usage/export?days=${encodeURIComponent(days)}&format=${encodeURIComponent(format || 'jsonl')}`;
    window.open(url, '_blank');
}

let credentialManagerMode = 'create';
let credentialManagerEditingId = '';
const CREDENTIAL_MANAGER_SHOW_REVOKED_KEY = 'credentialManagerShowRevoked';
let credentialManagerCachedCredentials = [];
let credentialManagerShowRevoked = false;

function readCredentialManagerShowRevoked() {
    try {
        const raw = localStorage.getItem(CREDENTIAL_MANAGER_SHOW_REVOKED_KEY);
        if (raw === null || raw === '') return false;
        if (raw === 'true' || raw === '1') return true;
        if (raw === 'false' || raw === '0') return false;
    } catch (e) {
        /* ignore */
    }
    return false;
}

function writeCredentialManagerShowRevoked(value) {
    try {
        localStorage.setItem(CREDENTIAL_MANAGER_SHOW_REVOKED_KEY, value ? 'true' : 'false');
    } catch (e) {
        /* ignore */
    }
    credentialManagerShowRevoked = Boolean(value);
}

credentialManagerShowRevoked = readCredentialManagerShowRevoked();

function countRevokedCredentials(credentials) {
    if (!Array.isArray(credentials)) return 0;
    return credentials.filter(credential => getCredentialLifecycleStatus(credential) === 'revoked').length;
}

function updateCredentialManagerRevokedToggle(credentials) {
    const toolbar = document.getElementById('credentialManagerListToolbar');
    const summary = document.getElementById('credentialManagerRevokedSummary');
    const btn = document.getElementById('credentialManagerToggleRevokedBtn');
    if (!toolbar || !summary || !btn) return;

    const revokedCount = countRevokedCredentials(credentials);
    if (revokedCount === 0) {
        toolbar.style.display = 'none';
        summary.textContent = '';
        btn.textContent = '';
        return;
    }

    toolbar.style.display = 'flex';
    if (credentialManagerShowRevoked) {
        summary.textContent = '';
        btn.textContent = t('credentialManager.hideRevoked', { count: revokedCount });
    } else {
        summary.textContent = t('credentialManager.hiddenRevokedSummary', { count: revokedCount });
        btn.textContent = t('credentialManager.showRevoked', { count: revokedCount });
    }
}

function toggleCredentialManagerRevokedVisibility() {
    credentialManagerShowRevoked = !credentialManagerShowRevoked;
    writeCredentialManagerShowRevoked(credentialManagerShowRevoked);
    renderCredentialManagerList(credentialManagerCachedCredentials);
}

function getCredentialLifecycleStatus(credential) {
    const rawStatus = String(credential?.status || '').trim().toLowerCase();
    if (rawStatus) return rawStatus;
    if (!credential) return 'unknown';
    if (Boolean(credential.revoked)) return 'revoked';
    const expiresAt = String(credential.expires_at || '').trim();
    if (expiresAt) {
        const parsed = new Date(expiresAt);
        if (!Number.isNaN(parsed.getTime()) && parsed.getTime() <= Date.now()) {
            return 'expired';
        }
    }
    const maxUses = Number(credential.max_uses);
    const usedCount = Number(credential.used_count || 0);
    if (Number.isFinite(maxUses) && maxUses >= 0 && usedCount >= maxUses) {
        return 'exhausted';
    }
    if (credential.usable === false) return 'unknown';
    return 'usable';
}

function getCredentialStatusLabel(status) {
    switch (status) {
        case 'usable':
            return t('credentialManager.statusUsable');
        case 'revoked':
            return t('credentialManager.statusRevoked');
        case 'expired':
            return t('credentialManager.statusExpired');
        case 'exhausted':
            return t('credentialManager.statusExhausted');
        default:
            return t('credentialManager.statusUnknown');
    }
}

function updateCredentialManagerFormMode(mode, credential) {
    const normalizedMode = mode === 'edit' && credential?.credential_id ? 'edit' : 'create';
    credentialManagerMode = normalizedMode;
    credentialManagerEditingId = normalizedMode === 'edit' ? String(credential?.credential_id || '').trim() : '';
    const statusDiv = document.getElementById('credentialManagerFormStatus');
    const saveBtn = document.getElementById('credentialManagerSaveBtn');
    if (statusDiv) {
        if (normalizedMode === 'edit') {
            const remark = String(credential?.remark || '').trim();
            statusDiv.textContent = `${t('credentialManager.modeEdit')} ${t('credentialManager.editingLabel')}${credentialManagerEditingId}${remark ? `（${remark}）` : ''}`;
        } else {
            statusDiv.textContent = t('credentialManager.modeCreate');
        }
        statusDiv.classList.remove('mode-create', 'mode-edit');
        statusDiv.classList.add(normalizedMode === 'edit' ? 'mode-edit' : 'mode-create');
    }
    if (saveBtn) {
        saveBtn.textContent = normalizedMode === 'edit' ? t('credentialManager.saveEdit') : t('credentialManager.saveCreate');
    }
}

function clearCredentialForm() {
    document.getElementById('credentialManagerCredentialId').value = '';
    document.getElementById('credentialManagerRemark').value = '';
    document.getElementById('credentialManagerPassword').value = '';
    document.getElementById('credentialManagerExpiresAt').value = '';
    document.getElementById('credentialManagerMaxUses').value = '';
    ['credentialPermAiUse', 'credentialPermAiViewProvider', 'credentialPermAiViewBaseUrl', 'credentialPermAiViewModel', 'credentialPermAiViewPrompts', 'credentialPermAiEditPreset', 'credentialPermWriteAccess'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.checked = false;
    });
    const defaultUse = document.getElementById('credentialPermAiUse');
    if (defaultUse) defaultUse.checked = true;
    updateCredentialManagerFormMode('create');
}

function fillCredentialForm(credential) {
    const lifecycleStatus = getCredentialLifecycleStatus(credential);
    if (lifecycleStatus === 'revoked') {
        alert(t('credentialManager.revokedEditBlocked'));
        return;
    }
    document.getElementById('credentialManagerCredentialId').value = credential?.credential_id || '';
    document.getElementById('credentialManagerRemark').value = credential?.remark || '';
    document.getElementById('credentialManagerPassword').value = '';
    document.getElementById('credentialManagerExpiresAt').value = toDatetimeLocalValue(credential?.expires_at || '');
    document.getElementById('credentialManagerMaxUses').value = credential?.max_uses ?? '';
    const permissions = credential?.permissions || {};
    document.getElementById('credentialPermAiUse').checked = Boolean(permissions.ai_use);
    document.getElementById('credentialPermAiViewProvider').checked = Boolean(permissions.ai_view_provider);
    document.getElementById('credentialPermAiViewBaseUrl').checked = Boolean(permissions.ai_view_base_url);
    document.getElementById('credentialPermAiViewModel').checked = Boolean(permissions.ai_view_model);
    document.getElementById('credentialPermAiViewPrompts').checked = Boolean(permissions.ai_view_prompts);
    document.getElementById('credentialPermAiEditPreset').checked = Boolean(permissions.ai_edit_preset);
    document.getElementById('credentialPermWriteAccess').checked = Boolean(permissions.write_access);
    updateCredentialManagerFormMode('edit', credential);
}

function toDatetimeLocalValue(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function getCredentialPermissionsFromForm() {
    return {
        ai_use: document.getElementById('credentialPermAiUse').checked,
        ai_view_provider: document.getElementById('credentialPermAiViewProvider').checked,
        ai_view_base_url: document.getElementById('credentialPermAiViewBaseUrl').checked,
        ai_view_model: document.getElementById('credentialPermAiViewModel').checked,
        ai_view_prompts: document.getElementById('credentialPermAiViewPrompts').checked,
        ai_edit_preset: document.getElementById('credentialPermAiEditPreset').checked,
        write_access: document.getElementById('credentialPermWriteAccess').checked,
    };
}

function renderCredentialManagerList(credentials) {
    const list = document.getElementById('credentialManagerList');
    if (!list) return;

    const allCredentials = Array.isArray(credentials) ? credentials : [];
    credentialManagerCachedCredentials = allCredentials;
    const revokedCount = countRevokedCredentials(allCredentials);
    const visibleCredentials = credentialManagerShowRevoked
        ? allCredentials
        : allCredentials.filter(credential => getCredentialLifecycleStatus(credential) !== 'revoked');

    list.innerHTML = '';
    updateCredentialManagerRevokedToggle(allCredentials);

    if (allCredentials.length === 0) {
        list.innerHTML = '<div style="padding: 10px; border: 1px dashed var(--border-color); border-radius: 8px; color: var(--text-secondary);">暂无共享凭据</div>';
        return;
    }
    if (visibleCredentials.length === 0) {
        const hiddenNote = t('credentialManager.hiddenRevokedSummary', { count: revokedCount });
        list.innerHTML = `<div style="padding: 10px; border: 1px dashed var(--border-color); border-radius: 8px; color: var(--text-secondary);">${escapeHtml(t('credentialManager.noActiveCredentials'))}（${escapeHtml(hiddenNote)}）</div>`;
        return;
    }
    visibleCredentials.forEach(credential => {
        if (!credential || typeof credential !== 'object') return;
        const status = getCredentialLifecycleStatus(credential);
        const statusLabel = getCredentialStatusLabel(status);
        const isRevoked = status === 'revoked';
        const isUsable = status === 'usable';
        const cardBackground = isRevoked ? 'rgba(240,62,62,0.04)' : (isUsable ? 'rgba(55,178,77,0.04)' : 'rgba(255,193,7,0.06)');
        const cardBorder = isRevoked ? '#f03e3e' : (isUsable ? 'var(--border-color)' : '#f0b429');
        const actionStyle = isRevoked ? 'background:#adb5bd; color:#fff; cursor:not-allowed;' : '';
        const actionTitle = isRevoked ? t('credentialManager.revokedEditBlocked') : '';
        const item = document.createElement('div');
        item.style.padding = '10px';
        item.style.border = `1px solid ${cardBorder}`;
        item.style.borderRadius = '8px';
        item.style.background = cardBackground;
        item.innerHTML = `
            <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
                <div>
                    <div style="font-weight:600;">内部编号：${escapeHtml(credential.credential_id || '-')}</div>
                    <div style="font-size:12px; color:var(--text-secondary);">仅供管理员识别、编辑和吊销，不要对外分享。</div>
                    <div style="font-size:12px; color:var(--text-secondary);">备注：${escapeHtml(credential.remark || '-')}</div>
                    <div style="font-size:12px; color:var(--text-secondary);">权限：${escapeHtml(formatPermissionSummary(credential.permissions))}</div>
                    <div style="font-size:12px; color:var(--text-secondary);">有效期：${escapeHtml(credential.expires_at || '-')}</div>
                    <div style="font-size:12px; color:var(--text-secondary);">次数：${credential.used_count || 0}${credential.max_uses !== null && credential.max_uses !== undefined ? ` / ${formatCredentialMaxUses(credential.max_uses)}` : ''}</div>
                    <div style="font-size:12px; color:var(--text-secondary);">状态：${escapeHtml(statusLabel)}</div>
                    ${isRevoked ? `<div style="font-size:12px; color:#f03e3e; line-height:1.5; margin-top:4px;">${escapeHtml(t('credentialManager.revokedHint'))}</div>` : ''}
                </div>
                <div style="display:flex; flex-direction:column; gap:6px; min-width: 92px;">
                    <button onclick='fillCredentialForm(${JSON.stringify(credential)})' ${isRevoked ? 'disabled' : ''} ${actionTitle ? `title="${escapeHtml(actionTitle)}"` : ''} style="${actionStyle}">编辑</button>
                    <button onclick='shareCredentialInfo(${JSON.stringify(credential)})' ${isRevoked ? 'disabled' : ''} ${actionTitle ? `title="${escapeHtml(actionTitle)}"` : ''} style="${actionStyle}">分享说明</button>
                    <button onclick='revokeCredentialById(${JSON.stringify(credential.credential_id)})' ${isRevoked ? 'disabled' : ''} style="${isRevoked ? 'background:#adb5bd; color:#fff; cursor:not-allowed;' : 'background:#f03e3e; color:#fff;'}">${isRevoked ? '已吊销' : '吊销'}</button>
                </div>
            </div>
        `;
        list.appendChild(item);
    });
}

async function loadCredentialsForManager() {
    const statusDiv = document.getElementById('credentialManagerStatus');
    const list = document.getElementById('credentialManagerList');
    credentialManagerShowRevoked = readCredentialManagerShowRevoked();
    if (statusDiv) {
        statusDiv.textContent = '正在加载凭据列表...';
        statusDiv.style.display = 'block';
    }
    if (list) list.innerHTML = '';
    try {
        const res = await fetch('/auth/credentials');
        const data = await res.json();
        if (data.status !== 'success') {
            throw new Error(data.message || '加载失败');
        }
        if (statusDiv) statusDiv.style.display = 'none';
        const loadedCredentials = Array.isArray(data.credentials) ? data.credentials : [];
        renderCredentialManagerList(loadedCredentials);
        updateCredentialManagerRevokedToggle(loadedCredentials);
    } catch (error) {
        updateCredentialManagerRevokedToggle([]);
        if (statusDiv) {
            statusDiv.textContent = error.message || '加载失败';
            statusDiv.style.display = 'block';
            statusDiv.classList.add('is-error');
        }
    }
}

async function showCredentialManagerModal() {
    clearCredentialForm();
    const modal = document.getElementById('credentialManagerModal');
    if (modal) modal.style.display = 'block';
    const statusDiv = document.getElementById('credentialManagerStatus');
    if (statusDiv) {
        statusDiv.textContent = t('credentialManager.statusHint');
        statusDiv.style.display = 'block';
        statusDiv.classList.remove('is-error');
    }
    await loadCredentialsForManager();
}

async function saveCredentialFromForm() {
    const credentialId = document.getElementById('credentialManagerCredentialId').value.trim();
    const remark = document.getElementById('credentialManagerRemark').value.trim();
    const password = document.getElementById('credentialManagerPassword').value;
    const expiresAt = document.getElementById('credentialManagerExpiresAt').value;
    const maxUsesRaw = document.getElementById('credentialManagerMaxUses').value;
    const maxUses = maxUsesRaw === '' ? null : Number(maxUsesRaw);
    const permissions = getCredentialPermissionsFromForm();
    const isEditMode = credentialManagerMode === 'edit' && Boolean(credentialId);
    const requestCredentialId = isEditMode ? credentialId : '';
    if (credentialManagerMode === 'edit' && !credentialId) {
        alert(t('credentialManager.editStateLost'));
        return;
    }
    const payload = {
        remark,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : '',
        max_uses: Number.isFinite(maxUses) ? maxUses : null,
        permissions,
    };
    if (requestCredentialId) payload.credential_id = requestCredentialId;
    if (password) payload.password = password;

    if (!payload.password && !requestCredentialId) {
        alert('请输入密码');
        return;
    }

    try {
        const res = await fetch(requestCredentialId ? `/auth/credentials/${encodeURIComponent(requestCredentialId)}` : '/auth/credentials', {
            method: requestCredentialId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status !== 'success') {
            throw new Error(data.message || '保存失败');
        }
        const savedCredential = data.credential || {
            remark,
            expires_at: expiresAt ? new Date(expiresAt).toISOString() : '',
            max_uses: Number.isFinite(maxUses) ? maxUses : null,
            permissions,
        };
        clearCredentialForm();
        await loadCredentialsForManager();
        await updateAuthUI();
        await updateWriteButtons();
        if (password) {
            const shareText = buildCredentialSharePayload(savedCredential, password);
            const copyResult = await copyTextWithFallback(shareText, '复制下面的密码和分享说明');
            if (copyResult === 'clipboard') {
                alert('凭据已保存，密码和分享说明已复制到剪贴板');
                return;
            }
            if (copyResult === 'prompt') {
                alert('凭据已保存，请手动复制上方内容');
                return;
            }
        }
        alert('凭据已保存');
    } catch (error) {
        alert(error.message || '保存失败');
    }
}

async function revokeCredentialById(credentialId) {
    if (!credentialId) return;
    if (!confirm(`确认吊销凭据 ${credentialId} ?`)) return;
    try {
        const res = await fetch(`/auth/credentials/${encodeURIComponent(credentialId)}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (data.status !== 'success') {
            throw new Error(data.message || '吊销失败');
        }
        if (credentialManagerMode === 'edit' && credentialManagerEditingId === credentialId) {
            clearCredentialForm();
        }
        await loadCredentialsForManager();
        await updateAuthUI();
        await updateWriteButtons();
    } catch (error) {
        alert(error.message || '吊销失败');
    }
}

// 密码设置相关功能
async function showSetPasswordModal() {
    document.getElementById('setPasswordModal').style.display = 'block';
    // 清空输入框
    document.getElementById('currentPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmPassword').value = '';
    document.getElementById('setPasswordStatus').style.display = 'none';

    const currentPasswordGroup = document.getElementById('currentPasswordGroup')
    const currentPasswordHint = document.getElementById('currentPasswordHint')
    try {
        const authData = await fetchAuthStatus()
        const skipCurrentPassword = authData.status === 'success' && (Boolean(authData.is_local) || Boolean(authData.is_system_admin))
        if (currentPasswordGroup) {
            currentPasswordGroup.style.display = skipCurrentPassword ? 'none' : 'block'
        }
        if (currentPasswordHint) {
            currentPasswordHint.textContent = skipCurrentPassword
                ? t('setPassword.currentHint')
                : t('setPassword.currentHintRemote')
        }
    } catch (error) {
        if (currentPasswordGroup) {
            currentPasswordGroup.style.display = 'block'
        }
        if (currentPasswordHint) {
            currentPasswordHint.textContent = t('setPassword.currentHintRemote')
        }
    }
}

function closeSetPasswordModal() {
    document.getElementById('setPasswordModal').style.display = 'none';
}

async function setPassword() {
    const currentPasswordGroup = document.getElementById('currentPasswordGroup');
    const currentPassword = document.getElementById('currentPassword').value;
    const newPassword = document.getElementById('newPassword').value;
    const confirmPassword = document.getElementById('confirmPassword').value;
    const statusDiv = document.getElementById('setPasswordStatus');
    
    // 验证密码
    if (newPassword.length < 8) {
        statusDiv.textContent = t('status.passwordMinLength');
        statusDiv.style.display = 'block';
        statusDiv.style.background = '#ffeaea';
        return;
    }
    
    if (newPassword !== confirmPassword) {
        statusDiv.textContent = t('status.passwordMismatch');
        statusDiv.style.display = 'block';
        statusDiv.style.background = '#ffeaea';
        return;
    }
    
    try {
        const res = await fetch('/auth/set_password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                password: newPassword,
                current_password: currentPasswordGroup && currentPasswordGroup.style.display === 'none' ? undefined : (currentPassword || undefined)
            })
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            statusDiv.textContent = t('status.passwordSetSuccess');
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#eaffea';
            
            // 清空输入框
            document.getElementById('currentPassword').value = '';
            document.getElementById('newPassword').value = '';
            document.getElementById('confirmPassword').value = '';
            
            // 3秒后关闭模态框
            setTimeout(() => {
                closeSetPasswordModal();
            }, 3000);
        } else {
            statusDiv.textContent = `❌ ${data.message || t('status.setPasswordFailed')}`;
            statusDiv.style.display = 'block';
            statusDiv.style.background = '#ffeaea';
        }
    } catch (e) {
        console.error('设置系统密码失败:', e);
        statusDiv.textContent = t('status.setPasswordFailed');
        statusDiv.style.display = 'block';
        statusDiv.style.background = '#ffeaea';
    }
}

// 设备吊销相关功能
function showRevokeDeviceModal() {
    document.getElementById('revokeDeviceModal').style.display = 'block';
    loadDevicesForRevoke();
}

function closeRevokeDeviceModal() {
    document.getElementById('revokeDeviceModal').style.display = 'none';
}

async function loadDevicesForRevoke() {
    const statusDiv = document.getElementById('revokeDeviceStatus');
    const listDiv = document.getElementById('revokeDeviceList');
    
    try {
        const res = await fetch('/auth/trusted');
        const data = await res.json();
        
        if (data.status === 'success' && data.devices.length > 0) {
            statusDiv.style.display = 'none';
            listDiv.style.display = 'block';
            listDiv.innerHTML = '';
            
            data.devices.forEach(device => {
                const deviceCard = document.createElement('div');
                deviceCard.style.padding = '10px';
                deviceCard.style.border = '1px solid var(--border-color)';
                deviceCard.style.borderRadius = '8px';
                deviceCard.style.marginBottom = '8px';
                deviceCard.innerHTML = `
                    <div style="font-weight: bold;">设备ID：${device.device_id}</div>
                    <div>IP：${device.ip}</div>
                    <div>创建时间：${device.created_at}</div>
                    <div>最后访问：${device.last_seen}</div>
                    <div>认证类型：${formatTrustedAuthType(device.auth_type, device.system_admin)}</div>
                    <div>凭据：${device.credential_id || '-'}</div>
                    <div>备注：${device.remark || '-'}</div>
                    <div>权限：${formatPermissionSummary(device.permissions)}</div>
                    <button onclick="revokeDevice('${device.device_id.replace('...', '')}')" 
                            style="margin-top: 8px; padding: 5px 10px; background: #f03e3e; color: white; border: none; border-radius: 4px; cursor: pointer;">
                        🚫 吊销此设备
                    </button>
                `;
                listDiv.appendChild(deviceCard);
            });
        } else {
            statusDiv.textContent = t('status.noDevices');
            statusDiv.style.background = '#f8f9fa';
        }
    } catch (e) {
        console.error('加载设备列表失败:', e);
        statusDiv.textContent = t('status.loadDevicesFailed');
        statusDiv.style.background = '#ffeaea';
    }
}

async function revokeDevice(deviceIdPrefix) {
    if (!confirm(t('runtime.confirmRevokeDevice', {id: deviceIdPrefix}))) {
        return;
    }
    
    try {
        const res = await fetch('/auth/revoke', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceIdPrefix })
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            alert('✅ ' + t('runtime.revokedDevices', {count: data.revoked_count}));
            // 重新加载设备列表
            loadDevicesForRevoke();
            // 更新UI状态
            await updateAuthUI();
            await updateWriteButtons();
        } else {
            alert(t('alert.revokeDeviceFailed'));
        }
    } catch (e) {
        console.error('吊销认证设备失败:', e);
        alert(t('alert.revokeDeviceFailedRetry'));
    }
}

function searchDevices() {
    const searchTerm = document.getElementById('deviceSearch').value.toLowerCase();
    const deviceCards = document.getElementById('revokeDeviceList').querySelectorAll('div');
    
    deviceCards.forEach(card => {
        const deviceText = card.textContent.toLowerCase();
        if (deviceText.includes(searchTerm)) {
            card.style.display = 'block';
        } else {
            card.style.display = 'none';
        }
    });
}

let searchHeaderOffsetRaf = 0
let lastSearchHeaderOffset = -1

function updateSearchHeaderOffset() {
    const header = document.querySelector('.search-container');
    if (!header) {
        return;
    }
    const isMobileLayout = window.matchMedia('(max-width: 768px), (orientation: portrait)').matches;
    if (isMobileLayout) {
        if (lastSearchHeaderOffset !== -1) {
            document.body.style.removeProperty('--search-header-offset');
            lastSearchHeaderOffset = -1;
        }
        return;
    }
    const height = Math.ceil(header.offsetHeight || header.getBoundingClientRect().height);
    if (height === lastSearchHeaderOffset) {
        return;
    }
    lastSearchHeaderOffset = height;
    document.body.style.setProperty('--search-header-offset', `${height}px`);
}

function scheduleUpdateSearchHeaderOffset() {
    if (searchHeaderOffsetRaf) {
        return;
    }
    searchHeaderOffsetRaf = window.requestAnimationFrame(() => {
        searchHeaderOffsetRaf = 0;
        updateSearchHeaderOffset();
    });
}

// 添加设备管理下拉菜单的显示/隐藏逻辑
document.addEventListener('DOMContentLoaded', function() {
    // 使用固定类选择器
    const manageButton = document.querySelector('.device-manage-button');
    
    if (manageButton) {
        const dropdownMenu = manageButton.querySelector('.dropdown-menu');
        if (dropdownMenu) {
            manageButton.addEventListener('click', function(e) {
                dropdownMenu.style.display = dropdownMenu.style.display === 'none' ? 'block' : 'none';
                e.stopPropagation();
            });
            
            // 点击其他地方关闭下拉菜单
            document.addEventListener('click', function() {
                dropdownMenu.style.display = 'none';
            });
        }
    }
});

document.addEventListener('DOMContentLoaded', function() {
    scheduleUpdateSearchHeaderOffset();
    const header = document.querySelector('.search-container');
    if (header && typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(scheduleUpdateSearchHeaderOffset);
        observer.observe(header);
    }
});

function setupLyricsModalTouchScroll() {
    const modal = document.getElementById('lyricsModal');
    if (!modal) {
        return;
    }

    if (modal.dataset.touchScrollProxyInstalled === '1') {
        return;
    }

    const scroller = modal.querySelector('.lyrics-translation-wrap');
    if (!scroller) {
        return;
    }

    const modalContent = modal.querySelector('.modal-content');
    if (!modalContent) {
        return;
    }

    // Default strategy: if the touch starts anywhere inside the scroller,
    // proxy vertical swipes to the outer scroller for consistent "browse modules" scroll.
    // Exclude areas that must keep their own internal scrolling.
    const excludedScrollSelectors = [
        '#extraPromptContainer'
    ].join(',');

    const monacoContainerSelectors = [
        '#lyricsEditor',
        '#translationEditor',
        '.monaco-editor'
    ].join(',');

    let startX = 0;
    let startY = 0;
    let lastY = 0;
    let tracking = false;
    let startedInMonaco = false;

    const canScroll = (el, wantsScrollUp, wantsScrollDown) => {
        if (!el) return false;
        const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const canScrollUp = el.scrollTop > 0;
        const canScrollDown = el.scrollTop < maxScrollTop;
        return (wantsScrollUp && canScrollUp) || (wantsScrollDown && canScrollDown);
    };

    const onTouchStart = (e) => {
        if (!e.touches || e.touches.length !== 1) {
            tracking = false;
            return;
        }
        const target = e.target instanceof Element ? e.target : null;
        if (!target) {
            tracking = false;
            return;
        }
        if (target.closest(excludedScrollSelectors)) {
            // Let excluded areas keep their own internal scrolling.
            tracking = false;
            return;
        }
        if (!modalContent.contains(target)) {
            tracking = false;
            return;
        }

        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        lastY = startY;
        tracking = true;
        startedInMonaco = Boolean(target.closest(monacoContainerSelectors));
    };

    const onTouchMove = (e) => {
        const isMobile = window.matchMedia('(max-width: 768px), (orientation: portrait)').matches;
        if (!isMobile) {
            tracking = false;
            return;
        }
        if (!tracking || !e.touches || e.touches.length !== 1) {
            return;
        }
        const target = e.target instanceof Element ? e.target : null;
        if (target && target.closest(excludedScrollSelectors)) {
            // Never hijack scroll gestures inside excluded containers.
            tracking = false;
            return;
        }
        const x = e.touches[0].clientX;
        const y = e.touches[0].clientY;
        const dx = x - startX;
        const dy = y - startY;

        // Only take over for mostly-vertical swipes.
        if (Math.abs(dy) < 6 || Math.abs(dy) < Math.abs(dx) * 1.2) {
            return;
        }

        const deltaY = y - lastY;
        lastY = y;

        const wantsScrollUp = deltaY > 0;   // finger down -> content up -> scrollTop decreases
        const wantsScrollDown = deltaY < 0; // finger up   -> content down -> scrollTop increases

        // Candidate chain (inner -> outer):
        // - extraPromptContainer: excluded above, keeps its own native scroll
        // - Monaco internal scroll (native)
        // - lyrics-panel-body
        // - lyrics-translation-wrap
        // - modal-content (fallback)
        if (startedInMonaco) {
            const monacoRoot = (target && target.closest(monacoContainerSelectors)) || modal.querySelector(monacoContainerSelectors);
            const monacoScrollable = monacoRoot ? monacoRoot.querySelector('.monaco-scrollable-element') : null;
            if (canScroll(monacoScrollable, wantsScrollUp, wantsScrollDown)) {
                // Let Monaco handle its own scroll.
                return;
            }
        }

        const panelBody = target ? target.closest('#lyricsModal .lyrics-panel-body') : null;
        const candidates = [
            panelBody,
            scroller,
            modalContent
        ].filter(Boolean);

        for (const el of candidates) {
            if (!canScroll(el, wantsScrollUp, wantsScrollDown)) {
                continue;
            }
            el.scrollTop -= deltaY;
            e.preventDefault();
            return;
        }
    };

    const onTouchEnd = () => {
        tracking = false;
    };

    modal.addEventListener('touchstart', onTouchStart, { passive: true });
    modal.addEventListener('touchmove', onTouchMove, { passive: false });
    modal.addEventListener('touchend', onTouchEnd, { passive: true });
    modal.addEventListener('touchcancel', onTouchEnd, { passive: true });
    modal.dataset.touchScrollProxyInstalled = '1';
}

window.addEventListener('DOMContentLoaded', updateWriteButtons);
document.addEventListener('DOMContentLoaded', setupLyricsModalTouchScroll);