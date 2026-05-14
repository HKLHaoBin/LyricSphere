// 主题切换功能
function toggleDarkMode() {
    document.body.classList.toggle('dark-mode')
    const isDark = document.body.classList.contains('dark-mode')
    localStorage.setItem('darkMode', isDark)
    const toggle = document.getElementById('themeToggle')
    if (toggle) {
        toggle.textContent = isDark ? ('🌞 ' + t('btn.theme.light')) : ('🌒 ' + t('btn.theme.dark'))
    }
    if (typeof applyMonacoTheme === 'function') {
        applyMonacoTheme()
    }
}

// 初始化主题
function initTheme() {
    const savedDarkMode = localStorage.getItem('darkMode') === 'true'
    const toggle = document.getElementById('themeToggle')
    if (savedDarkMode) {
        document.body.classList.add('dark-mode')
    } else {
        document.body.classList.remove('dark-mode')
    }
    if (toggle) {
        if (savedDarkMode) {
            toggle.textContent = '🌞 ' + t('btn.theme.light')
        } else {
            toggle.textContent = '🌓 ' + t('btn.theme')
        }
    }
    if (typeof applyMonacoTheme === 'function') {
        applyMonacoTheme()
    }
}
initTheme()
function toggleLanguage() {
    const newLang = _currentLang === 'zh-CN' ? 'en' : 'zh-CN';
    setLanguage(newLang);
}
// 初始化语言切换按钮
(function initLangSwitchBtn() {
    const btn = document.getElementById('langSwitchBtn');
    if (btn) btn.textContent = _currentLang === 'zh-CN' ? 'EN' : '中文';
})();