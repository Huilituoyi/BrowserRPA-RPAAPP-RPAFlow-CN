# -*- coding: utf-8 -*-
"""
内置浏览器控件：基于 QWebEngineView（Chromium）。
提供地址栏、前进/后退/刷新/主页，并支持按配置应用：
  User-Agent、HTTP 代理、JavaScript 开关、图片加载开关、忽略 SSL 错误。
录制与抓取模块会复用本控件的 page() 与 run_js()。
"""
import os
from urllib.parse import urlparse

from PySide6.QtCore import QUrl, Signal, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QProgressBar, QLabel, QMenu, QApplication,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import (
    QWebEngineProfile, QWebEnginePage, QWebEngineSettings,
    QWebEngineContextMenuRequest,
)
from PySide6.QtNetwork import QNetworkProxy

from config.settings import CACHE_DIR
from core.logging.logger import get_logger

log = get_logger("browser")


def _cleanup_stale_cache():
    """启动前清理损坏的 Chromium 缓存锁文件，防止 'Unable to map Index file' 导致白屏。"""
    import glob
    # 清理 Cache 和 GPUCache 子目录中的 index 文件（损坏时导致映射失败）
    for pattern in ("Cache/index", "GPUCache/index", "Cache/data_1", "Cache/data_2", "Cache/data_3"):
        target = os.path.join(CACHE_DIR, pattern)
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass  # 文件可能被占用，忽略即可


# ===================== 页面加载后注入的全局 JS =====================
# 1) 监听 contextmenu 事件，记录右键目标元素
# 2) 注册 __rpaGetLocator 全局函数，返回 JSON 字符串
_INJECT_JS = r"""
(function() {
    if (window.__rpa_injected) return;
    window.__rpa_injected = true;
    window.__rpa_ctx_target = null;

    document.addEventListener('contextmenu', function(e) {
        window.__rpa_ctx_target = e.target;
    }, true);

    window.__rpaGetLocator = function(px, py) {
        try {
            var el = window.__rpa_ctx_target || document.elementFromPoint(px, py);
            if (!el || el === document.body || el === document.documentElement) return '';

            function isUnique(sel) {
                try { return document.querySelectorAll(sel).length === 1; } catch(e) { return false; }
            }
            function cssEscape(s) {
                if (window.CSS && CSS.escape) return CSS.escape(s);
                return String(s).replace(/[^a-zA-Z0-9_-]/g, function(c) { return '\\' + c; });
            }

            function bestCss(element) {
                if (element.id && isUnique('#' + cssEscape(element.id))) return '#' + cssEscape(element.id);
                var attrs = element.attributes;
                for (var i = 0; i < attrs.length; i++) {
                    var an = attrs[i].name;
                    if (an.indexOf('data-') === 0 && an.indexOf('data-v-') !== 0) {
                        var sel = element.tagName.toLowerCase() + '[' + an + '="' + attrs[i].value + '"]';
                        if (isUnique(sel)) return sel;
                    }
                }
                var parts = [];
                var cur = element;
                while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
                    var part = cur.tagName.toLowerCase();
                    var cn = cur.className;
                    if (typeof cn === 'string' && cn.trim()) {
                        var fc = cn.trim().split(/\s+/)[0];
                        if (fc.indexOf('data-') !== 0) part += '.' + cssEscape(fc);
                    }
                    var parent = cur.parentElement;
                    if (parent) {
                        var same = Array.prototype.filter.call(parent.children, function(s) { return s.tagName === cur.tagName; });
                        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
                    }
                    parts.unshift(part);
                    var full = parts.join(' > ');
                    if (isUnique(full)) return full;
                    cur = cur.parentElement;
                }
                return parts.length > 0 ? parts.join(' > ') : element.tagName.toLowerCase();
            }

            function bestXpath(element) {
                if (element.id) return '//*[@id="' + element.id + '"]';
                var attrs = element.attributes;
                for (var i = 0; i < attrs.length; i++) {
                    var an = attrs[i].name;
                    if (an.indexOf('data-') === 0 && an.indexOf('data-v-') !== 0) {
                        var xp = '//' + element.tagName.toLowerCase() + '[@' + an + '="' + attrs[i].value + '"]';
                        try {
                            if (document.evaluate('count(' + xp + ')', document, null, XPathResult.ANY_TYPE, null).numberValue === 1) return xp;
                        } catch(e) {}
                    }
                }
                var text = (element.textContent || '').trim();
                if (text.length > 0 && text.length <= 50) {
                    var xp2 = '//' + element.tagName.toLowerCase() + '[contains(text(),"' + text.replace(/"/g, '\\"') + '")]';
                    try {
                        if (document.evaluate('count(' + xp2 + ')', document, null, XPathResult.ANY_TYPE, null).numberValue === 1) return xp2;
                    } catch(e) {}
                }
                var parts2 = [], cur2 = element;
                while (cur2 && cur2.nodeType === 1) {
                    var idx = 1, sib = cur2.previousElementSibling;
                    while (sib) { if (sib.tagName === cur2.tagName) idx++; sib = sib.previousElementSibling; }
                    parts2.unshift(cur2.tagName.toLowerCase() + '[' + idx + ']');
                    cur2 = cur2.parentElement;
                }
                return '/' + parts2.join('/');
            }

            var result = {
                css: bestCss(el),
                xpath: bestXpath(el),
                id: el.id || '',
                text: (el.textContent || '').trim().substring(0, 60),
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || ''
            };
            return JSON.stringify(result);
        } catch(err) {
            return JSON.stringify({error: err.message});
        }
    };
})();
"""


# ===================== 元素拾取模式 JS =====================
# 注入后：鼠标悬停高亮元素，左键单击获取 CSS 选择器，ESC 取消
# __rpa_picked 状态：'__PICKING__'=进行中, ''=取消, 其他字符串=已选择的CSS
_PICK_JS = r"""
(function() {
    // 第一时间重置状态（必须在任何可能抛异常的DOM操作之前）
    window.__rpa_picked = '__PICKING__';

    // 清理上次残留的监听器和元素
    if (window.__rpa_pick_cleanup) { try { window.__rpa_pick_cleanup(); } catch(e){} }
    var oldOv = document.getElementById('__rpa_pick_ov');
    var oldHint = document.getElementById('__rpa_pick_hint');
    if (oldOv) oldOv.remove();
    if (oldHint) oldHint.remove();

    try {
        // 高亮框
        var ov = document.createElement('div');
        ov.id = '__rpa_pick_ov';
        ov.style.cssText = 'position:fixed;border:2px solid #2563eb;background:rgba(37,99,235,0.15);z-index:999999;pointer-events:none;display:none;transition:all 0.05s;';
        document.body.appendChild(ov);

        // 底部提示条（不遮挡页面顶部内容）
        var hint = document.createElement('div');
        hint.id = '__rpa_pick_hint';
        hint.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:#2563eb;color:#fff;padding:8px;text-align:center;z-index:999999;font-size:14px;font-family:sans-serif;box-shadow:0 -2px 6px rgba(0,0,0,0.3);';
        hint.innerHTML = '🖱️ 请点击要选择的元素（按 ESC 取消）';
        document.body.appendChild(hint);
    } catch(e) {
        // DOM 不可用，但仍允许通过坐标拾取
    }

    function isUnique(sel) {
        try { return document.querySelectorAll(sel).length === 1; } catch(e) { return false; }
    }
    function cssEscape(s) {
        if (window.CSS && CSS.escape) return CSS.escape(s);
        return String(s).replace(/[^a-zA-Z0-9_-]/g, function(c) { return '\\' + c; });
    }
    function bestCss(el) {
        if (el.id && isUnique('#' + cssEscape(el.id))) return '#' + cssEscape(el.id);
        var attrs = el.attributes;
        for (var i = 0; i < attrs.length; i++) {
            var an = attrs[i].name;
            if (an.indexOf('data-') === 0 && an.indexOf('data-v-') !== 0) {
                var sel = el.tagName.toLowerCase() + '[' + an + '="' + attrs[i].value + '"]';
                if (isUnique(sel)) return sel;
            }
        }
        var parts = [], cur = el;
        while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
            var part = cur.tagName.toLowerCase();
            var cn = cur.className;
            if (typeof cn === 'string' && cn.trim()) {
                var fc = cn.trim().split(/\s+/)[0];
                if (fc.indexOf('data-') !== 0) part += '.' + cssEscape(fc);
            }
            var parent = cur.parentElement;
            if (parent) {
                var same = Array.prototype.filter.call(parent.children, function(s) { return s.tagName === cur.tagName; });
                if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
            }
            parts.unshift(part);
            var full = parts.join(' > ');
            if (isUnique(full)) return full;
            cur = cur.parentElement;
        }
        return parts.length > 0 ? parts.join(' > ') : el.tagName.toLowerCase();
    }

    function cleanup() {
        var ov = document.getElementById('__rpa_pick_ov');
        var hint = document.getElementById('__rpa_pick_hint');
        if (ov) ov.remove();
        if (hint) hint.remove();
        document.removeEventListener('mousemove', onMove, true);
        document.removeEventListener('click', onClick, true);
        document.removeEventListener('keydown', onKey, true);
        window.__rpa_pick_cleanup = null;
    }
    window.__rpa_pick_cleanup = cleanup;

    function onMove(e) {
        var el = e.target;
        if (!el || el.id === '__rpa_pick_ov' || el.id === '__rpa_pick_hint') return;
        var r = el.getBoundingClientRect();
        var ov = document.getElementById('__rpa_pick_ov');
        if (ov) {
            ov.style.display = 'block';
            ov.style.left = r.left + 'px'; ov.style.top = r.top + 'px';
            ov.style.width = r.width + 'px'; ov.style.height = r.height + 'px';
        }
    }
    function onClick(e) {
        e.preventDefault(); e.stopPropagation();
        var el = e.target;
        if (!el || el.id === '__rpa_pick_ov' || el.id === '__rpa_pick_hint') return;
        window.__rpa_picked = bestCss(el);
        cleanup();
    }
    function onKey(e) {
        if (e.key === 'Escape') { window.__rpa_picked = ''; cleanup(); }
    }
    document.addEventListener('mousemove', onMove, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKey, true);
})();
"""


class RpaWebPage(QWebEnginePage):
    """自定义页面：处理 SSL 忽略、把新窗口链接接管到当前页。"""
    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        self._ignore_ssl = False

    def set_ignore_ssl(self, v: bool):
        self._ignore_ssl = v

    def certificateError(self, error):
        if self._ignore_ssl:
            # PySide6 6.11: 使用 acceptCertificate() 替代已移除的 ignoreCertificateErrors()
            try:
                error.acceptCertificate()
            except Exception:
                pass
            return True
        return super().certificateError(error)

    def createWindow(self, _type):
        page = QWebEnginePage(self.profile(), self)
        page.urlChanged.connect(self._takeover_url)
        return page

    def _takeover_url(self, url):
        self.setUrl(url)
        sender = self.sender()
        if sender is not None:
            try:
                sender.deleteLater()
            except Exception:
                pass


class RpaWebView(QWebEngineView):
    """自定义浏览器视图：重写右键菜单，去掉"查看源代码"等会导致卡住的选项，
    并新增「复制定位器」子菜单。"""

    def contextMenuEvent(self, event):
        global_pos = event.globalPos()
        pos = event.pos()
        # 调用页面中已注入的全局函数，传入坐标，返回 JSON 字符串
        js = "window.__rpaGetLocator ? window.__rpaGetLocator(%d, %d) : ''" % (pos.x(), pos.y())
        self.page().runJavaScript(js, lambda r: self._show_menu(global_pos, r))

    def _show_menu(self, global_pos, locator_json):
        # locator_json 是 JS 返回的 JSON 字符串，解析为 dict
        import json as _json
        locator = None
        if locator_json and isinstance(locator_json, str) and locator_json.strip():
            try:
                locator = _json.loads(locator_json)
            except Exception:
                locator = None
        log.info("右键定位器返回：%s", locator)
        menu = QMenu(self)

        # ---- 导航 ----
        a_back = QAction("后退", self)
        a_back.triggered.connect(self.back)
        a_back.setEnabled(self.history().canGoBack())
        menu.addAction(a_back)

        a_fwd = QAction("前进", self)
        a_fwd.triggered.connect(self.forward)
        a_fwd.setEnabled(self.history().canGoForward())
        menu.addAction(a_fwd)

        a_reload = QAction("刷新", self)
        a_reload.triggered.connect(self.reload)
        menu.addAction(a_reload)
        menu.addSeparator()

        # ---- 编辑 ----
        req = self.lastContextMenuRequest()
        if req:
            if req.linkUrl().isValid():
                a_copy_link = QAction("复制链接地址", self)
                a_copy_link.triggered.connect(
                    lambda: QApplication.clipboard().setText(req.linkUrl().toString()))
                menu.addAction(a_copy_link)
            if req.selectedText():
                a_copy = QAction("复制", self)
                a_copy.triggered.connect(
                    lambda: self.triggerPageAction(QWebEnginePage.WebAction.Copy))
                menu.addAction(a_copy)

        # ---- 复制定位器子菜单 ----
        if locator and isinstance(locator, dict):
            menu.addSeparator()
            sub = menu.addMenu("复制定位器")

            css = locator.get("css", "")
            if css:
                label = css if len(css) <= 45 else css[:42] + "…"
                act = sub.addAction("CSS 选择器")
                act.setToolTip(css)
                act.triggered.connect(lambda _=False, v=css: self._copy_locator(v, "CSS"))

            xpath = locator.get("xpath", "")
            if xpath:
                act = sub.addAction("XPath")
                act.setToolTip(xpath)
                act.triggered.connect(lambda _=False, v=xpath: self._copy_locator(v, "XPath"))

            elem_id = locator.get("id", "")
            if elem_id:
                id_str = "#" + elem_id
                act = sub.addAction("ID 选择器")
                act.setToolTip(id_str)
                act.triggered.connect(lambda _=False, v=id_str: self._copy_locator(v, "ID"))

            role = locator.get("role", "")
            aria = locator.get("ariaLabel", "")
            if role and aria:
                role_str = "getByRole('%s', name='%s')" % (role, aria)
                act = sub.addAction("Role + Name")
                act.setToolTip(role_str)
                act.triggered.connect(lambda _=False, v=role_str: self._copy_locator(v, "Role"))

            text = locator.get("text", "")
            if text:
                act = sub.addAction("文本")
                act.setToolTip(text[:40])
                act.triggered.connect(
                    lambda _=False, v=text: self._copy_locator(
                        "getByText('%s')" % v.replace("'", "\\'"), "Text"))

        menu.addSeparator()
        a_save = QAction("另存为…", self)
        a_save.triggered.connect(lambda: self.triggerPageAction(QWebEnginePage.WebAction.SavePage))
        menu.addAction(a_save)

        menu.exec(global_pos)

    def _copy_locator(self, text: str, kind: str):
        QApplication.clipboard().setText(text)
        log.info("已复制%s定位器到剪贴板：%s", kind, text)


class BrowserWidget(QWidget):
    """内置浏览器。"""
    url_changed = Signal(str)
    title_changed = Signal(str)
    load_finished = Signal(bool, str)   # (ok, url)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._cfg = config

        self._view = RpaWebView(self)
        # 清理上次残留的损坏缓存锁文件，防止白屏
        _cleanup_stale_cache()
        # 无痕模式：用内存型 Profile（无名称），关了就清空
        incognito = self._cfg.get("browser", "incognito", default=False)
        if incognito:
            self.profile = QWebEngineProfile()       # off-the-record，不落盘
            log.info("内置浏览器：无痕模式（不留存 cookie/缓存/登录状态）")
        else:
            self.profile = QWebEngineProfile("rpaapp-profile")  # 持久化，保留登录态
            # 设置独立的缓存和数据目录，避免 Chromium 缓存锁冲突导致白屏
            self.profile.setCachePath(CACHE_DIR)
            self.profile.setPersistentStoragePath(CACHE_DIR)
            log.info("内置浏览器：持久化模式（保留 cookie/缓存/登录状态）")
        self.page = RpaWebPage(self.profile, self._view)
        self._view.setPage(self.page)

        self._build_ui()
        self._wire_signals()
        self.apply_settings()

        home = self._cfg.get("browser", "home_url", default="about:blank")
        self.load(home)

    # ---------- UI ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        bar = QHBoxLayout()
        self.btn_back = QPushButton("后退")
        self.btn_forward = QPushButton("前进")
        self.btn_reload = QPushButton("刷新")
        self.btn_home = QPushButton("主页")
        self.address = QLineEdit()
        self.address.setPlaceholderText("输入网址并回车访问，或输入关键词用必应搜索…")
        self.btn_go = QPushButton("转到")
        for b in (self.btn_back, self.btn_forward, self.btn_reload, self.btn_home):
            b.setFixedHeight(30)
        bar.addWidget(self.btn_back)
        bar.addWidget(self.btn_forward)
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.btn_home)
        bar.addWidget(self.address, 1)
        bar.addWidget(self.btn_go)
        # 拾取模式返回按钮（仅拾取时显示）
        self.btn_cancel_pick = QPushButton("← 返回")
        self.btn_cancel_pick.setStyleSheet("background:#6B7280;color:white;font-weight:bold;")
        self.btn_cancel_pick.setFixedHeight(30)
        self.btn_cancel_pick.setVisible(False)
        self.btn_cancel_pick.clicked.connect(self._cancel_pick_mode)
        bar.addWidget(self.btn_cancel_pick)
        root.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setMaximumHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        root.addWidget(self._view, 1)

        self.status = QLabel("就绪")
        self.status.setMaximumHeight(20)
        root.addWidget(self.status)

    def _wire_signals(self):
        self.btn_back.clicked.connect(self._view.back)
        self.btn_forward.clicked.connect(self._view.forward)
        self.btn_reload.clicked.connect(self._view.reload)
        self.btn_home.clicked.connect(self._go_home)
        self.btn_go.clicked.connect(self._navigate)
        self.address.returnPressed.connect(self._navigate)
        self._view.urlChanged.connect(self._on_url_changed)
        self._view.titleChanged.connect(self.title_changed.emit)
        self._view.loadStarted.connect(self._on_load_started)
        self._view.loadProgress.connect(self.progress.setValue)
        self._view.loadFinished.connect(self._on_load_finished)

    # ---------- 内部回调 ----------
    def _on_load_started(self):
        self.progress.setVisible(True)
        self.progress.setValue(0)

    def _on_url_changed(self, qurl):
        u = qurl.toString()
        self.address.setText(u)
        self.url_changed.emit(u)

    def _on_load_finished(self, ok):
        self.progress.setVisible(False)
        self.status.setText("加载完成" if ok else "加载失败")
        # 注入右键菜单定位器 JS（contextmenu 监听 + __rpaGetLocator 函数）
        if ok:
            self.page.runJavaScript(_INJECT_JS)
        self.load_finished.emit(ok, self._view.url().toString())

    def _navigate(self):
        text = self.address.text().strip()
        if not text:
            return
        if "://" not in text:
            if "." in text and " " not in text:
                text = "https://" + text
            else:
                text = "https://www.bing.com/search?q=" + text
        self.load(text)

    def _go_home(self):
        self.load(self._cfg.get("browser", "home_url", default="about:blank"))

    # ---------- 公共 API ----------
    def load(self, url: str):
        self.status.setText("加载中：" + url)
        self._view.setUrl(QUrl(url))

    def url(self) -> str:
        return self._view.url().toString()

    def run_js(self, script: str, callback=None):
        """运行页面内 JS。callback(result) 为异步回调。"""
        if callback is not None:
            self.page.runJavaScript(script, callback)
        else:
            self.page.runJavaScript(script)

    def view(self):
        return self._view

    def apply_settings(self):
        """根据 AppConfig 应用 UA / 代理 / JS / 图片 / SSL。"""
        ua = self._cfg.get("browser", "user_agent") or ""
        if ua:
            self.profile.setHttpUserAgent(ua)

        st = self._view.settings()
        st.setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            self._cfg.get("browser", "javascript_enabled", default=True),
        )
        st.setAttribute(
            QWebEngineSettings.WebAttribute.AutoLoadImages,
            self._cfg.get("browser", "load_images", default=True),
        )
        self.page.set_ignore_ssl(self._cfg.get("browser", "ignore_ssl_errors", default=False))

        proxy = self._cfg.get("browser", "proxy", default="") or ""
        if proxy:
            p = urlparse(proxy)
            host, port = p.hostname or "", p.port or 0
            if host:
                qp = QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, host, port)
                QNetworkProxy.setApplicationProxy(qp)
                log.info("已设置浏览器代理：%s:%s", host, port)
        else:
            QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))

        log.info("浏览器设置已应用（UA=%s, JS=%s, 图片=%s, 忽略SSL=%s）",
                 (ua[:30] + "…") if ua else "默认",
                 self._cfg.get("browser", "javascript_enabled", default=True),
                 self._cfg.get("browser", "load_images", default=True),
                 self._cfg.get("browser", "ignore_ssl_errors", default=False))

    def resize_browser(self, w: int, h: int):
        """调整浏览器视口尺寸（用于"调整浏览器窗口大小"设置）。"""
        self._view.resize(max(320, w), max(240, h))

    # ===================== 元素拾取模式 =====================
    def start_pick_mode(self, on_picked):
        """进入元素拾取模式。on_picked(css_selector) 在用户点击元素或返回后回调。
        css_selector 为空字符串表示用户点了返回（未拾取）。"""
        self._pick_callback = on_picked
        self._pick_done = False
        # 显示返回按钮
        self.btn_cancel_pick.setVisible(True)
        # 注入拾取 JS，注入完成（回调）后再启动轮询
        self.run_js(_PICK_JS, lambda _r: self._begin_pick_poll())
        log.info("已进入元素拾取模式，等待用户点击元素…")

    def _cancel_pick_mode(self):
        """用户点击"返回"按钮或按 ESC：清理页面拾取状态，标记为已取消。"""
        self.run_js(
            "if (window.__rpa_pick_cleanup) { try { window.__rpa_pick_cleanup(); } catch(e){} }"
            " window.__rpa_picked = '__CANCELLED__';"
        )

    def _begin_pick_poll(self):
        """JS 注入完成后启动轮询定时器。"""
        self._pick_timer = QTimer(self)
        self._pick_timer.timeout.connect(self._poll_pick)
        self._pick_timer.start(200)

    def _poll_pick(self):
        """轮询 JS 拾取结果：'__PICKING__'=进行中，其他字符串=有结果（选择或取消）。"""
        self.run_js(
            "(typeof window.__rpa_picked === 'string') ? window.__rpa_picked : '__PICKING__'",
            self._on_pick_result,
        )

    def _on_pick_result(self, result):
        # 已处理过结果（飞行中的旧请求回调），直接忽略
        if getattr(self, "_pick_done", True):
            return
        # '__PICKING__' 表示仍在进行中（null 也算）
        if result == '__PICKING__' or result is None:
            return
        # 有结果了（选择或返回），标记完成并清理 UI
        self._pick_done = True
        self._pick_timer.stop()
        self.btn_cancel_pick.setVisible(False)
        cb = getattr(self, "_pick_callback", None)
        self._pick_callback = None
        # '__CANCELLED__' → 空字符串（表示未拾取），其他 → CSS 选择器
        final = "" if result == "__CANCELLED__" else result
        log.info("元素拾取完成：%s", repr(final))
        if cb:
            cb(final if isinstance(final, str) else "")
