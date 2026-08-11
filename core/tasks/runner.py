# -*- coding: utf-8 -*-
"""
任务执行器：定时任务触发时，用 Playwright（headless）后台执行抓取或动作回放。
抓取结果可自动导出 Excel 与写入 Oracle（由任务 payload 控制）。
注意：本模块在 APScheduler 的后台线程中运行，会为每个任务新建 Playwright 实例。
"""
import json
import os
import random
import time
import traceback
from datetime import datetime
from typing import List, Dict

from config.settings import SCRAPE_DIR
from core.data.scraper import _RULES_JS, _TABLE_JS
from core.data.excel_exporter import export as export_excel
from core.logging.logger import get_logger
from .task_models import TaskDef

log = get_logger("runner")


def run_task(task: TaskDef, config):
    """按 task.kind 分发执行。"""
    kind = task.kind
    name = task.name or task.id   # 日志中用任务名
    task._log_name = name         # 供各子函数复用
    log.info("开始执行任务[%s]（类型=%s）", name, kind)
    if kind == "scrape_rules":
        run_scrape_rules(task, config)
    elif kind == "scrape_table":
        run_scrape_table(task, config)
    elif kind == "play_actions":
        run_play_actions(task, config)
    else:
        raise ValueError("未知任务类型：" + str(kind))


def _wait_page_ready(page, timeout_ms: int = 8000):
    """等待页面基本就绪：先等 domcontentloaded，再短等 networkidle（超时忽略）。
    比单纯等 networkidle 快很多，避免长轮询页面白等 15 秒。"""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    # 尝试短时间等 networkidle，超时就忽略（有长轮询的页面永远等不到）
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


def _timeout(config) -> int:
    return int(config.get("browser", "timeout_ms", default=30000))


def _new_page(task: TaskDef, config):
    """启动 Playwright，返回 (playwright, browser, context, page)。
    headless 由任务 payload 或全局配置决定（payload 优先）。
    任一步失败时清理已启动的资源，防止 Playwright/Chromium 子进程泄漏（R1）。"""
    from playwright.sync_api import sync_playwright
    pw = None
    browser = None
    context = None
    try:
        pw = sync_playwright().start()
        ua = config.get("browser", "user_agent", default="")
        # 任务自定义 > 全局默认
        if "headless" in task.payload:
            headless = task.payload["headless"]
        else:
            headless = config.get("runner", "headless", default=True)
        log.info("任务[%s] 启动浏览器（headless=%s）", task._log_name, headless)
        browser = pw.chromium.launch(headless=headless)
        ctx_kw = {}
        if ua:
            ctx_kw["user_agent"] = ua
        w = config.get("browser", "viewport_width", default=1280)
        h = config.get("browser", "viewport_height", default=800)
        ctx_kw["viewport"] = {"width": int(w), "height": int(h)}
        # 与内置浏览器共享 ignore_ssl_errors 配置
        if config.get("browser", "ignore_ssl_errors", default=False):
            ctx_kw["ignore_https_errors"] = True
        context = browser.new_context(**ctx_kw)
        page = context.new_page()
        return pw, browser, context, page
    except Exception:
        # 回收已启动的资源（close/stop 各自捕获异常，保证原异常继续上抛）
        for obj in (context, browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        raise


def _handle_output(task: TaskDef, rows: List[Dict], config):
    """按 payload 决定是否导出 Excel / 写 Oracle。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = (task.name or task.id).replace(" ", "_")

    if task.payload.get("export_excel") and rows:
        path = os.path.join(SCRAPE_DIR, f"{base}_{ts}.xlsx")
        try:
            export_excel(rows, path)
        except Exception as e:
            log.error("任务输出 Excel 失败：%s", e)

    tbl = task.payload.get("oracle_table")
    if tbl and rows:
        try:
            from core.data.oracle_client import OracleClient
            oc = OracleClient(config)
            oc.insert_many(tbl, rows)
            oc.disconnect()
            log.info("任务结果已写入 Oracle 表 %s（%d 行）", tbl, len(rows))
        except Exception as e:
            log.error("任务结果写入 Oracle 失败：%s", e)


# ---------- 多页抓取：后台点击"下一页" ----------
def _runner_click_next(page, selector: str, log_name: str) -> bool:
    """后台任务中点击"下一页"元素并等待 networkidle。返回是否成功点击。"""
    click_js = (
        "(function(sel){var el=document.querySelector(sel);"
        "if(!el){return false;}el.click();return true;})(%s)"
        % json.dumps(selector)
    )
    try:
        clicked = page.evaluate(click_js)
    except Exception as e:
        log.warning("任务[%s] 点击下一页失败：%s", log_name, e)
        return False
    if not clicked:
        log.info("任务[%s] 未找到下一页元素(%s)，多页抓取结束", log_name, selector)
        return False
    # 等待页面基本就绪（domcontentloaded 优先，不白等 networkidle）
    _wait_page_ready(page, timeout_ms=8000)
    return True


# ---------- 按规则抓取 ----------
def run_scrape_rules(task: TaskDef, config):
    url = task.payload.get("url", "")
    rules = task.payload.get("rules", [])
    # 翻页参数（任务面板可能未配置，给默认值：不翻页）
    next_sel = task.payload.get("next_page_selector", "")
    max_pages = int(task.payload.get("max_pages", 1) or 1)
    if not url or not rules:
        raise ValueError("抓取规则任务缺少 url 或 rules")
    pw, browser, ctx, page = _new_page(task, config)
    try:
        page.goto(url, timeout=_timeout(config))
        _wait_page_ready(page)
        js = _RULES_JS.replace("<RULES>", json.dumps(rules, ensure_ascii=False))
        all_rows: List[Dict] = []
        page_no = 0
        while page_no < max_pages:
            page_no += 1
            raw = page.evaluate(js)
            rows = json.loads(raw) if raw else []
            all_rows.extend(rows)
            log.info("任务[%s] 第 %d 页抓取到 %d 行", task._log_name, page_no, len(rows))
            # 已到最大页数或未配置翻页，停止
            if page_no >= max_pages or not next_sel:
                break
            if not _runner_click_next(page, next_sel, task._log_name):
                break
        log.info("任务[%s] 多页抓取完成，共 %d 页 %d 行", task._log_name, page_no, len(all_rows))
        _handle_output(task, all_rows, config)
    finally:
        ctx.close(); browser.close(); pw.stop()


# ---------- 抓取表格 ----------
def run_scrape_table(task: TaskDef, config):
    url = task.payload.get("url", "")
    sel = task.payload.get("table_selector", "table")
    # 翻页参数（任务面板可能未配置，给默认值：不翻页）
    next_sel = task.payload.get("next_page_selector", "")
    max_pages = int(task.payload.get("max_pages", 1) or 1)
    if not url:
        raise ValueError("表格抓取任务缺少 url")
    pw, browser, ctx, page = _new_page(task, config)
    try:
        page.goto(url, timeout=_timeout(config))
        _wait_page_ready(page)
        js = _TABLE_JS.replace("<SEL>", json.dumps(sel))
        all_rows: List[Dict] = []
        page_no = 0
        while page_no < max_pages:
            page_no += 1
            raw = page.evaluate(js)
            data = json.loads(raw) if raw else {}
            rows = data.get("rows", [])
            all_rows.extend(rows)
            log.info("任务[%s] 第 %d 页抓取表格到 %d 行", task._log_name, page_no, len(rows))
            if page_no >= max_pages or not next_sel:
                break
            if not _runner_click_next(page, next_sel, task._log_name):
                break
        log.info("任务[%s] 多页表格抓取完成，共 %d 页 %d 行", task._log_name, page_no, len(all_rows))
        _handle_output(task, all_rows, config)
    finally:
        ctx.close(); browser.close(); pw.stop()


# ---------- 回放录制动作 ----------
def run_play_actions(task: TaskDef, config):
    from core.recorder.action_models import Action
    raw_actions = task.payload.get("actions", [])
    actions = [Action.from_dict(a) for a in raw_actions]
    if not actions:
        raise ValueError("回放任务缺少 actions")
    pw, browser, ctx, page = _new_page(task, config)
    total = len(actions)

    # 每步间隔：优先用任务自定义，没有则用全局配置
    dmin = task.payload.get("step_delay_min")
    dmax = task.payload.get("step_delay_max")
    if dmin is None:
        dmin = config.get("runner", "step_delay_min", default=1.0)
    if dmax is None:
        dmax = config.get("runner", "step_delay_max", default=3.0)
    dmin, dmax = float(dmin), float(dmax)
    if dmax < dmin:
        dmax = dmin
    log.info("任务[%s] 开始回放，共 %d 步，每步间隔 %.1f~%.1f 秒", task._log_name, total, dmin, dmax)

    try:
        i = 0
        retry_counts: Dict[int, int] = {}  # {check_retry 所在 index: 已重试次数}
        while i < total:
            a = actions[i]
            desc = _action_desc(a)
            step_no = i + 1
            log.info("任务[%s] 第 %d/%d 步：%s", task._log_name, step_no, total, desc)

            # ---- 控制流：标记点（重试起点，不执行任何操作）----
            if a.type == "mark":
                i += 1
                continue

            # ---- 控制流：检查重试（检查元素是否存在+文本匹配，不通过则跳回上一个 mark）----
            if a.type == "check_retry":
                css = ""
                if a.selector:
                    css = a.selector.css or a.selector.id or ""
                max_retry = int(a.value or "3")
                expected_text = a.expected_text or ""
                passed = _check_element_exists(page, css)
                if passed and expected_text:
                    # 元素存在，还需校验文本
                    try:
                        actual_text = page.locator(css).first.inner_text(timeout=2000)
                        if expected_text not in actual_text:
                            passed = False
                            log.info("任务[%s] 检查重试：元素(%s)文本不匹配（期望含'%s'，实际'%s'）",
                                     task._log_name, css, expected_text,
                                     actual_text[:60])
                    except Exception:
                        passed = False
                        log.info("任务[%s] 检查重试：元素(%s)文本读取失败", task._log_name, css)
                if passed:
                    if expected_text:
                        log.info("任务[%s] 检查重试：成功标志(%s)已出现且文本匹配，继续往下执行",
                                 task._log_name, css)
                    else:
                        log.info("任务[%s] 检查重试：成功标志(%s)已出现，继续往下执行",
                                 task._log_name, css)
                    i += 1
                    continue
                # 未检测到 → 重试
                retry_counts[i] = retry_counts.get(i, 0) + 1
                if retry_counts[i] > max_retry:
                    raise RuntimeError(
                        "检查重试失败：已重试 %d 次仍未检测到成功标志元素(%s)，超出限制" % (max_retry, css))
                log.info("任务[%s] 检查重试：未检测到(%s)，第 %d/%d 次重试，跳回上一个标记点…",
                         task._log_name, css, retry_counts[i], max_retry)
                # 往前找最近的 mark
                mark_idx = None
                for j in range(i - 1, -1, -1):
                    if actions[j].type == "mark":
                        mark_idx = j
                        break
                if mark_idx is None:
                    raise RuntimeError(
                        "检查重试失败：在此 check_retry 之前找不到任何标记点(mark)，"
                        "请在它前面插入一个「标记点」动作")
                i = mark_idx
                continue

            # ---- 普通动作 ----
            try:
                _play_one(page, a, config, task=task)
            except Exception as e:
                log.error("任务[%s] 第 %d 步执行失败：%s", task._log_name, step_no, desc)
                log.error("异常类型：%s，异常信息：%s", type(e).__name__, e)
                log.error("完整错误栈：\n%s", traceback.format_exc())
                raise RuntimeError(
                    "第 %d/%d 步失败（%s）：%s: %s" % (step_no, total, desc, type(e).__name__, e)
                ) from e
            # 步骤之间的随机等待（最后一步不用等）
            if step_no < total and dmax > 0:
                wait = round(random.uniform(dmin, dmax), 2)
                log.info("任务[%s] 等待 %.2f 秒后执行下一步…", task._log_name, wait)
                time.sleep(wait)
            i += 1
        log.info("任务[%s] 动作回放完成（%d 步全部成功）", task._log_name, total)
    finally:
        ctx.close(); browser.close(); pw.stop()


def _action_desc(a) -> str:
    """生成动作的简短描述，用于日志。"""
    t = a.type
    s = a.selector
    if t == "navigate":
        return "导航 → " + str(a.url or "")
    sel_text = ""
    if s:
        sel_text = s.text or s.id or s.css or s.role or s.tag or ""
    if t == "click":
        return "点击 → " + sel_text
    if t == "fill":
        return "输入 → %s = %s" % (sel_text, a.value or "")
    if t == "select_option":
        return "选择 → %s = %s" % (sel_text, a.value or "")
    if t == "check":
        return "勾选 → %s (%s)" % (sel_text, a.value or "")
    if t == "press":
        return "按键 → " + str(a.value or "")
    if t == "hover":
        return "悬停 → " + sel_text
    if t == "scroll":
        return "滚动 → " + str(a.value or "")
    if t == "wait":
        return "等待 → %s 秒" % str(a.value or "1")
    if t == "fill_captcha":
        return "验证码识别 → 截图(%s) 填入 %s" % (a.image_selector or "", sel_text)
    if t == "slide_captcha":
        return "滑块验证码 → 小图(%s) 背景(%s) 拖拽 %s" % (
            a.image_selector or "", a.background_selector or "", sel_text)
    if t == "mark":
        return "标记点 → %s" % (a.value or "（重试起点）")
    if t == "check_retry":
        css = ""
        if s:
            css = s.css or s.id or ""
        return "检查重试 → 成功标志(%s) 最多重试 %s 次" % (css, a.value or "3")
    return str(t)


def _check_element_exists(page, css: str, timeout_ms: int = 3000) -> bool:
    """检查页面上的元素是否可见（短超时）。用于 check_retry 的条件判断。"""
    if not css:
        return False
    try:
        page.locator(css).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _locate(page, sel):
    """
    按唯一性优先级定位元素（不用 .first，避免点错元素）：
      id → CSS(含data-*等唯一属性) → role+name → text → 兜底 body
    录制时已确保这些选择器在页面上唯一（见 inject_js 的 bestUniqueCss）。
    """
    if not sel:
        return page.locator("body")

    # 1) 唯一 id（最强）— 自动补 # 前缀
    if sel.id:
        sid = sel.id.strip()
        if sid and not sid.startswith("#"):
            sid = "#" + sid
        return page.locator(sid)

    # 2) CSS 选择器（录制时已验证唯一，含 data-testid 等属性选择器）
    if sel.css:
        return page.locator(sel.css)

    # 3) role + accessible name（语义化定位，录制时 role 配 name/text 之一）
    if sel.role and sel.name:
        return page.get_by_role(sel.role, name=sel.name)

    # 4) 纯文本定位（录制时已尽量限制在 ≤40 字符的短文本）
    if sel.text:
        return page.get_by_text(sel.text)

    return page.locator("body")


def _play_one(page, a, config, task=None):
    t = a.type
    timeout = _timeout(config)
    if t == "navigate":
        # 导航后等待页面基本就绪（domcontentloaded 优先，不白等 networkidle）
        page.goto(a.url, timeout=timeout)
        _wait_page_ready(page, timeout_ms=timeout)
    elif t == "click":
        _locate(page, a.selector).click(timeout=timeout)
    elif t == "fill":
        _locate(page, a.selector).fill(a.value or "", timeout=timeout)
    elif t == "select_option":
        _locate(page, a.selector).select_option(a.value, timeout=timeout)
    elif t == "check":
        loc = _locate(page, a.selector)
        if a.value == "checked":
            loc.check(timeout=timeout)
        else:
            loc.uncheck(timeout=timeout)
    elif t == "press":
        page.locator("body").press(a.value or "Enter", timeout=timeout)
    elif t == "hover":
        _locate(page, a.selector).hover(timeout=timeout)
    elif t == "scroll":
        x, y = (0, 0)
        try:
            xy = json.loads(a.value or "{}")
            x, y = int(xy.get("x", 0)), int(xy.get("y", 0))
        except Exception:
            pass
        page.evaluate(f"window.scrollTo({x}, {y})")
    elif t == "wait":
        import time as _time
        seconds = float(a.value or "1")
        log.info("任务[%s] 等待 %.1f 秒…", getattr(task, "_log_name", ""), seconds)
        _time.sleep(seconds)
    elif t == "fill_captcha":
        import requests
        ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
        img_sel = a.image_selector or "img"
        log.info("任务[%s] 正在截取验证码图片(%s)…", getattr(task, "_log_name", ""), img_sel)
        img_bytes = page.locator(img_sel).screenshot()
        resp = requests.post(f"{ocr_url}/v1/ocr",
                             files={"image": ("captcha.png", img_bytes)}, timeout=30)
        captcha_text = resp.json().get("result", "")
        log.info("任务[%s] 验证码识别结果：%s", getattr(task, "_log_name", ""), captcha_text)
        _locate(page, a.selector).fill(captcha_text, timeout=timeout)
    elif t == "slide_captcha":
        import requests, base64, re, io as _io
        ocr_url = (a.value or "http://127.0.0.1:8848").rstrip("/")
        target_sel = a.image_selector or "img"
        bg_sel = a.background_selector or "img"
        log.info("任务[%s] 正在获取滑块图片(%s)和背景图(%s)…",
                 getattr(task, "_log_name", ""), target_sel, bg_sel)

        def _get_img_bytes(sel, hide_sel=None):
            """获取图片字节（保留 alpha 通道）。
            优先级：Canvas 提取（最准确） > data URI > 截图(隐藏底层+透明背景)。"""
            loc = page.locator(sel)
            log_prefix = f"任务[{getattr(task, '_log_name', '')}]"

            # 方式1：Canvas 提取——从浏览器已渲染的 <img> 直接拿原始像素（含 alpha）
            try:
                data_url = loc.evaluate("""el => {
                    if (el.tagName.toLowerCase() !== 'img') return null;
                    try {
                        var canvas = document.createElement('canvas');
                        canvas.width = el.naturalWidth || el.width;
                        canvas.height = el.naturalHeight || el.height;
                        var ctx = canvas.getContext('2d');
                        ctx.drawImage(el, 0, 0);
                        return canvas.toDataURL('image/png');
                    } catch(e) { return null; }  // CORS 会走到这里
                }""")
                if data_url and data_url.startswith("data:image"):
                    m = re.match(r"data:image/\w+;base64,(.+)", data_url, re.DOTALL)
                    if m:
                        data = base64.b64decode(m.group(1))
                        log.info("%s Canvas提取图片成功，%d字节", log_prefix, len(data))
                        return data
            except Exception as e:
                log.info("%s Canvas提取失败(%s)，尝试其他方式", log_prefix, e)

            # 方式2：data URI src（静态内嵌图片）
            try:
                tag = loc.evaluate("el => el.tagName.toLowerCase()")
                src = loc.get_attribute("src") if tag == "img" else None
                if src:
                    m = re.match(r"data:image/\w+;base64,(.+)", src, re.DOTALL)
                    if m:
                        data = base64.b64decode(m.group(1))
                        log.info("%s 从data URI获取图片，%d字节", log_prefix, len(data))
                        return data
            except Exception:
                pass

            # 方式3：截图（隐藏底层元素，让透明区域真正透明）
            hidden = False
            if hide_sel:
                try:
                    page.locator(hide_sel).evaluate("el => { el.style.visibility = 'hidden'; }")
                    hidden = True
                    log.info("%s 已隐藏底层元素(%s)以获取纯净截图", log_prefix, hide_sel)
                except Exception:
                    pass
            log.info("%s 使用截图(透明背景)获取图片", log_prefix)
            try:
                return loc.screenshot(omit_background=True)
            finally:
                if hidden:
                    try:
                        page.locator(hide_sel).evaluate("el => { el.style.visibility = ''; }")
                    except Exception:
                        pass

        # target 截图时隐藏 background，避免透明区域透出底层背景图
        target_bytes = _get_img_bytes(target_sel, hide_sel=bg_sel)
        bg_bytes = _get_img_bytes(bg_sel)
        resp = requests.post(f"{ocr_url}/v1/slide",
                             files={"target": ("t.png", target_bytes),
                                    "background": ("b.png", bg_bytes)},
                             timeout=30)
        distance = resp.json().get("target_x", 0)
        log.info("任务[%s] 滑块缺口距离：%s", getattr(task, "_log_name", ""), distance)
        # 模拟人工拖拽滑块
        slider = _locate(page, a.selector)
        box = slider.bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx + int(distance), cy, steps=20)
        page.mouse.up()
    elif t == "slide_right":
        # 滑动到最右侧：计算滑块按钮可移动的最大距离，直接拖到尽头
        slider = _locate(page, a.selector)
        box = slider.bounding_box()
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        # 获取滑块轨道宽度（父元素），算出可移动距离
        try:
            track_width = slider.evaluate("""el => {
                var track = el.parentElement;
                if (!track) return 0;
                return track.scrollWidth - el.offsetWidth;
            }""")
        except Exception:
            track_width = 0
        if not track_width or track_width < 10:
            # 回退：用一个较大的默认值
            track_width = 300
        distance = int(track_width)
        log.info("任务[%s] 滑动到最右侧，距离：%s", getattr(task, "_log_name", ""), distance)
        page.mouse.move(cx, cy)
        page.mouse.down()
        # 分段拖拽模拟人工
        steps = 25
        for i in range(1, steps + 1):
            page.mouse.move(cx + int(distance * i / steps), cy)
        page.mouse.up()
