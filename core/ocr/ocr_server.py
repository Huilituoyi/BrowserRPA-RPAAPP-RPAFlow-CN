# -*- coding: utf-8 -*-
"""
OCR 服务：基于 ddddocr 的验证码识别 HTTP 服务。

提供两个接口：
  POST /v1/ocr    —— 文字验证码识别（上传字段 image）
  POST /v1/slide  —— 滑块缺口识别（上传字段 target + background）

特性：
  - 使用 werkzeug.serving.make_server 运行，支持精确 start/stop（原 app.run 无法优雅关闭）。
  - OCR / 滑块模型在首次启动时加载并复用，避免每次请求重复加载（加载较慢）。
  - 统计计数与请求历史线程安全（Lock 保护）。
  - 滑块结果归一化：不同 ddddocr 版本 slide_match 返回结构不同，统一输出标准格式。
"""
import threading
import time
from collections import deque
from datetime import datetime

from core.logging.logger import get_logger

log = get_logger("ocr.server")

# 默认配置
DEFAULT_PORT = 8848
LOCAL_HOST = "127.0.0.1"       # 仅本机访问
LAN_HOST = "0.0.0.0"           # 局域网可访问


def _to_native(v):
    """把 numpy 等非原生类型转成 JSON 可序列化的 Python 原生类型。"""
    try:
        import numpy as np  # noqa
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
    except Exception:
        pass
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    return v


def normalize_slide_result(result):
    """
    归一化 ddddocr 不同版本的 slide_match 返回值。

    常见格式差异：
      - {'target': [x1, y1, x2, y2]}      （多数版本，target 为矩形框）
      - {'target': [y1, x1, y2, x2]}      （部分旧版本顺序不同）
      - {'target_y': .., 'target': [...]} （含显式坐标字段）
      - numpy 类型数值                    （需转原生 int）

    统一输出：
      {
        'target_x': <滑块目标 x 坐标，最常用，即滑块需水平移动的距离>,
        'target_y': <滑块目标 y 坐标，可能为空>,
        'target':   [<归一化后的整数列表>],
        'raw':      <原始返回 dict，便于调试/兼容判断>,
      }
    """
    raw = _to_native(result)
    if not isinstance(raw, dict):
        raw = {"value": raw}
    out = {"raw": raw}

    # 优先采用显式的 target_x / target_y 字段
    if "target_x" in raw and isinstance(raw["target_x"], (int, float)):
        out["target_x"] = int(raw["target_x"])
    if "target_y" in raw and isinstance(raw["target_y"], (int, float)):
        out["target_y"] = int(raw["target_y"])

    target = raw.get("target")
    if isinstance(target, list) and target:
        t = [int(x) for x in target]
        out["target"] = t
        # 若无显式 target_x，从 target 列表推导（best-effort）
        if "target_x" not in out:
            # 通常 target[0] 为 x1（滑块左上角 x），即滑块需移动的水平距离
            out["target_x"] = t[0]
        if "target_y" not in out and len(t) >= 2:
            out["target_y"] = t[1]

    return out


class OcrServer:
    """管理 ddddocr HTTP 服务的生命周期、统计与历史。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._stats = {
            "ocr_ok": 0, "ocr_fail": 0,
            "slide_ok": 0, "slide_fail": 0,
        }
        self._history = deque(maxlen=100)
        self._ocr = None            # 文字识别实例（懒加载）
        self._slide_ocr = None      # 滑块识别实例（懒加载）
        self._server = None         # werkzeug BaseWSGIServer
        self._thread = None
        self._host = LOCAL_HOST
        self._port = DEFAULT_PORT
        self._flask = None  # 延迟到 start() 构建，避免未安装 flask 时整个应用无法启动

    # ---------- 模型加载 ----------
    def _ensure_models(self):
        """首次使用时加载模型（线程安全、只加载一次）。"""
        if self._ocr is None:
            import ddddocr
            log.info("正在加载 ddddocr 文字识别模型...")
            self._ocr = ddddocr.DdddOcr()
            log.info("文字识别模型加载完成")
        if self._slide_ocr is None:
            import ddddocr
            log.info("正在加载 ddddocr 滑块识别模型...")
            self._slide_ocr = ddddocr.DdddOcr(det=False, ocr=False)
            log.info("滑块识别模型加载完成")

    # ---------- 统计与历史 ----------
    def _add_history(self, record: dict):
        with self._lock:
            self._history.append(record)

    def _inc(self, key: str):
        with self._lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    def get_history(self) -> list:
        with self._lock:
            return list(self._history)

    def reset_stats(self):
        with self._lock:
            for k in self._stats:
                self._stats[k] = 0

    # ---------- Flask 路由 ----------
    def _build_flask(self):
        from flask import Flask, request, jsonify
        app = Flask(__name__)

        @app.route("/v1/ocr", methods=["POST"])
        def ocr_api():
            client_ip = request.remote_addr
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("收到 OCR 请求 - IP: %s", client_ip)

            if "image" not in request.files:
                self._inc("ocr_fail")
                self._add_history({"time": ts, "ip": client_ip, "type": "OCR",
                                   "status": "失败", "error": "未提供图片(image)"})
                return jsonify({"error": "No image provided"}), 400

            image_bytes = request.files["image"].read()
            start = time.time()
            try:
                self._ensure_models()
                result = self._ocr.classification(image_bytes)
                dur = round(time.time() - start, 2)
                self._inc("ocr_ok")
                self._add_history({"time": ts, "ip": client_ip, "type": "OCR",
                                   "status": "成功", "result": str(result),
                                   "duration": f"{dur}s"})
                return jsonify({"result": result})
            except Exception as e:
                dur = round(time.time() - start, 2)
                self._inc("ocr_fail")
                self._add_history({"time": ts, "ip": client_ip, "type": "OCR",
                                   "status": "失败", "error": str(e),
                                   "duration": f"{dur}s"})
                log.error("OCR 识别失败：%s", e, exc_info=True)
                return jsonify({"error": str(e)}), 500

        @app.route("/v1/slide", methods=["POST"])
        def slide_api():
            client_ip = request.remote_addr
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("收到滑块识别请求 - IP: %s", client_ip)

            if "target" not in request.files or "background" not in request.files:
                self._inc("slide_fail")
                self._add_history({"time": ts, "ip": client_ip, "type": "滑块",
                                   "status": "失败", "error": "缺少 target 或 background 图片"})
                return jsonify({"error": "Missing target/background images"}), 400

            target_bytes = request.files["target"].read()
            bg_bytes = request.files["background"].read()
            # simple_target=True 适合网页截图（无 alpha 通道），默认 True
            simple_target = request.form.get("simple_target", "true").lower() != "false"
            start = time.time()
            try:
                self._ensure_models()
                # 记录图片尺寸 + 保存调试图片
                import io, os
                from PIL import Image
                debug_dir = os.path.join("data", "debug_slide")
                os.makedirs(debug_dir, exist_ok=True)
                debug_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                try:
                    t_img = Image.open(io.BytesIO(target_bytes))
                    b_img = Image.open(io.BytesIO(bg_bytes))
                    log.info("滑块图片尺寸 - target: %s, background: %s, simple_target: %s",
                             t_img.size, b_img.size, simple_target)
                    t_img.save(os.path.join(debug_dir, f"{debug_ts}_target.png"))
                    b_img.save(os.path.join(debug_dir, f"{debug_ts}_background.png"))
                except Exception:
                    pass
                raw = self._slide_ocr.slide_match(target_bytes, bg_bytes, simple_target=simple_target)
                norm = normalize_slide_result(raw)
                dur = round(time.time() - start, 2)
                self._inc("slide_ok")
                log.info("滑块识别结果 - raw: %s, target_x: %s, 耗时: %ss",
                         raw, norm.get("target_x"), dur)
                self._add_history({"time": ts, "ip": client_ip, "type": "滑块",
                                   "status": "成功", "result": str(norm.get("target_x")),
                                   "detail": norm, "duration": f"{dur}s"})
                return jsonify(norm)
            except Exception as e:
                dur = round(time.time() - start, 2)
                self._inc("slide_fail")
                self._add_history({"time": ts, "ip": client_ip, "type": "滑块",
                                   "status": "失败", "error": str(e),
                                   "duration": f"{dur}s"})
                log.error("滑块识别失败：%s", e, exc_info=True)
                return jsonify({"error": str(e)}), 500

        @app.route("/v1/health", methods=["GET"])
        def health():
            return jsonify({"status": "ok"})

        self._flask = app

    # ---------- 服务生命周期 ----------
    def is_running(self) -> bool:
        return self._server is not None

    def start(self, host: str = LOCAL_HOST, port: int = DEFAULT_PORT):
        """启动 HTTP 服务。若已在运行则先返回当前地址。"""
        if self.is_running():
            raise RuntimeError("服务已在运行，请先停止")
        # 首次启动时构建 Flask 应用并 import 依赖（未安装 flask 时抛友好错误）
        if self._flask is None:
            self._build_flask()
        from werkzeug.serving import make_server
        self._host = host
        self._port = int(port)
        # make_server 会绑定端口，若端口被占用直接抛异常（由 UI 捕获提示）
        self._server = make_server(self._host, self._port, self._flask, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("OCR 服务已启动：%s:%s", self._host, self._port)
        self._add_history({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                           "ip": "localhost", "type": "系统",
                           "status": "服务启动", "result": f"{self._host}:{self._port}"})

    def stop(self):
        """停止 HTTP 服务并释放端口。"""
        if not self.is_running():
            return
        try:
            self._server.shutdown()
            if self._thread is not None:
                self._thread.join(timeout=5)
        except Exception as e:
            log.warning("停止 OCR 服务时出错：%s", e)
        finally:
            self._server = None
            self._thread = None
            log.info("OCR 服务已停止")
            self._add_history({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "ip": "localhost", "type": "系统",
                               "status": "服务停止", "result": ""})

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port
