# -*- coding: utf-8 -*-
"""
数据抓取器：在内置浏览器当前页面上，按"字段规则"或"表格选择器"提取数据。
通过注入 JS 提取，结果为 list[dict]，供 Excel 导出或 Oracle 写入使用。
"""
import json
from typing import List, Dict, Optional

from PySide6.QtCore import QEventLoop, QTimer

from core.browser.browser_widget import BrowserWidget
from core.logging.logger import get_logger

log = get_logger("scraper")


# ---------- 同步执行页面 JS（阻塞等待回调结果）----------
def run_js_sync(browser: BrowserWidget, script: str, timeout: float = 10.0):
    """同步运行 JS 并返回结果（超时返回 None）。"""
    holder = {"value": None}
    loop = QEventLoop()

    def cb(v):
        holder["value"] = v
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(timeout * 1000))

    browser.run_js(script, cb)
    loop.exec()
    return holder["value"]


# ---------- 按字段规则提取（列表模式）----------
_RULES_JS = r"""
(function(rules){
  var cols = rules.map(function(r){
    var els = document.querySelectorAll(r.selector);
    var vals = [];
    els.forEach(function(el){
      var v = r.attr ? el.getAttribute(r.attr) : (el.innerText || el.value || '');
      vals.push((v || '').toString().trim());
    });
    return {name: r.name, values: vals};
  });
  var n = 0;
  cols.forEach(function(c){ if(c.values.length > n) n = c.values.length; });
  var rows = [];
  for(var i = 0; i < n; i++){
    var row = {};
    cols.forEach(function(c){ row[c.name] = c.values[i] || ''; });
    rows.push(row);
  }
  return JSON.stringify(rows);
})(<RULES>);
"""

# ---------- 按 <table> 选择器提取 ----------
_TABLE_JS = r"""
(function(sel){
  var t = document.querySelector(sel || 'table');
  if(!t) return JSON.stringify({error:'未找到表格', headers:[], rows:[]});
  var headers = [];
  t.querySelectorAll('thead th, thead td').forEach(function(th){ headers.push(th.innerText.trim()); });
  if(headers.length === 0){
    var firstRow = t.querySelector('tr');
    if(firstRow){ firstRow.querySelectorAll('th, td').forEach(function(c){ headers.push(c.innerText.trim()); }); }
  }
  var rows = [];
  var bodyRows = t.querySelectorAll('tbody tr');
  if(bodyRows.length === 0) bodyRows = t.querySelectorAll('tr');
  bodyRows.forEach(function(tr){
    var cells = tr.querySelectorAll('td');
    if(cells.length === 0) return;
    var row = {};
    cells.forEach(function(c, i){ row[headers[i] || ('col_' + i)] = c.innerText.trim(); });
    rows.push(row);
  });
  return JSON.stringify({headers: headers, rows: rows});
})(<SEL>);
"""


def extract_by_rules(browser: BrowserWidget, rules: List[Dict]) -> List[Dict]:
    """
    rules: [{"name": "标题", "selector": "css选择器", "attr": "可选属性"}]
    返回 list[dict]，按最大列对齐为多行。
    """
    js = _RULES_JS.replace("<RULES>", json.dumps(rules, ensure_ascii=False))
    raw = run_js_sync(browser, js)
    if not raw:
        log.warning("按规则抓取未返回数据")
        return []
    try:
        rows = json.loads(raw)
        log.info("按规则抓取完成，共 %d 行", len(rows))
        return rows
    except Exception as e:
        log.error("解析抓取结果失败：%s", e)
        return []


def extract_table(browser: BrowserWidget, table_selector: str = "table") -> List[Dict]:
    """提取页面上的表格数据为 list[dict]。"""
    js = _TABLE_JS.replace("<SEL>", json.dumps(table_selector or "table"))
    raw = run_js_sync(browser, js)
    if not raw:
        log.warning("表格抓取未返回数据")
        return []
    try:
        data = json.loads(raw)
        if data.get("error"):
            log.warning(data["error"])
        rows = data.get("rows", [])
        log.info("表格抓取完成，共 %d 行", len(rows))
        return rows
    except Exception as e:
        log.error("解析表格结果失败：%s", e)
        return []


# ---------- 多页抓取：点击"下一页"按钮循环提取 ----------
_NEXT_PAGE_JS = r"""
(function(sel){
  var el = document.querySelector(sel);
  if(!el) return JSON.stringify({clicked: false});
  el.click();
  return JSON.stringify({clicked: true});
})(<SEL>);
"""


def _sleep_qt(seconds: float):
    """在 Qt 主线程中非阻塞等待（用事件循环，避免冻结界面导致 JS 回调无法处理）。"""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(seconds * 1000))
    loop.exec()


def _click_next_page(browser: BrowserWidget, selector: str, wait: float = 2.0) -> bool:
    """点击"下一页"元素并等待页面加载。返回是否成功点击到元素。"""
    js = _NEXT_PAGE_JS.replace("<SEL>", json.dumps(selector))
    raw = run_js_sync(browser, js)
    clicked = False
    try:
        clicked = bool(json.loads(raw).get("clicked")) if raw else False
    except Exception as e:
        log.warning("解析下一页点击结果失败：%s", e)
    if not clicked:
        return False
    # 等待页面加载（点击触发的翻页渲染）
    _sleep_qt(wait)
    return True


def extract_by_rules_paged(browser: BrowserWidget, rules: List[Dict],
                           next_page_selector: str, max_pages: int = 1) -> List[Dict]:
    """
    按字段规则多页抓取：抓完当前页后点击"下一页"元素，循环直到 max_pages 或找不到下一页。
    rules: [{"name": ..., "selector": ..., "attr": 可选}]
    返回所有页合并后的 list[dict]。
    """
    all_rows: List[Dict] = []
    page_no = 0
    while page_no < max_pages:
        page_no += 1
        rows = extract_by_rules(browser, rules)
        log.info("按规则抓取第 %d 页，得到 %d 行", page_no, len(rows))
        all_rows.extend(rows)
        # 已到最大页数或未配置翻页，不再点击下一页
        if page_no >= max_pages or not next_page_selector:
            break
        if not _click_next_page(browser, next_page_selector):
            log.info("未找到下一页元素(%s)，多页抓取结束", next_page_selector)
            break
    log.info("按规则多页抓取完成，共 %d 页 %d 行", page_no, len(all_rows))
    return all_rows


def extract_table_paged(browser: BrowserWidget, table_selector: str = "table",
                        next_page_selector: str = "", max_pages: int = 1) -> List[Dict]:
    """
    按表格选择器多页抓取：抓完当前页后点击"下一页"元素，循环直到 max_pages 或找不到下一页。
    返回所有页合并后的 list[dict]。
    """
    all_rows: List[Dict] = []
    page_no = 0
    while page_no < max_pages:
        page_no += 1
        rows = extract_table(browser, table_selector)
        log.info("表格抓取第 %d 页，得到 %d 行", page_no, len(rows))
        all_rows.extend(rows)
        if page_no >= max_pages or not next_page_selector:
            break
        if not _click_next_page(browser, next_page_selector):
            log.info("未找到下一页元素(%s)，多页抓取结束", next_page_selector)
            break
    log.info("表格多页抓取完成，共 %d 页 %d 行", page_no, len(all_rows))
    return all_rows


def extract_first_table(browser: BrowserWidget) -> List[Dict]:
    """便捷方法：抓取页面第一个表格。"""
    return extract_table(browser, "table")
