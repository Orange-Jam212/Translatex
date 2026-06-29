import os
import sys
import json
import base64
import subprocess
import requests
from pathlib import Path
from time import sleep

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QFrame, QPushButton, QSystemTrayIcon, QMenu,
                               QLabel, QLineEdit, QTextEdit, QPlainTextEdit,
                               QInputDialog, QListWidget, QTreeWidget, QTreeWidgetItem, QScrollArea, QSplitter)
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QEvent, QObject, QTimer, QUrl, QMimeData, QPoint, QKeyCombination
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QFont, QKeySequence, QShortcut, QKeyEvent, QDrag
from PySide6.QtWebEngineWidgets import QWebEngineView

HAVE_GLOBAL_HOTKEY = False
try:
    import Quartz
    import AppKit
    HAVE_GLOBAL_HOTKEY = True
except ImportError:
    pass



# ==========================================
# 配置持久化
# ==========================================
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "api_key": "",
    "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "model_name": "qwen-vl-plus",
    "translate_prompt": (
        "你是学术翻译与 LaTeX 识别专家，请将图片中的英文文献译为中文。\n"
        "【排版与结构】\n"
        "1. 严格保留原文的视觉段落断点，绝对禁止合并段落。\n"
        "2. 遇到 Definition、Theorem、Lemma、Figure 等结构化区块，请将其标题【加粗】并独立成段，例如：**定义 4.7.6** 函数 $g(x)$ 是...\n"
        "3. 遇到图片、图表，请直接忽略，绝对不要输出任何类似 ![image] 的占位符或图片描述。\n"
        "【公式与输出规则】\n"
        "1. 行内公式严格使用 $...$，独立公式使用 $$...$$ 并换行。\n"
        "2. 公式内绝对禁止出现中文字符（使用 \\text{} 处理原有的英文文字）。\n"
        "3. 仅输出翻译结果，不添加任何解释或前缀。若图片无文字，仅回复【无法识别】。\n"
        "【翻译风格】\n"
        "- 意译专有名词，拆分英文长句，符合中文学术表达习惯。"
    ),
    "translate_subject": "金融学、计量经济学、应用统计学",
    "qa_prompt": (
        "你是学术助教。上文是翻译好的文献内容，请直接针对用户的问题进行回答。\n"
        "【关键要求】：\n"
        "1. 严禁复述全文，只针对本次提问的具体内容进行回答。\n"
        "2. 回答要精确、有逻辑推导，引用原文相关内容时仅摘取关键句。\n"
        "3. 公式请严格使用 $...$ (行内) 和 $$...$$ (独立)。\n"
        "4. ⚠️ 公式内部严禁使用 `#` 号（请用 \\text{count} 或 N 代替），绝对禁止夹杂中文字符。"
    ),
    "hotkey": "<cmd>+k",
    "toggle_hotkey": "<cmd>+`",
    "scholar_url": "https://scholar.google.com",
    "download_dir": ""
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(saved)
            return merged
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

_CMD_MOD = None

def _get_cmd_mod():
    global _CMD_MOD
    if _CMD_MOD is None:
        if sys.platform == "darwin":
            _CMD_MOD = Qt.ControlModifier
        else:
            _CMD_MOD = Qt.MetaModifier
    return _CMD_MOD

_SPECIAL_KEYS = {
    '`': Qt.Key_QuoteLeft, '-': Qt.Key_Minus, '=': Qt.Key_Equal,
    '[': Qt.Key_BracketLeft, ']': Qt.Key_BracketRight,
    '\\': Qt.Key_Backslash, ';': Qt.Key_Semicolon,
    "'": Qt.Key_Apostrophe, ',': Qt.Key_Comma,
    '.': Qt.Key_Period, '/': Qt.Key_Slash,
    ' ': Qt.Key_Space, '\t': Qt.Key_Tab,
}

def _parse_mods(spec):
    """解析快捷键字符串，返回 (cmd_mod, ctrl_mod, alt_mod, shift_mod, key_char)"""
    parts = spec.lower().replace("<", "").replace(">", "").strip().split("+")
    parts = [p.strip() for p in parts if p.strip()]
    needs_cmd = False
    needs_ctrl = False
    needs_alt = False
    needs_shift = False
    key_char = None
    for p in parts:
        if p in ("cmd", "command", "meta", "super", "win"):
            needs_cmd = True
        elif p in ("ctrl", "control"):
            needs_ctrl = True
        elif p in ("alt", "option"):
            needs_alt = True
        elif p == "shift":
            needs_shift = True
        elif p in _SPECIAL_KEYS:
            key_char = p
        elif len(p) == 1:
            key_char = p
    return needs_cmd, needs_ctrl, needs_alt, needs_shift, key_char

def _spec_to_qkeyseq(spec):
    """'<cmd>+<shift>+k' → QKeySequence"""
    needs_cmd, needs_ctrl, needs_alt, needs_shift, key_char = _parse_mods(spec)
    if key_char is None:
        return None

    mods = Qt.NoModifier
    if needs_cmd:
        mods |= _get_cmd_mod()
    if needs_ctrl:
        if sys.platform == "darwin":
            mods |= Qt.MetaModifier
        else:
            mods |= Qt.ControlModifier
    if needs_alt:
        mods |= Qt.AltModifier
    if needs_shift:
        mods |= Qt.ShiftModifier

    if key_char in _SPECIAL_KEYS:
        qt_key = _SPECIAL_KEYS[key_char]
    else:
        qt_key = Qt.Key(ord(key_char.upper()))

    combo = QKeyCombination(mods, qt_key)
    return QKeySequence(combo)

settings = load_settings()

STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_stats.json")

def record_api_call(text=""):
    import time
    stats = {"calls": [], "total_words": 0, "reads": 0}
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                stats = raw
            else:
                stats = {"calls": raw if isinstance(raw, list) else [], "total_words": 0, "reads": 0}
        except:
            pass
    words = len(text) if text else 0
    stats.setdefault("calls", [])
    stats["calls"].append({"t": int(time.time()), "w": words})
    stats["total_words"] = stats.get("total_words", 0) + words
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, ensure_ascii=False)

def get_api_stats():
    import time
    stats = {"calls": [], "total_words": 0, "reads": 0}
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                stats = {"calls": [{"t": s["t"], "w": 0} for s in raw], "total_words": 0, "reads": 0}
            else:
                stats = raw
        except:
            pass
    calls = stats.get("calls", [])
    total = len(calls)
    total_words = stats.get("total_words", 0)
    tokens_est = int(total_words * 0.7)
    first_call = time.strftime("%Y-%m-%d", time.localtime(calls[0]["t"])) if calls else ""
    last_call = time.strftime("%Y-%m-%d %H:%M", time.localtime(calls[-1]["t"])) if calls else ""
    reads = stats.get("reads", 0)

    now = time.time()
    hourly = [0] * 24
    hourly_labels = [f"{h}:00" for h in range(24)]
    for c in calls:
        t = c["t"]
        if now - t <= 86400:
            hour = int(time.strftime("%H", time.localtime(t)))
            hourly[hour] += 1

    return total, total_words, tokens_est, first_call, last_call, reads, hourly, hourly_labels

def record_literature_read():
    import time
    stats = {"calls": [], "total_words": 0, "reads": 0}
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                stats = raw
            else:
                stats = {"calls": raw if isinstance(raw, list) else [], "total_words": 0, "reads": 0}
        except:
            pass
    stats["reads"] = stats.get("reads", 0) + 1
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, ensure_ascii=False)

def _do_pdf(viewer, output_path, callback=None):
    viewer.page().printToPdf(output_path)
    def on_done(fp, ok):
        viewer.deleteLater()
        if ok and callback:
            callback(output_path)
    viewer.page().pdfPrintingFinished.connect(on_done)

# ==========================================
# 2. 核心工作线程：截屏与 API 请求
# ==========================================
class OCRWorker(QThread):
    chunk_signal = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def run(self):
        import copy, json
        s = copy.deepcopy(settings)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        img_path = os.path.join(current_dir, "latex_ocr_target.png")

        if os.path.exists(img_path):
            os.remove(img_path)

        print("🎯 唤醒十字准星...")
        subprocess.run(["screencapture", "-i", img_path], check=False)

        if not os.path.exists(img_path):
            print("❌ 截图被取消。")
            self.error_signal.emit("截图已取消")
            return

        try:
            with open(img_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            self.error_signal.emit(f"图片处理失败: {str(e)}")
            return

        payload = {
            "model": s["model_name"],
            "messages": [
                {
                    "role": "system",
                    "content": f"你是精通{s['translate_subject']}的顶尖学术翻译专家。"
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"{s['translate_prompt']}\n\n"
                                "【⚠️ 极其重要的高压指令】：\n"
                                "你现在的任务是**翻译**！你必须将图片中的所有英文内容翻译成通顺的**中文**（公式保持 LaTeX 不变）。\n"
                                "绝对禁止顺从 OCR 本能只提取英文原文！如果输出大段英文将视为严重错误！\n"
                                "请直接输出中文翻译结果，不要包含任何开头说明或修饰词。"
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 2000,
            "temperature": 0.2,
            "stream": True
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {s['api_key']}"
        }

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                print(f"🔄 请求大模型 API (第 {attempt} 次)...")
                response = requests.post(s["api_url"], json=payload, headers=headers, stream=True, timeout=30)
                if response.status_code == 200:
                    full_content = ""
                    for line in response.iter_lines():
                        if line:
                            line_str = line.decode('utf-8')
                            if line_str.startswith("data: "):
                                data_str = line_str[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data_json = json.loads(data_str)
                                    delta = data_json['choices'][0]['delta']
                                    if 'content' in delta:
                                        chunk = delta['content']
                                        full_content += chunk
                                        self.chunk_signal.emit(chunk)
                                except Exception:
                                    pass
                    if full_content:
                        record_api_call(full_content)
                        self.finished_signal.emit(full_content)
                        return
                if response.status_code < 500:
                    self.error_signal.emit(f"API 请求失败: HTTP {response.status_code}")
                    return
                sleep(2)
            except Exception:
                sleep(2)

        self.error_signal.emit("API 请求彻底失败，请检查网络或 Key。")


class ChatWorker(QThread):
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self, messages, parent=None):
        super().__init__(parent)
        self.messages = messages

    def run(self):
        import copy
        s = copy.deepcopy(settings)
        payload = {
            "model": s["model_name"],
            "messages": self.messages,
            "max_tokens": 2000,
            "temperature": 0.2
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {s['api_key']}"
        }

        try:
            print("🔄 请求对话 API...")
            response = requests.post(s["api_url"], json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                if content:
                    record_api_call(content)
                    self.finished_signal.emit(content)
                    return
        except Exception as e:
            pass

        self.error_signal.emit("对话请求失败，请检查网络或 Key。")


# ==========================================
# 3. 主控台设置窗口 (Apple 风格)
# ==========================================
def make_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #6E6E73; font-size: 12px; font-weight: 500; padding-left: 2px;")
    return lbl

def input_style():
    return """
        QLineEdit {
            background-color: #FFFFFF; color: #1D1D1F;
            border: 1px solid #D1D1D6; border-radius: 6px;
            padding: 8px 10px; font-size: 13px;
        }
        QLineEdit:focus { border: 1px solid #007AFF; }
    """

LIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "文献")
os.makedirs(LIT_DIR, exist_ok=True)
LIT_INDEX = os.path.join(LIT_DIR, "index.json")

def _repair_node(node):
    if isinstance(node, list):
        return {"files": node, "children": {}}
    if not isinstance(node, dict):
        return {"files": [], "children": {}}
    result = {"files": [], "children": {}}
    if "files" in node and isinstance(node["files"], list):
        fixed = []
        for f in node["files"]:
            if isinstance(f, dict):
                fixed.append(f)
            elif isinstance(f, str):
                fixed.append({"name": os.path.basename(f), "path": f})
        result["files"] = fixed
    else:
        for v in node.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                result["files"] = v
                break
    if "children" in node and isinstance(node["children"], dict):
        result["children"] = {k: _repair_node(v) for k, v in node["children"].items()}
    return result

def load_literature_index():
    if os.path.exists(LIT_INDEX):
        try:
            with open(LIT_INDEX, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "tree" not in data:
                tree = {}
                for fname, files in data.get("folders", {}).items():
                    tree[fname] = {"files": files, "children": {}}
                data = {"tree": tree}
            data["tree"] = {k: _repair_node(v) for k, v in data["tree"].items()}
            return data
        except:
            pass
    return {"tree": {"__root__": {"files": [], "children": {}}, "下载": {"files": [], "children": {}}}}

def save_literature_index(idx):
    with open(LIT_INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)

class LitListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.panel = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.panel and self.currentItem():
                self.panel._lit_rename()
                return
        super().keyPressEvent(event)


class ReaderTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.panel = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.panel and self.currentItem():
                self.panel._reader_rename()
                return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        if self.panel and self.panel._reader_start_drag():
            return
        super().startDrag(supported_actions)

    def dragEnterEvent(self, event):
        if self.panel and self.panel._reader_drag_enter(event):
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self.panel and self.panel._reader_drag_move(event):
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if self.panel and self.panel._reader_drop(event):
            return
        super().dropEvent(event)


class MainPanel(QWidget):
    settings_saved = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(800, 520)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame()
        self.container.setObjectName("mainPanel")
        self.container.setStyleSheet("""
            QFrame#mainPanel {
                background-color: rgba(255, 255, 255, 240);
                border-radius: 12px;
                border: 1px solid rgba(60, 60, 67, 20);
            }
        """)
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 0, 0)

        # 顶部标题栏
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(16, 10, 16, 6)
        top_bar.setSpacing(8)

        self.btn_close = QPushButton()
        self.btn_close.setFixedSize(12, 12)
        self.btn_close.setStyleSheet("QPushButton { background-color: #FF5F56; border-radius: 6px; border: none; } QPushButton:hover { background-color: #FF2E22; }")
        self.btn_close.clicked.connect(self.hide)

        self.btn_min = QPushButton()
        self.btn_min.setFixedSize(12, 12)
        self.btn_min.setStyleSheet("QPushButton { background-color: #FFBD2E; border-radius: 6px; border: none; } QPushButton:hover { background-color: #E6A213; }")
        self.btn_min.clicked.connect(self.showMinimized)

        self.btn_fullscreen = QPushButton()
        self.btn_fullscreen.setFixedSize(12, 12)
        self.btn_fullscreen.setStyleSheet("QPushButton { background-color: #28C840; border-radius: 6px; border: none; } QPushButton:hover { background-color: #1FA832; }")
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)

        self.btn_floating = QPushButton("切换悬浮窗")
        self.btn_floating.setFixedHeight(28)
        self.btn_floating.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.btn_floating.clicked.connect(self._switch_to_floating)

        # 左侧按钮区域（占位，与右侧平衡，实现导航居中）
        left_section = QHBoxLayout()
        left_section.setContentsMargins(0, 0, 0, 0)
        left_section.setSpacing(8)
        left_section.addWidget(self.btn_close)
        left_section.addWidget(self.btn_min)
        left_section.addWidget(self.btn_fullscreen)
        left_section.addWidget(self.btn_floating)
        left_section.addStretch()

        # 板块切换按钮
        self.nav_btns = {}
        nav_frame = QFrame()
        nav_frame.setStyleSheet("QFrame { background: transparent; }")
        nav_layout = QHBoxLayout(nav_frame)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(36)
        nav_layout.setAlignment(Qt.AlignCenter)

        for key, label in [("config", "设置"), ("literature", "文献搜索"), ("scholar", "文献管理"), ("reader", "文献阅读")]:
            btn = QPushButton(label)
            btn.setFixedHeight(28)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #E8E8ED; color: #1D1D1F;
                    border: none; border-radius: 6px; padding: 4px 14px;
                    font-size: 12px; font-weight: 500;
                }
                QPushButton:hover { background-color: #D1D1D6; }
                QPushButton:checked { background-color: #007AFF; color: #FFFFFF; }
            """)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._switch_tab(k))
            nav_layout.addWidget(btn)
            self.nav_btns[key] = btn

        # 右侧平衡区域
        right_section = QHBoxLayout()
        right_section.setContentsMargins(0, 0, 0, 0)
        right_section.addStretch()

        top_bar.addLayout(left_section, 1)
        top_bar.addWidget(nav_frame, 0, Qt.AlignCenter)
        top_bar.addLayout(right_section, 1)
        cl.addLayout(top_bar)

        # 堆叠内容区
        from PySide6.QtWidgets import QStackedWidget
        self.stack = QStackedWidget()
        cl.addWidget(self.stack, 1)

        # ---- 板块1: 设置 ----
        self._build_config_tab()
        # ---- 板块2: 文献管理 ----
        self._build_literature_tab()
        # ---- 板块3: 谷歌学术 ----
        self._build_scholar_tab()
        # ---- 板块4: 文献阅读 ----
        self._build_reader_tab()

        self.nav_btns["config"].setChecked(True)
        self.stack.setCurrentIndex(0)

        main_layout.addWidget(self.container)
        self.old_pos = None
        self._is_fullscreen = False
        self._pre_fullscreen_geometry = None
        self._refresh_stats()

    def _toggle_fullscreen(self):
        screen = QApplication.primaryScreen().geometry()
        if not self._is_fullscreen:
            self._pre_fullscreen_geometry = self.geometry()
            self.setGeometry(screen)
        else:
            if self._pre_fullscreen_geometry:
                self.setGeometry(self._pre_fullscreen_geometry)
        self._is_fullscreen = not self._is_fullscreen

    def _find_floating(self):
        if hasattr(self, '_floating_window'):
            try:
                if self._floating_window and self._floating_window.isVisible():
                    return self._floating_window
            except RuntimeError:
                self._floating_window = None
        for w in QApplication.topLevelWidgets():
            if isinstance(w, LatexAppWindow):
                self._floating_window = w
                return w
        fw = LatexAppWindow(None)
        self._floating_window = fw
        return fw

    def _trigger_global_screenshot(self):
        fw = self._find_floating()
        fw._trigger_screenshot()

    def _toggle_floating(self):
        fw = self._find_floating()
        if fw.isVisible():
            fw._ignore_deactivate = True
            fw.hide()
            self.showNormal()
            self.raise_()
            self.activateWindow()
        else:
            self.hide()
            fw._ignore_deactivate = True
            fw.showNormal()
            fw.raise_()
            fw.activateWindow()
            QTimer.singleShot(500, lambda: setattr(fw, '_ignore_deactivate', False))

    def _switch_to_floating(self):
        fw = self._find_floating()
        fw._ignore_deactivate = True
        self.hide()
        fw.showNormal()
        fw.raise_()
        fw.activateWindow()
        QTimer.singleShot(500, lambda: setattr(fw, '_ignore_deactivate', False))

    def _switch_tab(self, key):
        idx = list(self.nav_btns.keys()).index(key)
        for k, btn in self.nav_btns.items():
            btn.setChecked(k == key)
        self.stack.setCurrentIndex(idx)
        if key == "config":
            self._refresh_stats()
        self._lock_settings()

    # ===== 板块1: 配置 =====
    def _build_config_tab(self):
        from PySide6.QtWidgets import QSplitter, QGraphicsBlurEffect, QGridLayout
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background-color: #D1D1D6; }")

        left_wrapper = QWidget()
        left_grid = QGridLayout(left_wrapper)
        left_grid.setContentsMargins(0, 0, 0, 0)

        self.config_content = QWidget()
        left_layout = QVBoxLayout(self.config_content)
        left_layout.setContentsMargins(16, 8, 12, 16)
        left_layout.setSpacing(8)

        left_layout.addWidget(make_label("API Key"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setText(settings["api_key"])
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setStyleSheet(input_style())
        left_layout.addWidget(self.api_key_input)
        self.api_key_input.textChanged.connect(self._save)

        left_layout.addWidget(make_label("API URL"))
        self.api_url_input = QLineEdit()
        self.api_url_input.setText(settings["api_url"])
        self.api_url_input.setStyleSheet(input_style())
        left_layout.addWidget(self.api_url_input)
        self.api_url_input.textChanged.connect(self._save)

        left_layout.addWidget(make_label("模型名称"))
        self.model_input = QLineEdit()
        self.model_input.setText(settings["model_name"])
        self.model_input.setStyleSheet(input_style())
        left_layout.addWidget(self.model_input)
        self.model_input.textChanged.connect(self._save)

        left_layout.addWidget(make_label("专业领域"))
        self.translate_subject_input = QLineEdit()
        self.translate_subject_input.setText(settings["translate_subject"])
        self.translate_subject_input.setStyleSheet(input_style())
        self.translate_subject_input.setPlaceholderText("例如: 金融学、计量经济学")
        left_layout.addWidget(self.translate_subject_input)
        self.translate_subject_input.textChanged.connect(self._save)

        left_layout.addWidget(make_label("截图快捷键"))
        h = QHBoxLayout()
        self.hotkey_input = QLineEdit()
        self.hotkey_input.setText(settings["hotkey"])
        self.hotkey_input.setStyleSheet(input_style())
        self.hotkey_input.setPlaceholderText("<cmd>+<shift>+x")
        self.hotkey_input.textChanged.connect(self._save)
        h.addWidget(self.hotkey_input)
        self.record_btn = QPushButton("录制")
        self.record_btn.setFixedWidth(56)
        self.record_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; padding: 6px; font-size: 12px; } QPushButton:hover { background-color: #D1D1D6; }")
        self.record_btn.clicked.connect(lambda: self._record_hotkey(self.hotkey_input))
        h.addWidget(self.record_btn)
        left_layout.addLayout(h)

        left_layout.addWidget(make_label("面板切换快捷键"))
        h2 = QHBoxLayout()
        self.toggle_hotkey_input = QLineEdit()
        self.toggle_hotkey_input.setText(settings["toggle_hotkey"])
        self.toggle_hotkey_input.setStyleSheet(input_style())
        self.toggle_hotkey_input.setPlaceholderText("<cmd>+`")
        self.toggle_hotkey_input.textChanged.connect(self._save)
        h2.addWidget(self.toggle_hotkey_input)
        self.toggle_record_btn = QPushButton("录制")
        self.toggle_record_btn.setFixedWidth(56)
        self.toggle_record_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; padding: 6px; font-size: 12px; } QPushButton:hover { background-color: #D1D1D6; }")
        self.toggle_record_btn.clicked.connect(lambda: self._record_hotkey(self.toggle_hotkey_input))
        h2.addWidget(self.toggle_record_btn)
        left_layout.addLayout(h2)

        left_layout.addWidget(make_label("学术网站地址"))
        self.settings_scholar_url_input = QLineEdit()
        self.settings_scholar_url_input.setText(settings["scholar_url"])
        self.settings_scholar_url_input.setStyleSheet(input_style())
        self.settings_scholar_url_input.setPlaceholderText("https://scholar.google.com")
        left_layout.addWidget(self.settings_scholar_url_input)
        self.settings_scholar_url_input.textChanged.connect(self._save)

        left_layout.addWidget(make_label("下载文件夹路径"))
        self.settings_download_dir_input = QLineEdit()
        self.settings_download_dir_input.setText(settings["download_dir"])
        self.settings_download_dir_input.setStyleSheet(input_style())
        self.settings_download_dir_input.setPlaceholderText("留空则使用默认路径：<程序目录>/文献")
        left_layout.addWidget(self.settings_download_dir_input)
        self.settings_download_dir_input.textChanged.connect(self._save)

        left_layout.addStretch()

        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(12)
        self.config_content.setGraphicsEffect(self.blur_effect)

        self.config_overlay = QFrame()
        self.config_overlay.setStyleSheet("background-color: rgba(255, 255, 255, 0.25); border-radius: 12px;")
        overlay_layout = QVBoxLayout(self.config_overlay)
        overlay_layout.setAlignment(Qt.AlignCenter)

        self.btn_unlock = QPushButton("进入设置")
        self.btn_unlock.setFixedSize(130, 36)
        self.btn_unlock.setCursor(Qt.PointingHandCursor)
        self.btn_unlock.setStyleSheet("""
            QPushButton {
                background-color: #007AFF; color: #FFFFFF;
                border: none; border-radius: 18px; font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #005BBF; }
            QPushButton:pressed { background-color: #004499; }
        """)
        self.btn_unlock.clicked.connect(self._unlock_settings)
        overlay_layout.addWidget(self.btn_unlock)

        left_grid.addWidget(self.config_content, 0, 0)
        left_grid.addWidget(self.config_overlay, 0, 0)

        splitter.addWidget(left_wrapper)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 8, 16, 12)
        self.stats_browser = QWebEngineView()
        self.stats_browser.page().setBackgroundColor(Qt.transparent)
        right_layout.addWidget(self.stats_browser)

        self.stats_browser.titleChanged.connect(self._on_stats_title_changed)

        splitter.addWidget(right)
        splitter.setSizes([340, 460])
        layout.addWidget(splitter)
        self.stack.addWidget(tab)

        right.installEventFilter(self)
        splitter.installEventFilter(self)

        self._lock_settings()

    def _on_stats_title_changed(self, title):
        if title.startswith("lock_settings:"):
            self._lock_settings()

    def _record_hotkey(self, target_input):
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
        dialog = QDialog(self)
        dialog.setWindowTitle("录制快捷键")
        dialog.setFixedSize(340, 200)
        dialog.setStyleSheet("QDialog { background-color: #F5F5F7; border-radius: 10px; }")
        layout = QVBoxLayout(dialog)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(12)
        label = QLabel("请按下你要设置的快捷键组合...")
        label.setStyleSheet("color: #1D1D1F; font-size: 15px; font-weight: 500;")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        self._hotkey_preview = QLabel("")
        self._hotkey_preview.setStyleSheet("color: #007AFF; font-size: 22px; font-weight: 700;")
        self._hotkey_preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._hotkey_preview)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; padding: 8px 20px; font-size: 13px; } QPushButton:hover { background-color: #D1D1D6; }")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        self._hotkey_ok_btn = QPushButton("确认")
        self._hotkey_ok_btn.setEnabled(False)
        self._hotkey_ok_btn.setStyleSheet("QPushButton { background-color: #007AFF; color: #FFFFFF; border: none; border-radius: 6px; padding: 8px 20px; font-size: 13px; } QPushButton:hover { background-color: #005BBF; } QPushButton:disabled { background-color: #C7C7CC; }")
        self._hotkey_ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(self._hotkey_ok_btn)
        layout.addLayout(btn_layout)
        self._hotkey_result = None
        def key_handler(event):
            key = event.key()
            mods = event.modifiers()
            parts = []
            if sys.platform == "darwin":
                if mods & Qt.ControlModifier: parts.append('<cmd>')
                if mods & Qt.MetaModifier: parts.append('<ctrl>')
            else:
                if mods & Qt.ControlModifier: parts.append('<ctrl>')
                if mods & Qt.MetaModifier: parts.append('<cmd>')
            if mods & Qt.ShiftModifier: parts.append('<shift>')
            if mods & Qt.AltModifier: parts.append('<alt>')
            ks = QKeySequence(key)
            char = ks.toString().lower()
            if char and char not in ('meta', 'shift', 'ctrl', 'alt', ''):
                parts.append(char if len(char) == 1 else char)
            if parts:
                self._hotkey_result = "+".join(parts)
                self._hotkey_preview.setText(self._hotkey_result)
                self._hotkey_ok_btn.setEnabled(True)
            return True
        dialog.keyPressEvent = key_handler
        dialog.exec()
        if self._hotkey_result:
            target_input.setText(self._hotkey_result)

    def _refresh_stats(self):
        import json as _json
        total, total_words, tokens_est, first_call, last_call, reads, hourly, hl = get_api_stats()
        hours_json = _json.dumps(hourly)
        hl_json = _json.dumps(hl)
        html = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>'
            '<style>*{margin:0;padding:0}body{background:transparent;padding:16px;font-family:-apple-system,sans-serif;color:#1D1D1F}</style></head><body>'
            f'<div style="font-size:22px;font-weight:700;margin-bottom:20px">历史统计</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'
            f'<div style="background:#F5F5F7;border-radius:10px;padding:14px"><div style="font-size:11px;color:#6E6E73;margin-bottom:4px">翻译次数</div><div style="font-size:28px;font-weight:700">{total}</div></div>'
            f'<div style="background:#F5F5F7;border-radius:10px;padding:14px"><div style="font-size:11px;color:#6E6E73;margin-bottom:4px">阅读次数</div><div style="font-size:28px;font-weight:700">{reads} 篇</div></div>'
            f'<div style="background:#F5F5F7;border-radius:10px;padding:14px"><div style="font-size:11px;color:#6E6E73;margin-bottom:4px">产出词汇</div><div style="font-size:28px;font-weight:700">{total_words:,}</div></div>'
            f'<div style="background:#F5F5F7;border-radius:10px;padding:14px"><div style="font-size:11px;color:#6E6E73;margin-bottom:4px">预估 Token</div><div style="font-size:28px;font-weight:700">{tokens_est:,}</div></div>'
            f'</div>'
            f'<div style="margin-top:16px;font-size:12px;color:#6E6E73">首次调用: {first_call or "暂无"} | 最近调用: {last_call or "暂无"}</div>'
            f'<div style="font-size:13px;font-weight:600;color:#1D1D1F;margin:16px 0 4px">过去24小时调用</div>'
            f'<div id="h" style="height:120px"></div>'
            '<script>'
            'document.addEventListener("mousedown", function() { document.title = "lock_settings:" + Date.now(); });'
            f'var hEl=document.getElementById("h");if(hEl&&typeof echarts!=="undefined")echarts.init(hEl).setOption({{grid:{{left:30,right:8,top:8,bottom:20}},xAxis:{{type:"category",data:{hl_json},axisLabel:{{fontSize:9,rotate:45}}}},yAxis:{{type:"value",minInterval:1,axisLabel:{{fontSize:9}}}},series:[{{type:"bar",data:{hours_json},itemStyle:{{color:"#007AFF"}}}}]}});'
            '</script></body></html>'
        )
        self.stats_browser.setHtml(html)

    def _save(self):
        settings["api_key"] = self.api_key_input.text()
        settings["api_url"] = self.api_url_input.text()
        settings["model_name"] = self.model_input.text()
        settings["translate_subject"] = self.translate_subject_input.text()
        settings["hotkey"] = self.hotkey_input.text()
        settings["toggle_hotkey"] = self.toggle_hotkey_input.text()
        settings["scholar_url"] = self.settings_scholar_url_input.text()
        settings["download_dir"] = self.settings_download_dir_input.text()
        save_settings(settings)
        self.settings_saved.emit()
        if hasattr(self, 'scholar_url_input'):
            self.scholar_url_input.setText(settings["scholar_url"])

    def _lock_settings(self):
        if hasattr(self, 'config_content'):
            self.config_content.setEnabled(False)
            self.blur_effect.setEnabled(True)
            self.config_overlay.setVisible(True)

    def _unlock_settings(self):
        if hasattr(self, 'config_content'):
            self.config_content.setEnabled(True)
            self.blur_effect.setEnabled(False)
            self.config_overlay.setVisible(False)

    def changeEvent(self, event):
        if event.type() == QEvent.ActivationChange:
            if not self.isActiveWindow():
                self._lock_settings()
        elif event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self._lock_settings()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._lock_settings()
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            self._lock_settings()
        return super().eventFilter(obj, event)

    # ===== 板块2: 学术网站浏览器 =====
    def _build_literature_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(8, 6, 8, 6)
        nav_bar.setSpacing(6)

        self.scholar_back_btn = QPushButton("←")
        self.scholar_back_btn.setFixedSize(32, 28)
        self.scholar_back_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; font-size: 14px; } QPushButton:hover { background-color: #D1D1D6; } QPushButton:disabled { color: #C7C7CC; }")
        self.scholar_back_btn.clicked.connect(self._scholar_go_back)
        nav_bar.addWidget(self.scholar_back_btn)

        self.scholar_forward_btn = QPushButton("→")
        self.scholar_forward_btn.setFixedSize(32, 28)
        self.scholar_forward_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; font-size: 14px; } QPushButton:hover { background-color: #D1D1D6; } QPushButton:disabled { color: #C7C7CC; }")
        self.scholar_forward_btn.clicked.connect(self._scholar_go_forward)
        nav_bar.addWidget(self.scholar_forward_btn)

        self.scholar_refresh_btn = QPushButton("⟳")
        self.scholar_refresh_btn.setFixedSize(32, 28)
        self.scholar_refresh_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; font-size: 14px; } QPushButton:hover { background-color: #D1D1D6; }")
        self.scholar_refresh_btn.clicked.connect(lambda: self.scholar_browser.reload())
        nav_bar.addWidget(self.scholar_refresh_btn)

        self.scholar_url_input = QLineEdit()
        self.scholar_url_input.setText(settings.get("scholar_url", "https://scholar.google.com"))
        self.scholar_url_input.setStyleSheet("QLineEdit { background-color: #FFFFFF; color: #1D1D1F; border: 1px solid #D1D1D6; border-radius: 6px; padding: 4px 8px; font-size: 12px; } QLineEdit:focus { border: 1px solid #007AFF; }")
        self.scholar_url_input.returnPressed.connect(self._navigate_to_scholar)
        nav_bar.addWidget(self.scholar_url_input, 1)

        self.scholar_go_btn = QPushButton("前往")
        self.scholar_go_btn.setFixedHeight(28)
        self.scholar_go_btn.setStyleSheet("QPushButton { background-color: #007AFF; color: #FFF; border: none; border-radius: 6px; padding: 4px 12px; font-size: 12px; } QPushButton:hover { background-color: #005BBF; }")
        self.scholar_go_btn.clicked.connect(self._navigate_to_scholar)
        nav_bar.addWidget(self.scholar_go_btn)

        layout.addLayout(nav_bar)

        self.scholar_browser = QWebEngineView()
        self.scholar_browser.page().profile().downloadRequested.connect(self._on_download)
        self.scholar_browser.urlChanged.connect(self._on_scholar_url_changed)
        self.scholar_browser.page().createWindow = lambda window_type: self.scholar_browser.page()
        layout.addWidget(self.scholar_browser, 1)

        url = settings.get("scholar_url", "https://scholar.google.com")
        self.scholar_browser.setUrl(QUrl(url))

        self.stack.addWidget(tab)

    def _navigate_to_scholar(self):
        url = self.scholar_url_input.text().strip()
        if not url:
            return
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        from urllib.parse import urlparse
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https"):
            return
        self.scholar_url_input.setText(url)
        self.scholar_browser.setUrl(QUrl(url))

    def _on_scholar_url_changed(self, url):
        self.scholar_url_input.setText(url.toString())
        self.scholar_back_btn.setEnabled(self.scholar_browser.history().canGoBack())
        self.scholar_forward_btn.setEnabled(self.scholar_browser.history().canGoForward())

    def _scholar_go_back(self):
        if self.scholar_browser.history().canGoBack():
            self.scholar_browser.back()

    def _scholar_go_forward(self):
        if self.scholar_browser.history().canGoForward():
            self.scholar_browser.forward()

    def _on_download(self, download):
        folder = self._get_download_folder()
        os.makedirs(folder, exist_ok=True)
        suggested = download.suggestedFileName()
        path = os.path.join(folder, suggested)
        download.setPath(path)
        download.accept()
        download.finished.connect(lambda: self._record_download(suggested, path))

    def _record_download(self, name, path):
        idx = load_literature_index()
        tree = idx["tree"]
        if "下载" not in tree:
            tree["下载"] = {"files": [], "children": {}}
        if name not in [f["name"] for f in tree["下载"]["files"]]:
            tree["下载"]["files"].append({"name": name, "path": path})
            save_literature_index(idx)
            self._refresh_lit_tree()
            self._refresh_reader_file_list()

    # ===== 板块3: 文献管理 =====
    def _build_scholar_tab(self):
        from PySide6.QtWidgets import QListWidgetItem, QTreeWidgetItem, QTreeWidget, QFileDialog, QMenu, QInputDialog, QMessageBox

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 10, 16, 16)
        layout.setSpacing(8)

        # 导航栏 + 搜索
        nav_toolbar = QHBoxLayout()
        nav_toolbar.setSpacing(4)

        self.lit_back_btn = QPushButton("←")
        self.lit_back_btn.setFixedSize(32, 28)
        self.lit_back_btn.setEnabled(False)
        self.lit_back_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; font-size: 14px; } QPushButton:hover { background-color: #D1D1D6; } QPushButton:disabled { color: #C7C7CC; }")
        self.lit_back_btn.clicked.connect(self._lit_go_back)
        nav_toolbar.addWidget(self.lit_back_btn)

        self.lit_forward_btn = QPushButton("→")
        self.lit_forward_btn.setFixedSize(32, 28)
        self.lit_forward_btn.setEnabled(False)
        self.lit_forward_btn.setStyleSheet("QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none; border-radius: 6px; font-size: 14px; } QPushButton:hover { background-color: #D1D1D6; } QPushButton:disabled { color: #C7C7CC; }")
        self.lit_forward_btn.clicked.connect(self._lit_go_forward)
        nav_toolbar.addWidget(self.lit_forward_btn)

        self.lit_path_label = QLabel("下载")
        self.lit_path_label.setStyleSheet("color: #6E6E73; font-size: 12px; font-weight: 500; padding: 0 8px;")
        nav_toolbar.addWidget(self.lit_path_label)
        nav_toolbar.addStretch()

        self.lit_search_input = QLineEdit()
        self.lit_search_input.setPlaceholderText("搜索文献...")
        self.lit_search_input.setFixedWidth(180)
        self.lit_search_input.setStyleSheet("""
            QLineEdit {
                background-color: #FFF; color: #1D1D1F;
                border: 1px solid #D1D1D6; border-radius: 6px;
                padding: 4px 8px; font-size: 12px;
            }
            QLineEdit:focus { border: 1px solid #007AFF; }
        """)
        self.lit_search_input.returnPressed.connect(self._lit_search)
        nav_toolbar.addWidget(self.lit_search_input)

        layout.addLayout(nav_toolbar)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.lit_new_folder_btn = QPushButton("新建文件夹")
        self.lit_new_folder_btn.setFixedHeight(28)
        self.lit_new_folder_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none;
                          border-radius: 6px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.lit_new_folder_btn.clicked.connect(self._lit_new_folder)
        toolbar.addWidget(self.lit_new_folder_btn)

        self.lit_upload_btn = QPushButton("上传文献")
        self.lit_upload_btn.setFixedHeight(28)
        self.lit_upload_btn.setStyleSheet("""
            QPushButton { background-color: #007AFF; color: #FFF; border: none;
                          border-radius: 6px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #005BBF; }
        """)
        self.lit_upload_btn.clicked.connect(self._lit_upload)
        toolbar.addWidget(self.lit_upload_btn)

        toolbar.addStretch()

        self.lit_delete_btn = QPushButton("删除")
        self.lit_delete_btn.setFixedHeight(28)
        self.lit_delete_btn.setStyleSheet("""
            QPushButton { background-color: #FF5F56; color: #FFF; border: none;
                          border-radius: 6px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #FF2E22; }
        """)
        self.lit_delete_btn.clicked.connect(self._lit_delete)
        toolbar.addWidget(self.lit_delete_btn)

        self.lit_rename_btn = QPushButton("重命名")
        self.lit_rename_btn.setFixedHeight(28)
        self.lit_rename_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none;
                          border-radius: 6px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.lit_rename_btn.clicked.connect(self._lit_rename)
        toolbar.addWidget(self.lit_rename_btn)

        self.lit_move_btn = QPushButton("移动")
        self.lit_move_btn.setFixedHeight(28)
        self.lit_move_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F; border: none;
                          border-radius: 6px; padding: 4px 12px; font-size: 12px; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.lit_move_btn.clicked.connect(self._lit_move)
        toolbar.addWidget(self.lit_move_btn)

        layout.addLayout(toolbar)

        # 文献目录（图标视图）
        from PySide6.QtWidgets import QListWidget, QListView
        from PySide6.QtCore import QSize
        self.lit_list = LitListWidget()
        self.lit_list.panel = self
        self.lit_list.setViewMode(QListView.IconMode)
        self.lit_list.setResizeMode(QListView.Adjust)
        self.lit_list.setMovement(QListView.Static)
        self.lit_list.setGridSize(QSize(95, 110))
        self.lit_list.setIconSize(QSize(56, 56))
        self.lit_list.setWordWrap(True)
        self.lit_list.setSpacing(10)
        self.lit_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lit_list.customContextMenuRequested.connect(self._lit_context_menu)
        self.lit_list.itemDoubleClicked.connect(self._lit_item_double_clicked)
        self.lit_list.setStyleSheet("""
            QListWidget {
                background: #FFF; border: 1px solid #D1D1D6;
                border-radius: 6px; padding: 8px;
                outline: none;
            }
            QListWidget::item {
                padding: 4px; border-radius: 6px;
            }
            QListWidget::item:hover {
                background: #E8E8ED;
            }
            QListWidget::item:selected {
                background: #007AFF; color: #FFF;
            }
        """)
        layout.addWidget(self.lit_list, 1)

        self._lit_current_path = []
        self._lit_history = []
        self._lit_future = []
        self._lit_search_active = False
        self._refresh_lit_tree()
        self.stack.addWidget(tab)

    def _path_for_icon(self, path):
        from PySide6.QtGui import QIcon, QPixmap
        base = os.path.dirname(os.path.abspath(__file__))
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            pdf_icon = os.path.join(base, "pdf.png")
            if os.path.exists(pdf_icon):
                return QIcon(QPixmap(pdf_icon).scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return QIcon()

    def _folder_icon(self):
        from PySide6.QtGui import QIcon
        icon = QApplication.style().standardIcon(QApplication.style().StandardPixmap.SP_DirIcon)
        return icon

    def _truncate_text(self, text, max_chars=14):
        if len(text) <= max_chars:
            return text
        half = (max_chars - 3) // 2
        return text[:half] + "..." + text[-half:]

    def _make_list_item(self, text, data, icon=None):
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import QSize
        display = self._truncate_text(text)
        item = QListWidgetItem(display)
        item.setData(Qt.UserRole, data)
        item.setData(Qt.UserRole + 1, text)
        item.setSizeHint(QSize(95, 110))
        item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
        if icon is not None:
            item.setIcon(icon)
        return item

    def _lit_navigate_to(self, path_parts):
        if self._lit_search_active:
            self.lit_search_input.clear()
            self._lit_search_active = False
        self._lit_history.append(self._lit_current_path[:])
        self._lit_current_path = path_parts[:]
        self._lit_future.clear()
        self._lit_show_current()

    def _lit_go_back(self):
        if self._lit_history:
            self._lit_future.append(self._lit_current_path[:])
            if self._lit_search_active:
                self.lit_search_input.clear()
                self._lit_search_active = False
            self._lit_current_path = self._lit_history.pop()
            self._lit_show_current()

    def _lit_go_forward(self):
        if self._lit_future:
            self._lit_history.append(self._lit_current_path[:])
            self._lit_current_path = self._lit_future.pop()
            self._lit_show_current()

    def _update_nav_buttons(self):
        self.lit_back_btn.setEnabled(bool(self._lit_history))
        self.lit_forward_btn.setEnabled(bool(self._lit_future))

    def _lit_show_current(self):
        self.lit_list.clear()
        idx = load_literature_index()
        tree = idx["tree"]

        path_display = "/".join(self._lit_current_path) if self._lit_current_path else "根目录"
        self.lit_path_label.setText(path_display)
        self._update_nav_buttons()

        folder_icon = self._folder_icon()

        if not self._lit_current_path:
            for fname in sorted(tree.keys()):
                if fname.startswith("__"):
                    continue
                item = self._make_list_item(fname, ("folder", [fname]))
                if not folder_icon.isNull():
                    item.setIcon(folder_icon)
                self.lit_list.addItem(item)
            root_node = tree.get("__root__", {})
            for entry in root_node.get("files", []):
                name = entry.get("name", "未知")
                path = entry.get("path", "")
                fi = self._make_list_item(name, ("file", path, name))
                ico = self._path_for_icon(path)
                if not ico.isNull():
                    fi.setIcon(ico)
                self.lit_list.addItem(fi)
        else:
            parent_node = self._get_node(tree, self._lit_current_path)
            children = parent_node.get("children", {})
            for fname in sorted(children.keys()):
                fpath = self._lit_current_path + [fname]
                item = self._make_list_item(fname, ("folder", fpath))
                if not folder_icon.isNull():
                    item.setIcon(folder_icon)
                self.lit_list.addItem(item)
            for entry in parent_node.get("files", []):
                name = entry.get("name", "未知")
                path = entry.get("path", "")
                fi = self._make_list_item(name, ("file", path, name))
                ico = self._path_for_icon(path)
                if not ico.isNull():
                    fi.setIcon(ico)
                self.lit_list.addItem(fi)

    def _lit_item_double_clicked(self, item):
        if self._lit_search_active:
            return
        data = item.data(Qt.UserRole)
        if data and data[0] == "folder":
            fpath = data[1]
            self._lit_navigate_to(fpath)
        elif data and data[0] == "file":
            path = data[1]
            name = data[2]
            self._open_in_reader(path, name)

    def _open_in_reader(self, path, name):
        self._switch_tab("reader")
        if not self._reader_sidebar_visible:
            self._toggle_reader_sidebar()
        self._select_reader_file(self.reader_file_tree.invisibleRootItem(), path)

    def _select_reader_file(self, parent, target_path):
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, Qt.UserRole) == target_path:
                self.reader_file_tree.setCurrentItem(child)
                return True
            if self._select_reader_file(child, target_path):
                return True
        return False

    def _lit_search(self):
        query = self.lit_search_input.text().strip().lower()
        if not query:
            self._lit_search_active = False
            self._lit_current_path = self._lit_history[-1] if self._lit_history else []
            self._lit_show_current()
            return
        self._lit_search_active = True
        self.lit_list.clear()
        idx = load_literature_index()
        tree = idx["tree"]

        folder_icon = self._folder_icon()
        results = []

        def walk(node, path):
            if not isinstance(node, dict):
                return
            for entry in node.get("files", []):
                name = entry.get("name", "")
                if query in name.lower():
                    results.append(("file", entry.get("path", ""), name))
            for fname, child in node.get("children", {}).items():
                if isinstance(child, dict):
                    for entry in child.get("files", []):
                        name = entry.get("name", "")
                        if query in name.lower():
                            results.append(("file", entry.get("path", ""), name))
                walk(child, path + [fname])

        for fname, node in tree.items():
            walk(node, [fname])

        for rtype, rpath, rname in results:
            fi = self._make_list_item(rname, (rtype, rpath, rname))
            ico = self._path_for_icon(rpath)
            if not ico.isNull():
                fi.setIcon(ico)
            self.lit_list.addItem(fi)

        if not results:
            from PySide6.QtWidgets import QListWidgetItem
            no_item = QListWidgetItem("未找到匹配结果")
            no_item.setFlags(no_item.flags() & ~Qt.ItemIsSelectable)
            self.lit_list.addItem(no_item)

        self.lit_path_label.setText(f"搜索: {query}")

    def _refresh_lit_tree(self):
        self._lit_current_path = []
        self._lit_history.clear()
        self._lit_future.clear()
        self._lit_search_active = False
        self.lit_search_input.clear()
        self._lit_show_current()

    def _get_tree_node(self, tree, path):
        node = tree
        for name in path:
            if not isinstance(node, dict) or name not in node:
                return None
            children = node[name]
            if not isinstance(children, dict):
                return None
            children = children.get("children", {})
            node = children
        return node if isinstance(node, dict) else None

    def _get_node(self, tree, path):
        node = tree
        for i, name in enumerate(path):
            if i == 0:
                if name not in node:
                    node[name] = {"files": [], "children": {}}
                node = node[name]
            else:
                children = node.setdefault("children", {})
                if name not in children:
                    children[name] = {"files": [], "children": {}}
                node = children[name]
        return node

    def _collect_folder_files(self, node):
        files = []
        if not isinstance(node, dict):
            return files
        for entry in node.get("files", []):
            files.append(entry)
        for child in node.get("children", {}).values():
            files.extend(self._collect_folder_files(child))
        return files

    def _remove_file_from_tree(self, tree, name):
        if not isinstance(tree, dict):
            return
        for fname, node in list(tree.items()):
            if isinstance(node, dict):
                node["files"] = [f for f in node.get("files", []) if f.get("name") != name]
                self._remove_file_from_tree(node.get("children", {}), name)

    def _lit_new_folder(self):
        from PySide6.QtWidgets import QMessageBox
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name.startswith("__"):
            QMessageBox.warning(self, "无效名称", "文件夹名称不能以 __ 开头")
            return
        idx = load_literature_index()
        tree = idx["tree"]
        if not self._lit_current_path:
            if name not in tree:
                tree[name] = {"files": [], "children": {}}
        else:
            target_node = self._get_tree_node(tree, self._lit_current_path)
            if target_node is None:
                QMessageBox.warning(self, "错误", "当前文件夹路径无效")
                return
            if name not in target_node:
                target_node[name] = {"files": [], "children": {}}
        save_literature_index(idx)
        self._lit_show_current()
        self._refresh_reader_file_list()

    def _lit_upload(self):
        from PySide6.QtWidgets import QFileDialog, QInputDialog
        files, _ = QFileDialog.getOpenFileNames(self, "选择文献文件", "", "文献文件 (*.pdf *.djvu *.epub *.txt);;所有文件 (*)")
        if not files:
            return
        idx = load_literature_index()
        tree = idx["tree"]

        if not self._lit_current_path:
            folder_names = [k for k in tree.keys() if not k.startswith("__")]
            folder_names.insert(0, "根目录")
            target_str, ok = QInputDialog.getItem(self, "选择目标文件夹", "上传到哪个文件夹?", folder_names, 0, False)
            if not ok:
                return
            if target_str == "根目录":
                target_node = tree.setdefault("__root__", {"files": [], "children": {}})
            else:
                target_node = tree[target_str]
        else:
            target_node = self._get_node(tree, self._lit_current_path)

        import shutil
        for src_path in files:
            name = os.path.basename(src_path)
            dest_path = os.path.join(LIT_DIR, name)
            counter = 1
            while os.path.exists(dest_path):
                base, ext = os.path.splitext(name)
                dest_path = os.path.join(LIT_DIR, f"{base}_{counter}{ext}")
                counter += 1
            try:
                shutil.copy2(src_path, dest_path)
            except Exception as e:
                QMessageBox.warning(self, "上传失败", f"文件 {name} 上传失败: {e}")
                continue
            if name not in [f["name"] for f in target_node.get("files", [])]:
                target_node.setdefault("files", []).append({"name": os.path.basename(dest_path), "path": dest_path})
        save_literature_index(idx)
        self._lit_show_current()
        self._refresh_reader_file_list()

    def _lit_delete(self):
        selected = self.lit_list.currentItem()
        if not selected:
            return
        data = selected.data(Qt.UserRole)
        if not data:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "确认删除", "确定彻底删除选中的项目及其笔记?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        idx = load_literature_index()
        tree = idx["tree"]
        if data[0] == "folder":
            fpath = data[1]
            fname = fpath[-1]
            if len(fpath) == 1:
                parent = tree
                target_node = parent.get(fname, {})
                all_files = self._collect_folder_files(target_node)
                for entry in all_files:
                    p = entry.get("path", "")
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except:
                            pass
                    self._delete_associated_files(p)
                parent.pop(fname, None)
            else:
                parent_node = self._get_node(tree, fpath[:-1])
                children = parent_node.get("children", {})
                target_node = children.get(fname, {})
                all_files = self._collect_folder_files(target_node)
                for entry in all_files:
                    p = entry.get("path", "")
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except:
                            pass
                    self._delete_associated_files(p)
                children.pop(fname, None)
        elif data[0] == "file":
            path = data[1]
            name = data[2]
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
            self._delete_associated_files(path)
            self._remove_file_from_tree(tree, name)
        save_literature_index(idx)
        self._lit_show_current()
        self._refresh_reader_file_list()

    def _lit_rename(self):
        selected = self.lit_list.currentItem()
        if not selected:
            return
        data = selected.data(Qt.UserRole)
        if not data:
            return

        orig_name = selected.data(Qt.UserRole + 1) or selected.text()
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        if data[0] == "file":
            base_name, ext = os.path.splitext(orig_name)
        else:
            base_name, ext = orig_name, ""

        new_base, ok = QInputDialog.getText(self, "重命名", "新名称:", text=base_name)
        if not ok or not new_base.strip():
            return
        new_name = new_base.strip() + ext

        idx = load_literature_index()
        tree = idx["tree"]

        if data[0] == "folder":
            fpath = data[1]
            if len(fpath) == 1:
                if fpath[0] in tree and new_name not in tree:
                    tree[new_name] = tree.pop(fpath[0])
            else:
                parent_node = self._get_node(tree, fpath[:-1])
                children = parent_node.setdefault("children", {})
                old_fname = fpath[-1]
                if old_fname in children and new_name not in children:
                    children[new_name] = children.pop(old_fname)
        elif data[0] == "file":
            old_name = data[2]
            old_path = data[1]
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
                self._move_associated_files(old_path, new_path)
            except Exception as e:
                QMessageBox.warning(self, "重命名失败", str(e))
                return

            def rename_in_tree(node):
                if not isinstance(node, dict):
                    return False
                for f in node.get("files", []):
                    if f.get("name") == old_name:
                        f["name"] = new_name
                        f["path"] = new_path
                        return True
                for child in node.get("children", {}).values():
                    if rename_in_tree(child):
                        return True
                return False

            for top_node in tree.values():
                if rename_in_tree(top_node):
                    break

            if getattr(self, '_current_pdf_path', None) == old_path:
                self._current_pdf_path = new_path

        save_literature_index(idx)
        self._lit_show_current()
        self._refresh_reader_file_list()

    def _lit_move(self):
        selected = self.lit_list.currentItem()
        if not selected:
            return
        data = selected.data(Qt.UserRole)
        if not data or data[0] != "file":
            return
        path = data[1]
        name = data[2]
        idx = load_literature_index()
        tree = idx["tree"]

        flat_folders = []
        def walk(node, prefix=""):
            if not isinstance(node, dict):
                return
            for fname in node:
                if fname.startswith("__"):
                    continue
                flat_folders.append(prefix + fname)
                child = node[fname]
                if isinstance(child, dict):
                    walk(child.get("children", {}), prefix + fname + "/")
        walk(tree)

        from PySide6.QtWidgets import QInputDialog
        target_str, ok = QInputDialog.getItem(self, "移动文献", "移动到哪个文件夹?", flat_folders, 0, False)
        if not ok:
            return
        target_parts = target_str.split("/")
        target_node = self._get_node(tree, target_parts)
        if target_node is None:
            QMessageBox.warning(self, "移动失败", "目标文件夹不存在")
            return

        self._remove_file_from_tree(tree, name)
        existing = target_node.get("files", [])
        if not any((isinstance(f, dict) and f.get("name") == name) or f == name for f in existing):
            target_node.setdefault("files", []).append({"name": name, "path": path})
        save_literature_index(idx)
        self._lit_show_current()
        self._refresh_reader_file_list()

    def _lit_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        selected = self.lit_list.itemAt(pos)
        if not selected:
            menu.addAction("新建文件夹", self._lit_new_folder)
            menu.addAction("刷新", self._refresh_lit_tree)
        else:
            data = selected.data(Qt.UserRole)
            if data and data[0] == "folder":
                menu.addAction("新建文件夹", self._lit_new_folder)
                menu.addAction("重命名", self._lit_rename)
                menu.addAction("删除文件夹", self._lit_delete)
            elif data and data[0] == "file":
                menu.addAction("重命名", self._lit_rename)
                menu.addAction("移动", self._lit_move)
                menu.addAction("下载到本地", self._lit_download)
                menu.addAction("删除", self._lit_delete)
        menu.exec(self.lit_list.viewport().mapToGlobal(pos))

    def _lit_download(self):
        selected = self.lit_list.currentItem()
        if not selected:
            return
        data = selected.data(Qt.UserRole)
        if not data or data[0] != "file":
            return
        src_path = data[1]
        if not os.path.exists(src_path):
            return
        from PySide6.QtWidgets import QFileDialog
        name = data[2]
        dest, _ = QFileDialog.getSaveFileName(self, "下载到本地", name)
        if dest:
            import shutil
            try:
                shutil.copy2(src_path, dest)
            except Exception as e:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "下载失败", str(e))


    def _build_reader_tab(self):
        from PySide6.QtWidgets import QSplitter, QListWidgetItem, QFileSystemModel, QTreeWidget, QTreeWidgetItem

        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(1)
        self.main_splitter.setStyleSheet("QSplitter::handle { background-color: #D1D1D6; }")

        # ===== 左侧：文献目录（可调宽度，默认隐藏） =====
        self._reader_sidebar_visible = False
        left_panel = QWidget()
        left_panel.setMinimumWidth(150)
        left_panel.setVisible(False)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(4)

        sidebar_header = QHBoxLayout()
        sidebar_header.addWidget(make_label("文献目录"))
        sidebar_header.addStretch()
        self.reader_sidebar_toggle = QPushButton("✕")
        self.reader_sidebar_toggle.setFixedSize(20, 20)
        self.reader_sidebar_toggle.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #6E6E73;
                border: none; border-radius: 4px; font-size: 11px;
            }
            QPushButton:hover { background-color: #E8E8ED; color: #1D1D1F; }
        """)
        self.reader_sidebar_toggle.clicked.connect(self._toggle_reader_sidebar)

        self.reader_new_folder_btn = QPushButton("+")
        self.reader_new_folder_btn.setFixedSize(20, 20)
        self.reader_new_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #6E6E73;
                border: none; border-radius: 4px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #E8E8ED; color: #1D1D1F; }
        """)
        self.reader_new_folder_btn.clicked.connect(self._reader_new_folder)
        sidebar_header.addWidget(self.reader_new_folder_btn)
        sidebar_header.addWidget(self.reader_sidebar_toggle)
        left_layout.addLayout(sidebar_header)

        left_v_splitter = QSplitter(Qt.Vertical)
        left_v_splitter.setHandleWidth(4)
        left_v_splitter.setStyleSheet("QSplitter::handle { background-color: transparent; } QSplitter::handle:hover { background-color: #D1D1D6; }")

        self.reader_file_tree = ReaderTreeWidget()
        self.reader_file_tree.panel = self
        from PySide6.QtWidgets import QAbstractItemView
        self.reader_file_tree.setDragEnabled(True)
        self.reader_file_tree.setAcceptDrops(True)
        self.reader_file_tree.setDropIndicatorShown(True)
        self.reader_file_tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

        self.reader_file_tree.setHeaderHidden(True)
        self.reader_file_tree.setRootIsDecorated(True)
        self.reader_file_tree.setStyleSheet("""
            QTreeWidget {
                background: #F5F5F7;
                border: 1px solid #D1D1D6;
                border-radius: 8px;
                padding: 4px;
                outline: none;
                show-decoration-selected: 1;
            }
            QTreeWidget::item {
                padding: 8px 10px;
                margin: 2px 0px;
                border-radius: 6px;
                border: 1px solid transparent;
            }
            QTreeWidget::item:hover {
                background-color: #FFFFFF;
                border: 1px solid #E5E5EA;
                border-bottom: 2px solid #D1D1D6;
            }
            QTreeWidget::item:selected {
                background-color: #007AFF;
                color: #FFFFFF;
                border: 1px solid #007AFF;
                border-bottom: 1px solid #005BBF;
            }
            QTreeWidget::item:has-children {
                font-weight: 600;
            }
            QTreeWidget::branch { background: transparent; }
            QTreeWidget::branch:hover { background: transparent; }
            QTreeWidget::branch:selected {
                background-color: #DCDCDC;
            }
        """)
        self.reader_file_tree.currentItemChanged.connect(self._on_reader_file_selected)

        self.reader_file_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.reader_file_tree.customContextMenuRequested.connect(self._reader_context_menu)
        left_v_splitter.addWidget(self.reader_file_tree)

        notes_container = QWidget()
        notes_layout = QVBoxLayout(notes_container)
        notes_layout.setContentsMargins(0, 4, 0, 0)
        notes_layout.setSpacing(4)

        notes_header = QHBoxLayout()
        notes_header.addWidget(make_label("笔记"))
        notes_header.addStretch()
        self.notes_new_folder_btn = QPushButton("+")
        self.notes_new_folder_btn.setFixedSize(20, 20)
        self.notes_new_folder_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #6E6E73;
                border: none; border-radius: 4px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background-color: #E8E8ED; color: #1D1D1F; }
        """)
        self.notes_new_folder_btn.clicked.connect(self._notes_new_folder)
        notes_header.addWidget(self.notes_new_folder_btn)
        notes_layout.addLayout(notes_header)

        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QAbstractItemView
        self.reader_note_list = QTreeWidget()
        self.reader_note_list.setHeaderHidden(True)
        self.reader_note_list.setRootIsDecorated(True)
        self.reader_note_list.setDragEnabled(True)
        self.reader_note_list.setAcceptDrops(True)
        self.reader_note_list.setDropIndicatorShown(True)
        self.reader_note_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.reader_note_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.reader_note_list.customContextMenuRequested.connect(self._note_context_menu)
        self.reader_note_list.itemDoubleClicked.connect(self._note_double_clicked)
        self.reader_note_list.itemClicked.connect(self._note_item_clicked)
        self._note_click_timer = None
        self._note_click_item = None
        self.reader_note_list.setStyleSheet("""
            QTreeWidget {
                background: #F5F5F7;
                border: 1px solid #D1D1D6;
                border-radius: 8px;
                padding: 4px;
                outline: none;
                show-decoration-selected: 1;
            }
            QTreeWidget::item {
                padding: 8px 10px;
                margin: 2px 0px;
                border-radius: 6px;
                color: #48484A;
                border: 1px solid transparent;
            }
            QTreeWidget::item:hover {
                background-color: #FFFFFF;
                border: 1px solid #E5E5EA;
                border-bottom: 2px solid #D1D1D6;
                color: #1D1D1F;
            }
            QTreeWidget::item:selected {
                background-color: #007AFF;
                color: #FFFFFF;
                border: 1px solid #007AFF;
                border-bottom: 1px solid #005BBF;
            }
            QTreeWidget::branch { background: transparent; }
            QTreeWidget::branch:hover { background: transparent; }
            QTreeWidget::branch:selected {
                background-color: #DCDCDC;
            }
        """)
        notes_layout.addWidget(self.reader_note_list)

        # 拖拽移动后自动保存文件夹归属状态
        self.reader_note_list.model().rowsInserted.connect(lambda *a: self._sync_notes_tree_state())

        left_v_splitter.addWidget(notes_container)
        left_v_splitter.setSizes([300, 200])

        left_layout.addWidget(left_v_splitter, 1)

        self.knowledge_btn = QPushButton("📋 笔记功能")
        self.knowledge_btn.setFixedHeight(28)
        self.knowledge_btn.setStyleSheet("""
            QPushButton {
                background-color: #AF52DE; color: #FFF;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background-color: #8944B8; }
            QPushButton:disabled { background-color: #C7C7CC; }
        """)
        self.knowledge_btn.clicked.connect(self._show_notes_dialog)
        left_layout.addWidget(self.knowledge_btn)

        self.main_splitter.addWidget(left_panel)

        # ===== 中间：文献阅读器 =====
        reader_view_widget = QWidget()
        reader_view_layout = QVBoxLayout(reader_view_widget)
        reader_view_layout.setContentsMargins(4, 8, 4, 8)

        reader_view_header = QHBoxLayout()
        self.reader_toggle_sidebar_btn = QPushButton("☰ 目录")
        self.reader_toggle_sidebar_btn.setFixedSize(60, 24)
        self.reader_toggle_sidebar_btn.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; font-size: 11px; font-weight: 500;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.reader_toggle_sidebar_btn.clicked.connect(self._toggle_reader_sidebar)
        reader_view_header.addWidget(self.reader_toggle_sidebar_btn)
        self.reader_screenshot_btn = QPushButton("截图翻译")
        self.reader_screenshot_btn.setFixedHeight(24)
        self.reader_screenshot_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF; color: #FFF;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 11px; font-weight: 500;
            }
            QPushButton:hover { background-color: #005BBF; }
        """)
        self.reader_screenshot_btn.clicked.connect(self._reader_take_screenshot)
        reader_view_header.addWidget(self.reader_screenshot_btn)

        self.reader_toggle_pins_btn = QPushButton("隐藏笔记")
        self.reader_toggle_pins_btn.setFixedHeight(24)
        self.reader_toggle_pins_btn.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 11px; font-weight: 500;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.reader_toggle_pins_btn.clicked.connect(self._toggle_pins_visibility)
        reader_view_header.addWidget(self.reader_toggle_pins_btn)
        reader_view_header.addStretch()
        page_label = QLabel("页码")
        page_label.setStyleSheet("color: #6E6E73; font-size: 11px;")
        reader_view_header.addWidget(page_label)
        self.reader_page_input = QLineEdit()
        self.reader_page_input.setFixedWidth(42)
        self.reader_page_input.setStyleSheet("""
            QLineEdit { background-color: #FFF; color: #1D1D1F; border: 1px solid #D1D1D6;
                border-radius: 4px; padding: 2px 4px; font-size: 11px; }
            QLineEdit:focus { border: 1px solid #007AFF; }
        """)
        self.reader_page_input.setPlaceholderText("1")
        self.reader_page_input.returnPressed.connect(self._reader_jump_page)
        reader_view_header.addWidget(self.reader_page_input)
        go_btn = QPushButton("Go")
        go_btn.setFixedSize(28, 20)
        go_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 4px; font-size: 10px; font-weight: 600; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        go_btn.clicked.connect(self._reader_jump_page)
        reader_view_header.addWidget(go_btn)

        # 缩放控制
        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedSize(24, 20)
        self.zoom_out_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 4px; font-size: 13px; font-weight: 600; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.zoom_out_btn.clicked.connect(self._zoom_out)
        reader_view_header.addWidget(self.zoom_out_btn)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(36)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setStyleSheet("color: #6E6E73; font-size: 10px; font-weight: 500;")
        reader_view_header.addWidget(self.zoom_label)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedSize(24, 20)
        self.zoom_in_btn.setStyleSheet("""
            QPushButton { background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 4px; font-size: 13px; font-weight: 600; }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        reader_view_header.addWidget(self.zoom_in_btn)
        reader_view_header.addWidget(go_btn)
        reader_view_layout.addLayout(reader_view_header)

        # ===== Pin 状态初始化 =====
        self._pins_visible = True
        self._pending_note_history = None
        self._zoom_level = 100

        self.reader_pdf_view = QWebEngineView()
        self.reader_pdf_view.page().setBackgroundColor(Qt.transparent)
        self.reader_pdf_view.setHtml("""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="utf-8">
        <script src="pdf.min.js"></script>
        <script>
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.js';

        var _pdfQueue = [];
        var _annotations = [];
        var _currentScrollPage = 1;
        var _currentRenderId = 0;
        var _isPinMode = false;
        var _pinsVisible = true;
        var _jsZoomLevel = 100;

        function setZoom(level) {
            _jsZoomLevel = level;
            var scale = level / 100;
            document.querySelectorAll('.pw').forEach(function(pw) {
                pw.style.transform = 'scale(' + scale + ')';
                pw.style.transformOrigin = 'top center';
                pw.style.marginBottom = ((scale - 1) * pw.offsetHeight) + 'px';
            });
        }

        window.addEventListener('wheel', function(e) {
            if (e.ctrlKey || e.metaKey) {
                e.preventDefault();
                var levels = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200, 250, 300, 400, 500];
                var next = _jsZoomLevel;
                if (-e.deltaY > 0) {
                    for (var i = 0; i < levels.length; i++) {
                        if (levels[i] > _jsZoomLevel) { next = levels[i]; break; }
                    }
                } else {
                    for (var i = levels.length - 1; i >= 0; i--) {
                        if (levels[i] < _jsZoomLevel) { next = levels[i]; break; }
                    }
                }
                if (next !== _jsZoomLevel) {
                    setZoom(next);
                    document.title = 'zoom:' + next;
                }
            }
        }, { passive: false });

        function loadPdf(b64) {
            if (typeof pdfjsLib === 'undefined') { _pdfQueue.push(b64); return; }
            doLoadPdf(b64);
        }
        async function doLoadPdf(b64) {
            const renderId = ++_currentRenderId;
            try {
                const raw = atob(b64);
                const arr = new Uint8Array(raw.length);
                for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
                const pdf = await pdfjsLib.getDocument({data: arr}).promise;
                if (renderId !== _currentRenderId) return;

                const c = document.getElementById('pdfc');
                c.innerHTML = '';
                document.getElementById('pinfo').style.display = 'block';
                document.getElementById('tp').textContent = pdf.numPages;

                let defaultVp = { width: 800, height: 1131 };
                if (pdf.numPages > 0) {
                    const firstPage = await pdf.getPage(1);
                    defaultVp = firstPage.getViewport({scale: 1.5});
                }

                const observer = new IntersectionObserver((entries, obs) => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting) {
                            const wrap = entry.target;
                            if (wrap.dataset.rendered) return;
                            wrap.dataset.rendered = "true";

                            const pageNum = parseInt(wrap.getAttribute('data-page'));
                            pdf.getPage(pageNum).then(page => {
                                const vp = page.getViewport({scale: 1.5});
                                wrap.style.width = vp.width + 'px';
                                wrap.style.height = vp.height + 'px';
                                const ca = wrap.querySelector('canvas');
                                ca.width = vp.width;
                                ca.height = vp.height;
                                page.render({canvasContext: ca.getContext('2d'), viewport: vp});
                            });
                        }
                    });
                }, { rootMargin: "150% 0px 150% 0px" });

                for (let i = 1; i <= pdf.numPages; i++) {
                    const wrap = document.createElement('div');
                    wrap.className = 'pw';
                    wrap.setAttribute('data-page', i);
                    wrap.style.position = 'relative';
                    wrap.style.width = defaultVp.width + 'px';
                    wrap.style.height = defaultVp.height + 'px';

                    const ca = document.createElement('canvas');
                    ca.className = 'pp';
                    wrap.appendChild(ca);
                    c.appendChild(wrap);

                    observer.observe(wrap);
                }

                if (renderId === _currentRenderId) {
                    rerenderAnnotations();
                    updatePageIndicator();
                }
            } catch(e) {
                if (renderId === _currentRenderId) {
                    document.getElementById('pdfc').innerHTML = '<p class="msg">PDF加载失败: ' + e.message + '</p>';
                }
            }
        }
        function showError(msg) {
            document.getElementById('pdfc').innerHTML = '<p class="msg">' + msg + '</p>';
        }
        function _pdfReady() { while (_pdfQueue.length) doLoadPdf(_pdfQueue.shift()); }

        function updatePageIndicator() {
            var wrappers = document.querySelectorAll('.pw');
            var top = window.scrollY + 50;
            var pn = 1;
            wrappers.forEach(function(w) {
                if (w.offsetTop <= top) pn = parseInt(w.getAttribute('data-page'));
            });
            _currentScrollPage = pn;
            document.getElementById('pn').textContent = pn;
        }

        function scrollToPage(pn) {
            var wrapper = document.querySelector('.pw[data-page="' + pn + '"]');
            if (wrapper) {
                wrapper.scrollIntoView({behavior: 'smooth', block: 'start'});
                updatePageIndicator();
            }
        }

        function enterPinMode() {
            _isPinMode = true;
            document.getElementById('pdfc').style.cursor = 'crosshair';
        }

        function setPinsVisibility(visible) {
            _pinsVisible = visible;
            document.querySelectorAll('.anno-pin').forEach(function(el) {
                el.style.display = visible ? 'block' : 'none';
            });
        }

        function loadAnnotations(data) {
            try {
                if (typeof data === 'string') data = JSON.parse(data);
                _annotations = data.annotations || [];
                rerenderAnnotations();
            } catch(e) { console.error('loadAnnotations error:', e); }
        }

        function addAnnotation(ann) {
            try {
                if (typeof ann === 'string') ann = JSON.parse(ann);
                _annotations.push(ann);
                renderSingleAnnotation(ann);
            } catch(e) { console.error('addAnnotation error:', e); }
        }

        function removeAnnotation(annId) {
            _annotations = _annotations.filter(function(a) { return a.id !== annId; });
            document.querySelectorAll('.anno-pin[data-id="' + annId + '"]').forEach(function(el) { el.remove(); });
        }

        function rerenderAnnotations() {
            document.querySelectorAll('.anno-pin').forEach(function(el) { el.remove(); });
            _annotations.forEach(function(ann) { renderSingleAnnotation(ann); });
        }

        function renderSingleAnnotation(ann) {
            var wrapper = document.querySelector('.pw[data-page="' + ann.page + '"]');
            if (!wrapper) return;
            var existing = wrapper.querySelector('.anno-pin[data-id="' + ann.id + '"]');
            if (existing) return;

            var pin = document.createElement('div');
            pin.className = 'anno-pin';
            pin.setAttribute('data-id', ann.id);
            pin.setAttribute('data-note', ann.summary || '');
            pin.setAttribute('data-ts', ann.created || '');

            var topPct = (ann.y_percent !== undefined) ? ann.y_percent : 0.5;
            var leftPct = (ann.x_percent !== undefined) ? ann.x_percent : 0.95;

            pin.style.top = (topPct * 100) + '%';
            pin.style.left = (leftPct * 100) + '%';

            pin.style.display = _pinsVisible ? 'block' : 'none';

            pin.addEventListener('mouseenter', function(e) {
                var note = this.getAttribute('data-note');
                if (note) showTip(e.clientX, e.clientY, note);
            });
            pin.addEventListener('mouseleave', function() { hideTip(); });
            pin.addEventListener('mousemove', function(e) { moveTip(e.clientX, e.clientY); });

            pin.addEventListener('click', function(e) {
                e.stopPropagation();
            });

            pin.addEventListener('dblclick', function(e) {
                e.stopPropagation();
                var ts = this.getAttribute('data-ts');
                if (ts) {
                    document.title = "pin_dblclick:" + ts;
                    setTimeout(function() { document.title = 'idle'; }, 50);
                }
            });

            pin.addEventListener('contextmenu', function(e) {
                e.preventDefault();
                e.stopPropagation();
                var ts = this.getAttribute('data-ts');
                if (ts) {
                    document.title = "pin_rclick:" + ts;
                    setTimeout(function() { document.title = 'idle'; }, 50);
                }
            });

            wrapper.appendChild(pin);
        }

        function showTip(x, y, text) {
            var tip = document.getElementById('atip');
            tip.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');
            tip.style.display = 'block';
            tip.style.left = (x + 12) + 'px';
            tip.style.top = (y + 12) + 'px';
        }

        function moveTip(x, y) {
            var tip = document.getElementById('atip');
            tip.style.left = (x + 12) + 'px';
            tip.style.top = (y + 12) + 'px';
        }

        function hideTip() {
            document.getElementById('atip').style.display = 'none';
        }

        window.addEventListener('scroll', updatePageIndicator);

        document.addEventListener('DOMContentLoaded', function() {
            var chk = setInterval(function() {
                if (typeof pdfjsLib !== 'undefined') {
                    clearInterval(chk);
                    _pdfReady();

                    document.getElementById('pdfc').addEventListener('click', function(e) {
                        if (!_isPinMode) return;

                        var pw = e.target.closest('.pw');
                        if (!pw) return;

                        var rect = pw.getBoundingClientRect();
                        var x = e.clientX - rect.left;
                        var y = e.clientY - rect.top;

                        var x_percent = x / rect.width;
                        var y_percent = y / rect.height;
                        var page = parseInt(pw.getAttribute('data-page'));

                        _isPinMode = false;
                        document.getElementById('pdfc').style.cursor = 'default';

                        setPinsVisibility(true);

                        document.title = "pin_click:" + JSON.stringify({
                            page: page,
                            x_percent: x_percent,
                            y_percent: y_percent,
                            _ts: Date.now()
                        });
                    });
                }
            }, 200);
        });
        </script>
        <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{background:#e8e8e8}
        #pbar{position:fixed;top:0;left:0;right:0;background:rgba(255,255,255,.93);padding:5px 12px;text-align:center;font-size:12px;color:#555;z-index:100;border-bottom:1px solid #ddd}
        #pdfc{padding:44px 10px 10px}
        .pw{display:block;margin:10px auto;position:relative;width:100%;max-width:900px}
        .pp{display:block;margin:0 auto;box-shadow:0 1px 4px rgba(0,0,0,.15);background:#fff;width:100%;max-width:900px;height:auto}
        .msg{text-align:center;padding:40px;color:#999;font-size:15px;font-family:sans-serif}
        .anno-pin {
            position: absolute;
            width: 14px;
            height: 14px;
            background-color: #FF9F0A;
            border: 2px solid #FFFFFF;
            border-radius: 50%;
            cursor: pointer;
            z-index: 99;
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
            transform: translate(-50%, -50%);
            transition: transform 0.15s ease, background-color 0.15s ease;
        }
        .anno-pin:hover {
            transform: translate(-50%, -50%) scale(1.3);
            background-color: #FF3B30;
        }
        #atip{display:none;position:fixed;z-index:9999;background:rgba(30,30,30,0.92);color:#fff;
            padding:8px 14px;border-radius:8px;font-size:13px;line-height:1.5;max-width:380px;
            pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,0.3);word-wrap:break-word}
        ::-webkit-scrollbar{width:6px}
        ::-webkit-scrollbar-thumb{background:#bbb;border-radius:3px}
        </style>
        </head>
        <body>
        <div id="atip"></div>
        <div id="pbar"><span id="pinfo" style="display:none">页码: <span id="pn">1</span> / <span id="tp">0</span></span></div>
        <div id="pdfc"><p class="msg">选择左侧文献以阅读</p></div>
        </body>
        </html>
        """, QUrl.fromLocalFile(os.path.dirname(os.path.abspath(__file__)) + "/"))
        reader_view_layout.addWidget(self.reader_pdf_view, 1)

        # 绑定 title 属性改变的桥梁信号
        self.reader_pdf_view.titleChanged.connect(self._on_pdf_view_title_changed)

        self.main_splitter.addWidget(reader_view_widget)

        # ===== 右侧：翻译结果 + 追问答疑（垂直分割，宽度对齐） =====
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setHandleWidth(1)
        right_splitter.setStyleSheet("QSplitter::handle { background-color: #D1D1D6; }")

        # ---- 翻译结果区 ----
        translate_widget = QWidget()
        translate_layout = QVBoxLayout(translate_widget)
        translate_layout.setContentsMargins(4, 8, 8, 4)

        translate_header = QHBoxLayout()
        translate_label = QLabel("📝 翻译结果")
        translate_label.setStyleSheet("color: #6E6E73; font-size: 11px; font-weight: 500;")
        translate_header.addWidget(translate_label)
        translate_header.addStretch()

        # ---------- 新增：复制按钮 ----------
        self.reader_copy_btn = QPushButton("复制")
        self.reader_copy_btn.setFixedSize(60, 20)
        self.reader_copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF; color: #FFF;
                border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background-color: #005BBF; }
            QPushButton:disabled { background-color: #C7C7CC; }
        """)
        self.reader_copy_btn.setEnabled(False)
        self.reader_copy_btn.clicked.connect(self._copy_translation)
        translate_header.addWidget(self.reader_copy_btn)
        # -----------------------------------

        self.reader_note_btn = QPushButton("💾 Note")
        self.reader_note_btn.setFixedSize(60, 20)
        self.reader_note_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9F0A; color: #FFF;
                border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
            }
            QPushButton:hover { background-color: #E0890A; }
            QPushButton:disabled { background-color: #C7C7CC; }
        """)
        self.reader_note_btn.clicked.connect(self._save_note)
        translate_header.addWidget(self.reader_note_btn)
        translate_layout.addLayout(translate_header)

        self.reader_browser = QWebEngineView()
        self.reader_browser.page().setBackgroundColor(Qt.transparent)

        reader_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <script>
                window.MathJax = {
                    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] },
                    svg: { fontCache: 'global' },
                    options: { enableMenu: false, renderActions: { assistiveMml: [], enrich: [], addTex: [150, (doc) => { for (const math of doc.math) { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } }, (math) => { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } ] } }
                };
                let currentRawText = "";
                let renderTimer = null;

                function startStream() {
                    const box = document.getElementById('reader-content-box');
                    if (!box) return;
                    box.style.opacity = 1;
                    currentRawText = "";
                    box.innerHTML = "大模型正在思考...";
                }

                function appendStreamChunk(chunk) {
                    const box = document.getElementById('reader-content-box');
                    if (!box) return;
                    if (currentRawText === "") box.innerHTML = "";
                    currentRawText += chunk;

                    let htmlText = currentRawText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
                    htmlText = htmlText.replace(/\\n*\\$\\$/g, '$$$$').replace(/\\$\\$\\n*/g, '$$$$');
                    box.innerHTML = htmlText.replace(/\\n/g, '<br>');

                    clearTimeout(renderTimer);
                    renderTimer = setTimeout(() => {
                        if (typeof MathJax !== 'undefined') MathJax.typesetPromise([box]).catch(() => {});
                    }, 200);
                }

                function finishStream() {
                    const box = document.getElementById('reader-content-box');
                    if (!box) return;
                    clearTimeout(renderTimer);
                    if (typeof MathJax !== 'undefined') MathJax.typesetPromise([box]).catch(() => {});
                }

                async function renderNewText(text) {
                    startStream();
                    appendStreamChunk(text);
                    finishStream();
                }

                document.addEventListener('copy', function(e) {
                    const sel = window.getSelection();
                    if (!sel.rangeCount) return;
                    const frag = sel.getRangeAt(0).cloneContents();
                    const div = document.createElement('div');
                    div.style.position = 'absolute';
                    div.style.left = '-9999px';
                    div.appendChild(frag);
                    document.body.appendChild(div);
                    div.querySelectorAll('mjx-container').forEach(function(mjx) {
                        const tex = mjx.getAttribute('data-tex');
                        const isDisplay = mjx.getAttribute('data-display') === 'true';
                        if (tex) {
                            const span = document.createElement('span');
                            span.textContent = isDisplay ? '\\n$$' + tex + '$$\\n' : '$' + tex + '$';
                            mjx.parentNode.replaceChild(span, mjx);
                        }
                    });
                    const text = div.innerText;
                    document.body.removeChild(div);
                    e.clipboardData.setData('text/plain', text);
                    e.preventDefault();
                });
            </script>
            <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
            <style>
                body {
                    font-family: 'Times New Roman', 'Kaiti SC', 'STKaiti', serif;
                    color: #1D1D1F; font-size: 19px; line-height: 1.8; padding: 10px 20px;
                    background-color: transparent; margin: 0; text-align: justify;
                }
                /* 稍微压低块级公式的外边距 */
                mjx-container[display="true"] { margin: 0.5em 0 !important; display: block; }
                mjx-container { -webkit-user-select: all; user-select: all; }
                mjx-container svg { pointer-events: none; }
                ::-webkit-scrollbar { display: none; }
            </style>
        </head>
        <body>
            <div id="reader-content-box">等候翻译结果...</div>
        </body>
        </html>
        """
        self.reader_browser.setHtml(reader_html)
        translate_layout.addWidget(self.reader_browser, 1)

        # ---- 追问答疑 ----
        chat_widget = QWidget()
        chat_widget.setStyleSheet("background-color: #FFFFFF; border-radius: 6px;")
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(4, 6, 4, 4)
        chat_layout.setSpacing(4)

        reader_chat_header = QHBoxLayout()
        reader_chat_label = QLabel("💬 追问答疑")
        reader_chat_label.setStyleSheet("color: #6E6E73; font-size: 11px; font-weight: 500; padding-left: 2px;")
        reader_chat_header.addWidget(reader_chat_label)
        reader_chat_header.addStretch()

        self.reader_clear_chat_btn = QPushButton("清空对话")
        self.reader_clear_chat_btn.setFixedSize(70, 22)
        self.reader_clear_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 4px; font-size: 10px;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.reader_clear_chat_btn.clicked.connect(self._reader_clear_chat)
        reader_chat_header.addWidget(self.reader_clear_chat_btn)
        chat_layout.addLayout(reader_chat_header)

        self.reader_chat_browser = QWebEngineView()
        self.reader_chat_browser.setMinimumHeight(80)
        self.reader_chat_browser.page().setBackgroundColor(Qt.transparent)
        reader_chat_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <script>
                window.MathJax = {
                    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] },
                    svg: { fontCache: 'global' },
                    options: { enableMenu: false, renderActions: { assistiveMml: [], enrich: [], addTex: [150, (doc) => { for (const math of doc.math) { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } }, (math) => { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } ] } }
                };

                function addChat(role, text) {
                    document.getElementById('empty-state').style.display = 'none';
                    const box = document.getElementById('chat-container');

                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'message ' + (role === '你' ? 'user' : 'ai');

                    const bubble = document.createElement('div');
                    bubble.className = 'bubble';
                    bubble.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');

                    msgDiv.appendChild(bubble);
                    box.appendChild(msgDiv);

                    MathJax.typesetPromise([bubble]).then(() => {
                        updateScrollButton();
                    });
                }

                function clearChat() {
                    document.getElementById('chat-container').innerHTML = '';
                    document.getElementById('empty-state').style.display = 'block';
                }

                function showTyping() {
                    hideTyping();
                    document.getElementById('empty-state').style.display = 'none';
                    const box = document.getElementById('chat-container');
                    const indicator = document.createElement('div');
                    indicator.id = 'typing-indicator';
                    indicator.className = 'typing-indicator';
                    indicator.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                    box.appendChild(indicator);
                    box.scrollTop = box.scrollHeight;
                }
                function hideTyping() {
                    const el = document.getElementById('typing-indicator');
                    if (el) el.remove();
                }
                function scrollToTop() {
                    document.getElementById('chat-container').scrollTop = 0;
                    updateScrollButton();
                }
                function updateScrollButton() {
                    const box = document.getElementById('chat-container');
                    const btn = document.getElementById('scroll-top-btn');
                    if (!btn) return;
                    btn.style.display = box.scrollTop > 200 ? 'flex' : 'none';
                }
                (function() {
                    const container = document.getElementById('chat-container');
                    if (container) container.addEventListener('scroll', updateScrollButton);
                })();

                document.addEventListener('copy', function(e) {
                    const sel = window.getSelection();
                    if (!sel.rangeCount) return;
                    const frag = sel.getRangeAt(0).cloneContents();
                    const div = document.createElement('div');
                    div.style.position = 'absolute';
                    div.style.left = '-9999px';
                    div.appendChild(frag);
                    document.body.appendChild(div);
                    div.querySelectorAll('mjx-container').forEach(function(mjx) {
                        const tex = mjx.getAttribute('data-tex');
                        const isDisplay = mjx.getAttribute('data-display') === 'true';
                        if (tex) {
                            const span = document.createElement('span');
                            span.textContent = isDisplay ? '\\n$$' + tex + '$$\\n' : '$' + tex + '$';
                            mjx.parentNode.replaceChild(span, mjx);
                        }
                    });
                    const text = div.innerText;
                    document.body.removeChild(div);
                    e.clipboardData.setData('text/plain', text);
                    e.preventDefault();
                });
            </script>
            <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #FFFFFF;
                    margin: 0; padding: 16px;
                    height: 100vh; box-sizing: border-box;
                    display: flex; flex-direction: column;
                    position: relative;
                }
                #empty-state {
                    position: absolute; top: 40%; left: 50%; transform: translate(-50%, -50%);
                    color: #A0A0A5; font-size: 14px; text-align: center;
                    line-height: 1.6; max-width: 80%; font-weight: 400;
                }
                #chat-container {
                    flex: 1; overflow-y: auto; padding-right: 4px;
                    display: flex; flex-direction: column; gap: 24px;
                }
                .message { display: flex; flex-direction: column; width: 100%; }
                .message.user { align-items: flex-end; }
                .message.ai { align-items: flex-start; }
                .bubble { max-width: 88%; font-size: 15px; line-height: 1.6; word-wrap: break-word; color: #0D0D0D; }

                .message.user .bubble {
                    background-color: #F4F4F4; padding: 10px 16px;
                    border-radius: 18px; border-bottom-right-radius: 4px;
                }
                .message.ai .bubble { background-color: transparent; padding: 0px 4px; }
                .message.ai .bubble p { margin: 0 0 10px 0; }
                .message.ai .bubble p:last-child { margin-bottom: 0; }

                .typing-indicator { display: flex; gap: 4px; padding: 4px 4px; align-items: center; }
                .typing-indicator .dot { width: 6px; height: 6px; border-radius: 50%; background: #A0A0A5; animation: bounce 1.4s infinite ease-in-out both; }
                .typing-indicator .dot:nth-child(1) { animation-delay: 0s; }
                .typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
                .typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }
                @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

                #scroll-top-btn { position: fixed; bottom: 80px; right: 24px; width: 36px; height: 36px; border-radius: 18px; background: rgba(0,0,0,0.5); color: #FFF; border: none; font-size: 18px; display: none; align-items: center; justify-content: center; cursor: pointer; z-index: 100; }
                #scroll-top-btn:hover { background: rgba(0,0,0,0.7); }

                ::-webkit-scrollbar { width: 6px; }
                ::-webkit-scrollbar-thumb { background: #E5E5E5; border-radius: 3px; }
                ::-webkit-scrollbar-thumb:hover { background: #CCCCCC; }
                mjx-container { -webkit-user-select: all; user-select: all; }
                mjx-container svg { pointer-events: none; }
            </style>
        </head>
        <body>
            <div id="empty-state">不时为拾到更光滑的石子或更美丽的贝壳而欢欣鼓舞。</div>
            <div id="chat-container"></div>
            <div id="scroll-top-btn" onclick="scrollToTop()">↑</div>
        </body>
        </html>
        """
        self.reader_chat_browser.setHtml(reader_chat_html)
        chat_layout.addWidget(self.reader_chat_browser, 1)

        # ========== 新增：补回追问答疑的输入框与按钮 ==========
        reader_chat_input_layout = QHBoxLayout()
        reader_chat_input_layout.setContentsMargins(4, 4, 4, 8)
        reader_chat_input_layout.setSpacing(8)

        self.reader_chat_input = QLineEdit()
        self.reader_chat_input.setPlaceholderText("还想了解点什么？")
        self.reader_chat_input.setStyleSheet("""
            QLineEdit {
                background-color: #F5F5F7; color: #0D0D0D;
                border: 1px solid #E5E5E5; border-radius: 16px;
                padding: 8px 14px; font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #007AFF; }
        """)
        self.reader_chat_input.returnPressed.connect(self._reader_send_chat)
        reader_chat_input_layout.addWidget(self.reader_chat_input)

        self.reader_send_btn = QPushButton("↑")
        self.reader_send_btn.setFixedSize(32, 32)
        self.reader_send_btn.setStyleSheet("""
            QPushButton {
                background-color: #000000; color: #FFFFFF;
                border: none; border-radius: 16px;
                font-size: 16px; font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #333333; }
            QPushButton:pressed { background-color: #000000; }
        """)
        self.reader_send_btn.clicked.connect(self._reader_send_chat)
        reader_chat_input_layout.addWidget(self.reader_send_btn)

        chat_layout.addLayout(reader_chat_input_layout)
        # ======================================================

        right_splitter.addWidget(translate_widget)
        right_splitter.addWidget(chat_widget)
        right_splitter.setSizes([400, 200])

        self.main_splitter.addWidget(right_splitter)
        self.main_splitter.setSizes([0, 600, 200])
        layout.addWidget(self.main_splitter)

        self.stack.addWidget(tab)

        # 初始化文献目录
        self._refresh_reader_file_list()

    def _toggle_reader_sidebar(self):
        self._reader_sidebar_visible = not self._reader_sidebar_visible
        left_panel = self.main_splitter.widget(0)
        left_panel.setVisible(self._reader_sidebar_visible)

        sizes = self.main_splitter.sizes()
        if self._reader_sidebar_visible:
            if sizes[0] == 0:
                self.main_splitter.setSizes([200, max(100, sizes[1] - 200), sizes[2]])
        else:
            self.main_splitter.setSizes([0, sizes[1] + sizes[0], sizes[2]])

    def _get_download_folder(self):
        folder = settings.get("download_dir", "")
        if folder:
            return folder
        return LIT_DIR

    def _refresh_reader_file_list(self):
        from PySide6.QtWidgets import QTreeWidgetItem
        self.reader_file_tree.blockSignals(True)
        self.reader_file_tree.clear()
        idx = load_literature_index()
        tree = idx.get("tree", {})
        folder_icon = self._folder_icon()

        def add_files(parent, node, current_path):
            if not isinstance(node, dict):
                return
            for entry in node.get("files", []):
                name = entry.get("name", "未知")
                path = entry.get("path", "")
                fi = QTreeWidgetItem([name])
                fi.setData(0, Qt.UserRole, path)
                fi.setData(0, Qt.UserRole + 1, ("file", path, name))
                ico = self._path_for_icon(path)
                if not ico.isNull():
                    fi.setIcon(0, ico)
                parent.addChild(fi)
            for child_name, child_node in sorted(node.get("children", {}).items()):
                ci = QTreeWidgetItem([child_name])
                ci.setData(0, Qt.UserRole, None)
                ci.setData(0, Qt.UserRole + 1, ("folder", current_path + [child_name]))
                if not folder_icon.isNull():
                    ci.setIcon(0, folder_icon)
                add_files(ci, child_node, current_path + [child_name])
                parent.addChild(ci)

        root_node = tree.get("__root__", {})
        if root_node.get("files"):
            root_item = QTreeWidgetItem(["根目录"])
            root_item.setData(0, Qt.UserRole, None)
            root_item.setData(0, Qt.UserRole + 1, ("folder", []))
            if not folder_icon.isNull():
                root_item.setIcon(0, folder_icon)
            add_files(root_item, root_node, [])
            self.reader_file_tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)

        for fname in sorted(tree.keys()):
            if fname.startswith("__"):
                continue
            node = tree[fname]
            fi = QTreeWidgetItem([fname])
            fi.setData(0, Qt.UserRole, None)
            fi.setData(0, Qt.UserRole + 1, ("folder", [fname]))
            if not folder_icon.isNull():
                fi.setIcon(0, folder_icon)
            add_files(fi, node, [fname])
            self.reader_file_tree.addTopLevelItem(fi)
            fi.setExpanded(True)

        self.reader_file_tree.blockSignals(False)

    def _reader_new_folder(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name.startswith("__"):
            QMessageBox.warning(self, "无效名称", "文件夹名称不能以 __ 开头")
            return
        idx = load_literature_index()
        tree = idx["tree"]
        item = self.reader_file_tree.currentItem()
        if item:
            data = item.data(0, Qt.UserRole + 1)
            if data and data[0] == "folder":
                fpath = data[1]
                target_node = self._get_node(tree, fpath) if fpath else tree.setdefault("__root__", {"files":[],"children":{}})
                children = target_node.setdefault("children", {})
                if name not in children:
                    children[name] = {"files": [], "children": {}}
                save_literature_index(idx)
                self._refresh_lit_tree()
                self._refresh_reader_file_list()
                return
        if name not in tree:
            tree[name] = {"files": [], "children": {}}
        save_literature_index(idx)
        self._refresh_lit_tree()
        self._refresh_reader_file_list()

    def _reader_rename(self):
        item = self.reader_file_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.UserRole + 1)
        if not data:
            return

        orig_name = item.text(0)
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        if data[0] == "file":
            base_name, ext = os.path.splitext(orig_name)
        else:
            base_name, ext = orig_name, ""

        new_base, ok = QInputDialog.getText(self, "重命名", "新名称:", text=base_name)
        if not ok or not new_base.strip():
            return
        new_base = new_base.strip()
        new_name = new_base + ext

        idx = load_literature_index()
        tree = idx["tree"]

        if data[0] == "folder":
            fpath = data[1]
            if len(fpath) == 0:
                return
            if len(fpath) == 1:
                if fpath[0] in tree and new_name not in tree:
                    tree[new_name] = tree.pop(fpath[0])
            else:
                parent_node = self._get_node(tree, fpath[:-1])
                children = parent_node.setdefault("children", {})
                old_fname = fpath[-1]
                if old_fname in children and new_name not in children:
                    children[new_name] = children.pop(old_fname)
        elif data[0] == "file":
            old_name = data[2]
            old_path = data[1]
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
                self._move_associated_files(old_path, new_path)
            except Exception as e:
                QMessageBox.warning(self, "重命名失败", str(e))
                return

            def rename_in_tree(node):
                if not isinstance(node, dict):
                    return False
                for f in node.get("files", []):
                    if f.get("name") == old_name:
                        f["name"] = new_name
                        f["path"] = new_path
                        return True
                for child in node.get("children", {}).values():
                    if rename_in_tree(child):
                        return True
                return False

            for top_node in tree.values():
                if rename_in_tree(top_node):
                    break

            if getattr(self, '_current_pdf_path', None) == old_path:
                self._current_pdf_path = new_path

        save_literature_index(idx)
        self._refresh_lit_tree()
        self._refresh_reader_file_list()

    def _reader_context_menu(self, pos):
        item = self.reader_file_tree.itemAt(pos)
        if not item:
            return
        self.reader_file_tree.setCurrentItem(item)
        data = item.data(0, Qt.UserRole + 1)
        if not data:
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("重命名", self._reader_rename)
        if data[0] == "file":
            menu.addAction("删除文献", self._reader_delete)
        elif data[0] == "folder":
            menu.addAction("删除文件夹", self._reader_delete)
        menu.exec(self.reader_file_tree.viewport().mapToGlobal(pos))

    def _reader_delete(self):
        item = self.reader_file_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.UserRole + 1)
        if not data:
            return

        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "确认删除", "确定彻底删除该项目（包括所有关联笔记和标注）?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        idx = load_literature_index()
        tree = idx["tree"]

        if data[0] == "folder":
            fpath = data[1]
            if len(fpath) == 0:
                return
            fname = fpath[-1]
            if len(fpath) == 1:
                target_node = tree.get(fname, {})
                parent_dict = tree
            else:
                parent_node = self._get_node(tree, fpath[:-1])
                target_node = parent_node.get("children", {}).get(fname, {})
                parent_dict = parent_node.get("children", {})

            all_files = self._collect_folder_files(target_node)
            for entry in all_files:
                p = entry.get("path", "")
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except:
                        pass
                self._delete_associated_files(p)
            parent_dict.pop(fname, None)

        elif data[0] == "file":
            path = data[1]
            name = data[2]
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
            self._delete_associated_files(path)
            self._remove_file_from_tree(tree, name)

        save_literature_index(idx)
        self._refresh_lit_tree()
        self._refresh_reader_file_list()

        if getattr(self, '_current_pdf_path', None) and not os.path.exists(self._current_pdf_path):
            self._current_pdf_path = None
            self.reader_pdf_view.page().runJavaScript("document.getElementById('pdfc').innerHTML = '<p class=\"msg\">选择左侧文献以阅读</p>';")
            self.reader_note_list.clear()

    def _reader_start_drag(self):
        item = self.reader_file_tree.currentItem()
        if not item:
            return False
        data = item.data(0, Qt.UserRole + 1)
        if not data or data[0] != "file":
            return False

        self._reader_drag_item = item
        from PySide6.QtCore import QMimeData, QPoint
        from PySide6.QtGui import QDrag
        mime = QMimeData()
        mime.setData("application/x-lit-file", b"1")
        mime.setText(data[2])
        drag = QDrag(self.reader_file_tree)
        drag.setMimeData(mime)

        icon = item.icon(0)
        if not icon.isNull():
            pix = icon.pixmap(56, 56)
            drag.setPixmap(pix)
            drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))

        result = drag.exec(Qt.MoveAction)
        if result != Qt.MoveAction:
            self._reader_drag_item = None
        return True

    def _reader_drag_enter(self, event):
        if getattr(self, '_reader_drag_item', None):
            event.accept()
        elif event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()
        return True

    def _reader_drag_move(self, event):
        pos = event.position().toPoint()
        item = self.reader_file_tree.itemAt(pos)
        if item:
            data = item.data(0, Qt.UserRole + 1)
            if data and data[0] == "folder":
                if item is not self.reader_file_tree.currentItem():
                    self.reader_file_tree.setCurrentItem(item)
                action = Qt.MoveAction if getattr(self, '_reader_drag_item', None) else Qt.CopyAction
                event.setDropAction(action)
                event.accept()
                return True
        event.ignore()
        return True

    def _reader_drop(self, event):
        pos = event.position().toPoint()
        target_item = self.reader_file_tree.itemAt(pos)
        if not target_item:
            event.ignore()
            return True
        target_data = target_item.data(0, Qt.UserRole + 1)
        if not target_data or target_data[0] != "folder":
            event.ignore()
            return True

        target_fpath = target_data[1]
        idx = load_literature_index()
        tree = idx["tree"]

        if len(target_fpath) == 0:
            target_node = tree.setdefault("__root__", {"files": [], "children": {}})
        else:
            target_node = self._get_node(tree, target_fpath)

        if getattr(self, '_reader_drag_item', None):
            src_data = self._reader_drag_item.data(0, Qt.UserRole + 1)
            if not src_data or src_data[0] != "file":
                event.ignore()
                return True
            path = src_data[1]
            name = src_data[2]

            self._remove_file_from_tree(tree, name)
            if name not in [f["name"] for f in target_node.get("files", [])]:
                target_node.setdefault("files", []).append({"name": name, "path": path})

            save_literature_index(idx)
            self._refresh_lit_tree()
            self._refresh_reader_file_list()
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self._reader_drag_item = None

        elif event.mimeData().hasUrls():
            import shutil
            for url in event.mimeData().urls():
                if not url.isLocalFile():
                    continue
                src_path = url.toLocalFile()
                name = os.path.basename(src_path)
                dest_path = os.path.join(LIT_DIR, name)
                counter = 1
                while os.path.exists(dest_path):
                    base, ext = os.path.splitext(name)
                    dest_path = os.path.join(LIT_DIR, f"{base}_{counter}{ext}")
                    counter += 1
                try:
                    shutil.copy2(src_path, dest_path)
                except Exception:
                    continue
                if name not in [f["name"] for f in target_node.get("files", [])]:
                    target_node.setdefault("files", []).append({"name": os.path.basename(dest_path), "path": dest_path})
            save_literature_index(idx)
            self._refresh_lit_tree()
            self._refresh_reader_file_list()
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()
        return True

    MAX_PDF_SIZE = 50 * 1024 * 1024

    def _load_pdf(self, path):
        import json
        try:
            fsize = os.path.getsize(path)
            if fsize > self.MAX_PDF_SIZE:
                self.reader_pdf_view.page().runJavaScript(f"showError({json.dumps('PDF 文件过大 (最大支持 50MB)')})")
                return
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            self.reader_pdf_view.page().runJavaScript(f"loadPdf({json.dumps(b64)})")
        except Exception as e:
            self.reader_pdf_view.page().runJavaScript(f"showError({json.dumps(str(e))})")

    def _on_reader_file_selected(self, current, previous):
        if not current:
            return
        self._sync_notes_tree_state()  # 切换前保存当前笔记树状态
        path = current.data(0, Qt.UserRole)
        if not path:
            return
        if not os.path.exists(path):
            self.reader_pdf_view.page().runJavaScript("showError('文件不存在')")
            return
        self._current_pdf_path = path
        record_literature_read()
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            self._load_pdf(path)
            _selected_path = path
            QTimer.singleShot(800, lambda p=_selected_path: self._load_pdf_annotations(p) if getattr(self, '_current_pdf_path', None) == p else None)
        else:
            self.reader_pdf_view.setUrl(QUrl.fromLocalFile(path))
        self._refresh_notes(path)

    def _reader_jump_page(self):
        try:
            page = int(self.reader_page_input.text().strip())
        except ValueError:
            return
        self.reader_pdf_view.page().runJavaScript(f"scrollToPage({page})")
        self.reader_pdf_view.setFocus()

    def _zoom_in(self):
        levels = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200, 250, 300, 400, 500]
        current = self._zoom_level
        for l in levels:
            if l > current:
                self._zoom_level = l
                break
        else:
            return
        self.zoom_label.setText(f"{self._zoom_level}%")
        self.reader_pdf_view.page().runJavaScript(f"setZoom({self._zoom_level})")

    def _zoom_out(self):
        levels = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 160, 180, 200, 250, 300, 400, 500]
        current = self._zoom_level
        for l in reversed(levels):
            if l < current:
                self._zoom_level = l
                break
        else:
            return
        self.zoom_label.setText(f"{self._zoom_level}%")
        self.reader_pdf_view.page().runJavaScript(f"setZoom({self._zoom_level})")

    def _reader_take_screenshot(self):
        try:
            if hasattr(self, '_reader_ocr_worker') and self._reader_ocr_worker and self._reader_ocr_worker.isRunning():
                return
        except RuntimeError:
            self._reader_ocr_worker = None

        self.reader_browser.page().runJavaScript("startStream();")
        self._reader_ocr_worker = OCRWorker()

        self._reader_ocr_worker.chunk_signal.connect(self._on_reader_ocr_chunk)
        self._reader_ocr_worker.finished_signal.connect(self._on_reader_ocr_result)

        self._reader_ocr_worker.error_signal.connect(
            lambda e: self.reader_browser.page().runJavaScript(f"renderNewText({json.dumps('识别失败: ' + str(e))})")
        )
        self._reader_ocr_worker.start()

    def _copy_translation(self):
        text = getattr(self, '_current_translation', '')
        if text:
            QApplication.clipboard().setText(text)
            original_text = self.reader_copy_btn.text()
            self.reader_copy_btn.setText("✅ 已复制")
            self.reader_copy_btn.setStyleSheet("""
                QPushButton {
                    background-color: #28C840; color: #FFF;
                    border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
                }
            """)
            def restore():
                self.reader_copy_btn.setText(original_text)
                self.reader_copy_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #007AFF; color: #FFF;
                        border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
                    }
                    QPushButton:hover { background-color: #005BBF; }
                    QPushButton:disabled { background-color: #C7C7CC; }
                """)
            QTimer.singleShot(1500, restore)

    def _on_reader_ocr_chunk(self, chunk):
        import json
        self.reader_browser.page().runJavaScript(f"appendStreamChunk({json.dumps(chunk)});")

    def _on_reader_ocr_result(self, text):
        import json
        self._current_translation = text
        self.reader_browser.page().runJavaScript("finishStream();")
        self._reader_chat_history = [{"role": "system", "content": f"以下是翻译结果，供后续对话参考：\n{text}"}]
        self.reader_chat_browser.page().runJavaScript("clearChat();")
        self.reader_note_btn.setEnabled(True)
        self.reader_copy_btn.setEnabled(True)
        self._switch_tab("reader")

    def _reader_send_chat(self):
        question = self.reader_chat_input.text().strip()
        if not question:
            return
        self.reader_chat_input.clear()

        question_safe = json.dumps(question)
        self.reader_chat_browser.page().runJavaScript(f"addChat('你', {question_safe});")

        messages = list(getattr(self, '_reader_chat_history', []))

        instruction = settings.get("qa_prompt", "")
        combined_question = f"{question}\n\n【系统指令】：\n{instruction}"

        messages.append({"role": "user", "content": combined_question})

        self._reader_chat_history.append({"role": "user", "content": question})

        try:
            if hasattr(self, '_reader_chat_worker') and self._reader_chat_worker and self._reader_chat_worker.isRunning():
                self._reader_chat_worker.quit()
                self._reader_chat_worker.wait(500)
        except RuntimeError:
            self._reader_chat_worker = None

        self._reader_chat_worker = ChatWorker(messages)
        self._reader_chat_worker.finished_signal.connect(self._on_reader_chat_response)
        self._reader_chat_worker.error_signal.connect(
            lambda e: self.reader_chat_browser.page().runJavaScript(f"hideTyping(); addChat('错误', {json.dumps(e)});")
        )
        self._reader_chat_worker.start()
        self.reader_chat_browser.page().runJavaScript("showTyping();")

    def _on_reader_chat_response(self, text):
        self.reader_chat_browser.page().runJavaScript("hideTyping();")
        safe_text = json.dumps(text)
        self.reader_chat_browser.page().runJavaScript(f"addChat('AI', {safe_text});")
        if not hasattr(self, '_reader_chat_history'):
            self._reader_chat_history = []
        self._reader_chat_history.append({"role": "assistant", "content": text})

    def _reader_clear_chat(self):
        self._reader_chat_history = []
        self.reader_chat_browser.page().runJavaScript("clearChat();")

    def _get_notes_folders_path(self, pdf_path):
        if not pdf_path or not os.path.exists(pdf_path):
            return None
        base, _ = os.path.splitext(pdf_path)
        return base + "_notes_folders.json"

    def _load_notes_folders(self, path):
        fp = self._get_notes_folders_path(path)
        if fp and os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {"folders": [], "mapping": {}}

    def _save_notes_folders(self, path, data):
        fp = self._get_notes_folders_path(path)
        if fp:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_note_md_path(self, pdf_path):
        if not pdf_path or not os.path.exists(pdf_path):
            return None
        base, _ = os.path.splitext(pdf_path)
        return base + "_notes.md"

    def _get_annot_path(self, pdf_path):
        if not pdf_path or not os.path.exists(pdf_path):
            return None
        base, _ = os.path.splitext(pdf_path)
        return base + "_annots.json"

    def _toggle_pins_visibility(self):
        """控制全部图钉显隐的开关按钮回调"""
        self._pins_visible = not self._pins_visible
        if self._pins_visible:
            self.reader_toggle_pins_btn.setText("隐藏笔记")
            self.reader_pdf_view.page().runJavaScript("setPinsVisibility(true)")
        else:
            self.reader_toggle_pins_btn.setText("显示笔记")
            self.reader_pdf_view.page().runJavaScript("setPinsVisibility(false)")

    def _save_note(self):
        """点击 Note 按钮：不立刻打点，转而进入图钉就绪状态"""
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        history = getattr(self, '_reader_chat_history', [])
        if not history:
            return

        self._pending_note_history = history

        self.reader_pdf_view.page().runJavaScript("enterPinMode()")

        self.reader_note_btn.setText("📍 请在正文位置点击落脚点...")
        self.reader_note_btn.setStyleSheet("""
            QPushButton {
                background-color: #AF52DE; color: #FFF;
                border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
            }
        """)

    def _on_pdf_view_title_changed(self, title):
        """监听前端发回的打钉数据包信号"""
        if title.startswith("pin_click:"):
            try:
                data_str = title[len("pin_click:"):]
                data = json.loads(data_str)
                page = data.get("page")
                x_pct = data.get("x_percent")
                y_pct = data.get("y_percent")

                path = getattr(self, '_current_pdf_path', None)
                if path and self._pending_note_history:
                    self._do_save_note(path, self._pending_note_history, page, x_pct, y_pct)
                    self._pending_note_history = None

                    self._pins_visible = True
                    self.reader_toggle_pins_btn.setText("隐藏笔记")
            except Exception as e:
                print(f"处理图钉落点数据流失败: {e}")

        elif title.startswith("pin_dblclick:"):
            ts = title[len("pin_dblclick:"):]
            item = self._find_note_item_by_ts(ts)
            if item:
                self.reader_note_list.setCurrentItem(item)
                self.reader_note_list.scrollToItem(item)
                QTimer.singleShot(50, lambda: self._note_double_clicked(item))

        elif title.startswith("pin_rclick:"):
            ts = title[len("pin_rclick:"):]
            item = self._find_note_item_by_ts(ts)
            if item:
                self.reader_note_list.setCurrentItem(item)
                self.reader_note_list.scrollToItem(item)
                QTimer.singleShot(50, lambda: self._delete_note(item))

        elif title.startswith("zoom:"):
            try:
                level = int(title[len("zoom:"):])
                self._zoom_level = level
                self.zoom_label.setText(f"{level}%")
            except ValueError:
                pass

    def _find_note_item_by_ts(self, ts):
        """通过时间戳在笔记树中寻找对应的 QTreeWidgetItem"""
        def search(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, Qt.UserRole) == ts:
                    return child
                result = search(child)
                if result:
                    return result
            return None

        for i in range(self.reader_note_list.topLevelItemCount()):
            item = self.reader_note_list.topLevelItem(i)
            if item.data(0, Qt.UserRole) == ts:
                return item
            result = search(item)
            if result:
                return result
        return None

    def _sync_notes_tree_state(self):
        """将当前树形结构中笔记的文件夹归属同步到 JSON 文件"""
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        data = self._load_notes_folders(path)
        mapping = data.setdefault("mapping", {})
        for i in range(self.reader_note_list.topLevelItemCount()):
            fi = self.reader_note_list.topLevelItem(i)
            fdata = fi.data(0, Qt.UserRole + 1)
            fname = fdata[1] if isinstance(fdata, tuple) else fi.text(0)
            for j in range(fi.childCount()):
                child = fi.child(j)
                ts = child.data(0, Qt.UserRole)
                if ts:
                    mapping[ts] = fname
        self._save_notes_folders(path, data)

    def _notes_new_folder(self):
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        name, ok = QInputDialog.getText(self, "新建笔记文件夹", "文件夹名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        data = self._load_notes_folders(path)
        folders = data.setdefault("folders", [])
        if name in folders:
            QMessageBox.warning(self, "重复", f"文件夹 '{name}' 已存在")
            return
        folders.append(name)
        self._save_notes_folders(path, data)
        self._refresh_notes(path)

    def _do_save_note(self, path, history, page, x_percent, y_percent):
        """执行最终保存，写入全维度的二维坐标信息 (x_percent, y_percent)"""
        translation = ""
        qa_pairs = []
        if history and history[0].get("role") == "system":
            t = history[0].get("content", "")
            prefix = "以下是翻译结果，供后续对话参考：\n"
            if t.startswith(prefix):
                translation = t[len(prefix):]
        for msg in history[1:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                qa_pairs.append(("你", content))
            elif role == "assistant":
                qa_pairs.append(("AI", content))

        if not translation and not qa_pairs:
            return

        import uuid
        from datetime import datetime
        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        note_id = f"note_{now.strftime('%Y%m%d_%H%M%S')}"

        md_path = self._get_note_md_path(path)
        if md_path:
            lines = []
            lines.append(f"\n## 📌 笔记 - {ts}\n")
            if translation:
                lines.append(translation)
                lines.append("\n---")
            if qa_pairs:
                lines.append("\n### 💡 追问与思考\n")
                for speaker, text in qa_pairs:
                    if speaker == "你":
                        lines.append(f"> **Q: {text}**\n")
                    else:
                        lines.append(f"{text}\n\n---")
            with open(md_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines))

        annot_path = self._get_annot_path(path)
        if annot_path:
            ann_data = {"annotations": []}
            if os.path.exists(annot_path):
                try:
                    with open(annot_path, "r", encoding="utf-8") as f:
                        ann_data = json.load(f)
                except:
                    pass
            summary = translation[:120].replace("\n", " ") if translation else (qa_pairs[0][1][:120] if qa_pairs else "")

            annotation = {
                "id": str(uuid.uuid4())[:8],
                "page": page,
                "x_percent": round(x_percent, 4),
                "y_percent": round(y_percent, 4),
                "note_id": note_id,
                "summary": summary,
                "full_translation": translation,
                "qa": [{"speaker": s, "text": t} for s, t in qa_pairs],
                "created": ts,
            }
            ann_data.setdefault("annotations", []).append(annotation)
            with open(annot_path, "w", encoding="utf-8") as f:
                json.dump(ann_data, f, ensure_ascii=False, indent=2)

            ann_js = json.dumps(annotation)
            self.reader_pdf_view.page().runJavaScript(f"addAnnotation({ann_js})")

        self._refresh_notes(path)
        self.reader_pdf_view.setFocus()

        self.reader_note_btn.setText("✅ 已保存落点")
        self.reader_note_btn.setStyleSheet("""
            QPushButton {
                background-color: #28C840; color: #FFF;
                border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
            }
        """)
        def restore_btn():
            self.reader_note_btn.setText("💾 Note")
            self.reader_note_btn.setStyleSheet("""
                QPushButton {
                    background-color: #FF9F0A; color: #FFF;
                    border: none; border-radius: 4px; font-size: 10px; font-weight: 600;
                }
                QPushButton:hover { background-color: #E0890A; }
                QPushButton:disabled { background-color: #C7C7CC; }
            """)
        QTimer.singleShot(1500, restore_btn)

    def _load_pdf_annotations(self, path):
        annot_path = self._get_annot_path(path)
        if annot_path and os.path.exists(annot_path):
            try:
                with open(annot_path, "r", encoding="utf-8") as f:
                    ann_data = json.load(f)
                ann_js = json.dumps(ann_data)
                self.reader_pdf_view.page().runJavaScript(f"loadAnnotations({ann_js})")
            except:
                self.reader_pdf_view.page().runJavaScript("loadAnnotations('{\"annotations\":[]}')")
        else:
            self.reader_pdf_view.page().runJavaScript("loadAnnotations('{\"annotations\":[]}')")

    def _refresh_notes(self, path):
        self.reader_note_list.clear()
        md_path = self._get_note_md_path(path)
        if not md_path or not os.path.exists(md_path):
            return
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
        except:
            return

        annot_path = self._get_annot_path(path)
        annot_map = {}
        if annot_path and os.path.exists(annot_path):
            try:
                with open(annot_path, "r", encoding="utf-8") as f:
                    ann_data = json.load(f)
                for a in ann_data.get("annotations", []):
                    created = a.get("created", "")
                    if created:
                        annot_map[created] = a.get("page", 1)
            except:
                pass

        folders_data = self._load_notes_folders(path)
        folder_map = folders_data.get("mapping", {})
        folder_order = folders_data.get("folders", [])

        from PySide6.QtWidgets import QTreeWidgetItem
        import re

        folder_icon = self._folder_icon()

        # 创建文件夹节点（根级别）
        folder_items = {}
        for fname in folder_order:
            fi = QTreeWidgetItem([fname])
            fi.setData(0, Qt.UserRole, None)
            fi.setData(0, Qt.UserRole + 1, ("folder", fname))
            fi.setFlags(fi.flags() | Qt.ItemIsEditable)
            if not folder_icon.isNull():
                fi.setIcon(0, folder_icon)
            self.reader_note_list.addTopLevelItem(fi)
            folder_items[fname] = fi

        blocks = re.split(r'\n(?=## (?:📌 )?笔记 - )', content)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            ts_match = re.match(r'## (?:📌 )?笔记 - (.+)', block)
            ts = ts_match.group(1).strip() if ts_match else ""
            title = ts if ts else block[:50]
            page = annot_map.get(ts, 1)

            note_item = QTreeWidgetItem([title])
            note_item.setData(0, Qt.UserRole, ts)
            note_item.setData(0, Qt.UserRole + 1, ("note", block))
            note_item.setData(0, Qt.UserRole + 2, page)

            target_folder = folder_map.get(ts, "")
            if target_folder and target_folder in folder_items:
                folder_items[target_folder].addChild(note_item)
            else:
                self.reader_note_list.addTopLevelItem(note_item)

        for fi in folder_items.values():
            if fi.childCount() > 0:
                fi.setExpanded(True)

    def _note_item_clicked(self, item):
        if not item:
            return
        ts = item.data(0, Qt.UserRole)
        if not ts:  # 文件夹节点
            return
        self._note_click_item = item
        if self._note_click_timer is not None:
            self._note_click_timer.stop()
        self._note_click_timer = QTimer(self)
        self._note_click_timer.setSingleShot(True)
        self._note_click_timer.timeout.connect(self._note_click_timeout)
        self._note_click_timer.start(280)

    def _note_click_timeout(self):
        item = self._note_click_item
        self._note_click_item = None
        self._note_click_timer = None
        if item:
            page = item.data(0, Qt.UserRole + 2)
            if page:
                self.reader_pdf_view.page().runJavaScript(f"scrollToPage({page})")
                self.reader_pdf_view.setFocus()

    def _note_double_clicked(self, item):
        if self._note_click_timer is not None:
            self._note_click_timer.stop()
            self._note_click_timer = None
        self._note_click_item = None
        block_data = item.data(0, Qt.UserRole + 1)
        block = block_data[1] if isinstance(block_data, tuple) else block_data
        if block:
            self._open_note_editor(item, block)

    def _find_folder_for_file(self, tree, file_path):
        """递归查找文献树中 file_path 所在的文件夹节点。"""
        target = os.path.abspath(file_path)
        for folder_name, node in tree.items():
            if not isinstance(node, dict):
                continue
            if "files" in node:
                for f in node.get("files", []):
                    if os.path.abspath(f.get("path", "")) == target:
                        return folder_name, node
            found = self._find_folder_for_file(node.get("children", {}), file_path)
            if found:
                return found
        return None

    NOTES_FEATURE_PROMPTS = {
        "文献概述": (
            "你不是在总结论文，而是在帮助用户沉淀知识。\n\n"
            "输入包括：\n"
            "1. 原文摘录（highlight）\n"
            "2. AI 对摘录的解释\n"
            "3. 用户自己的批注（notes）\n\n"
            "请不要简单按照论文结构复述，而是融合用户的思考。\n\n"
            "对于每一部分：\n"
            "- 先说明作者的观点；\n"
            "- 再分析用户为什么会标记这一部分；\n"
            "- 综合用户批注，提炼用户自己的理解；\n"
            "- 尽可能发现用户关注的主题和思维模式。\n\n"
            "输出：\n"
            "# 一句话总结\n（作者观点 + 用户理解）\n\n"
            "# 本文解决什么问题\n（作者关注 + 用户关注）\n\n"
            "# 核心方法\n（作者方法 + 用户重点关注 + 用户自己的解释）\n\n"
            "# 主要结果\n（实验结果 + 用户评价）\n\n"
            "# 作者贡献\n（作者认为的贡献 + 用户认为真正有价值的地方）\n\n"
            "# 局限性\n（作者局限 + 用户疑问）\n\n"
            "# 我的收获\n（从所有笔记中提炼）\n\n"
            "# 可以继续探索的问题\n\n"
            "要求：\n"
            "不要复述，不要逐条整理笔记。\n"
            "要站在用户视角，把这篇文章变成用户自己的知识。"
        ),
        "题目整理": (
            "你是一位专业的学术助教。以下是用户对文章的全部笔记。\n"
            "请从中提取所有问题、推导过程和对应的解答。最重要的是：在列出具体题目之前，必须先总结出这些推导的「前情提要」！\n\n"
            "输出格式要求严格按照以下 Markdown 排版：\n"
            "## 📋 题目整理\n\n"
            "### 📖 前情提要\n"
            "请用精炼的语言概述这些题目涉及的模型背景、核心前提假设以及关键变量的设定（例如：已知某变量服从正态分布，方差未知等）。让读者即使不看原文献，也能明白接下来的题目在求解什么场景下的问题。\n\n"
            "### Q1: [具体问题内容或推导目标]\n"
            "**解答**：[完整推导或详细答案]\n\n"
            "### Q2: [具体问题内容或推导目标]\n"
            "**解答**：[完整推导或详细答案]\n\n"
            "要求：\n"
            "- 必须包含「前情提要」板块。\n"
            "- 提取出所有具有逻辑递进关系的问题和解答。\n"
            "- 严格保留所有的 LaTeX 公式（使用 $...$ 或 $$...$$）。\n"
            "- 不要生造笔记中完全没有提及的设定，依据笔记内容进行概括。"
        ),
        "知识图谱": (
            "你是一位知识图谱构建专家。以下是用户对文章的全部笔记。\n"
            "请从中提炼核心概念，生成一个拓扑结构的知识图谱，用 Mermaid flowchart 格式输出。\n\n"
            "输出格式：\n"
            "## 🕸️ 知识图谱\n\n"
            "### 核心主题\n"
            "一句话总结文章核心主题\n\n"
            "### 概念图谱\n"
            "```mermaid\n"
            "flowchart TD\n"
            "    A[核心概念A]\n"
            "    B[核心概念B]\n"
            "    C[核心概念C]\n"
            "    A --> B\n"
            "    B --> C\n"
            "    ...\n"
            "```\n\n"
            "### 关键关系说明\n"
            "用要点说明每条连线的含义\n\n"
            "要求：\n"
            "- Mermaid 节点用中文命名，关系箭头表达逻辑关联\n"
            "- 至少 5 个节点，覆盖笔记中的核心概念\n"
            "- 节点形状根据类型选择：核心概念用方框，方法/技术用圆角框，结论用菱形"
        ),
        "词汇表": (
            "你是一位专业术语词典编纂者。以下是用户对文章的全部笔记。\n"
            "请按照出现频率降序，提取所有专业名词和术语，中英对照。\n\n"
            "输出格式：\n"
            "## 📖 词汇表\n\n"
            "| 序号 | 中文术语 | 英文术语 | 出现频次 | 简要解释 |\n"
            "|------|---------|---------|---------|--------|\n"
            "| 1 | ... | ... | 3次 | ... |\n\n"
            "要求：\n"
            "- 只列出专业/学术术语，不包括日常词汇\n"
            "- 频次按笔记中实际出现次数估算\n"
            "- 如果只有中文没有英文，英文列填「-」\n"
            "- 简要解释一句话说清含义"
        ),
    }

    def _show_notes_dialog(self):
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        self._sync_notes_tree_state()

        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                        QLabel, QTreeWidget, QTreeWidgetItem, QComboBox,
                                        QTextEdit, QWidget, QMessageBox, QApplication, QStyle)
        from PySide6.QtCore import Qt
        import re

        folder_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        dialog = QDialog(self)
        dialog.setWindowTitle("📋 笔记处理引擎")
        dialog.resize(580, 680)
        dialog.setStyleSheet("""
            QDialog { background-color: #1E1E1E; }
            QLabel { color: #E0E0E0; }
            QTreeWidget {
                background-color: #252526; color: #D4D4D4;
                border: 1px solid #3E3E42; border-radius: 8px;
                padding: 6px; font-size: 13px;
            }
            QTreeWidget::item { padding: 4px; border-radius: 4px; }
            QTreeWidget::item:hover { background-color: #2A2D2E; }
            QTreeWidget::item:selected { background-color: #094771; color: #FFF; }
            QTreeWidget::indicator { width: 16px; height: 16px; }

            QTextEdit {
                background-color: #252526; color: #D4D4D4;
                border: 1px solid #3E3E42; border-radius: 6px;
                padding: 8px; font-size: 13px;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title_label = QLabel("范围选择：精准定位需要处理的笔记")
        title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFFFFF;")
        layout.addWidget(title_label)

        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        layout.addWidget(tree, 1)

        folders_data = self._load_notes_folders(path)
        folder_map = folders_data.get("mapping", {})
        folder_order = folders_data.get("folders", [])

        md_path = self._get_note_md_path(path)
        content = ""
        if md_path and os.path.exists(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except: pass

        blocks = re.split(r'\n(?=## (?:📌 )?笔记 - )', content)
        notes_by_folder = {f: [] for f in folder_order}
        root_notes = []

        for b in blocks:
            b = b.strip()
            if not b: continue
            ts_match = re.match(r'## (?:📌 )?笔记 - (.+)', b)
            ts = ts_match.group(1).strip() if ts_match else ""
            title = ts if ts else b[:30] + "..."
            target_f = folder_map.get(ts, "")

            if target_f and target_f in notes_by_folder:
                notes_by_folder[target_f].append((title, b))
            else:
                root_notes.append((title, b))

        root_item = QTreeWidgetItem(["选中全部文献笔记"])
        root_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        tree.addTopLevelItem(root_item)

        for title, block in root_notes:
            n_item = QTreeWidgetItem([title])
            n_item.setIcon(0, file_icon)
            n_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            n_item.setCheckState(0, Qt.Checked)
            n_item.setData(0, Qt.UserRole, block)
            root_item.addChild(n_item)

        for fname in folder_order:
            f_notes = notes_by_folder.get(fname, [])
            if not f_notes: continue

            f_item = QTreeWidgetItem([fname])
            f_item.setIcon(0, folder_icon)
            f_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            root_item.addChild(f_item)

            for title, block in f_notes:
                n_item = QTreeWidgetItem([title])
                n_item.setIcon(0, file_icon)
                n_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                n_item.setCheckState(0, Qt.Checked)
                n_item.setData(0, Qt.UserRole, block)
                f_item.addChild(n_item)

        root_item.setExpanded(True)
        for i in range(root_item.childCount()):
            root_item.child(i).setExpanded(True)

        func_label = QLabel("选择处理引擎")
        func_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #FFFFFF; margin-top: 8px;")
        layout.addWidget(func_label)

        combo = QComboBox()
        features = ["文献概述 📝", "题目整理 📋", "知识图谱 🕸️", "词汇表 📖", "自定义指令 ⚙️"]
        combo.addItems(features)
        combo.setCursor(Qt.PointingHandCursor)
        layout.addWidget(combo)

        engine_colors = {
            "文献概述 📝": "#2E8B57",
            "题目整理 📋": "#673AB7",
            "知识图谱 🕸️": "#8A1538",
            "词汇表 📖": "#E67E22",
            "自定义指令 ⚙️": "#708090"
        }

        desc_label = QLabel()
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #9E9E9E; font-size: 13px; line-height: 1.5; padding-left: 4px;")
        layout.addWidget(desc_label)

        custom_edit = QTextEdit()
        custom_edit.setPlaceholderText("在这里输入你给 AI 的自定义指令...")
        custom_edit.setMaximumHeight(80)
        custom_edit.setVisible(False)
        layout.addWidget(custom_edit)

        descriptions = {
            "文献概述 📝": "深度融合原文知识与您的个人批注，智能提炼核心论点、研究方法与思考，为您生成一篇高密度的沉淀式知识卡片。",
            "题目整理 📋": "精准扫描并提取您在阅读时产生的 Q&A 问答对，剔除杂乱信息，生成清晰的复习测试列表。",
            "知识图谱 🕸️": "抽取核心概念及它们之间的逻辑链条（因果、支撑、推导），为您构建出文章的拓扑思维架构。",
            "词汇表 📖": "提取高频出现的专业名词与生僻术语，自动中英对照并生成词典释义，扫清语言障碍。",
            "自定义指令 ⚙️": "打破常规，输入你自己的独特 Prompt。AI 将严格按照你的特殊指令去加工和重组选中的笔记。"
        }

        def on_combo_changed(text):
            desc_label.setText(descriptions.get(text, ""))
            custom_edit.setVisible("自定义" in text)

            current_color = engine_colors.get(text, "#708090")
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {current_color};
                    color: #FFFFFF;
                    border: none;
                    border-radius: 6px;
                    padding: 8px 12px;
                    font-size: 14px;
                    font-weight: bold;
                }}
                QComboBox::drop-down {{ border: none; }}

                QComboBox QAbstractItemView {{
                    background-color: #1E1E1E;
                    color: #FFFFFF;
                    border: 1px solid #3E3E42;
                    border-radius: 6px;
                    selection-background-color: #3E3E42;
                    outline: none;
                }}
            """)

        combo.currentTextChanged.connect(on_combo_changed)
        on_combo_changed(combo.currentText())

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 8, 0, 0)
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3E3E42; color: #E0E0E0;
                border: none; border-radius: 6px; padding: 10px 24px; font-size: 14px;
            }
            QPushButton:hover { background-color: #505050; }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        exec_btn = QPushButton("开始处理")
        exec_btn.setCursor(Qt.PointingHandCursor)
        exec_btn.setStyleSheet("""
            QPushButton {
                background-color: #000000; color: #FFFFFF;
                border: none; border-radius: 6px; padding: 10px 24px;
                font-size: 14px; font-weight: bold;
            }
            QPushButton:hover { background-color: #333333; }
        """)

        def do_execute():
            selected_blocks = []
            def collect_checked(item):
                if item.childCount() == 0:
                    if item.checkState(0) == Qt.Checked:
                        block = item.data(0, Qt.UserRole)
                        if block: selected_blocks.append(block)
                else:
                    for i in range(item.childCount()):
                        collect_checked(item.child(i))

            collect_checked(root_item)

            if not selected_blocks:
                QMessageBox.warning(dialog, "提示", "请在树状图中至少勾选一条笔记！")
                return

            sel_key = combo.currentText().split(" ")[0]
            custom = custom_edit.toPlainText().strip() if sel_key == "自定义指令" else ""
            dialog.accept()

            combined_text = "\n".join(selected_blocks)
            self._run_notes_feature_with_text(sel_key, combined_text, custom)

        exec_btn.clicked.connect(do_execute)
        btn_layout.addWidget(exec_btn)

        layout.addLayout(btn_layout)
        dialog.exec()

    def _run_notes_feature_with_text(self, prompt_key, notes_text, custom_prompt=""):
        """接收组合好的笔记文本，并发送给大模型进行特色功能处理"""
        if not notes_text:
            return

        self.knowledge_btn.setEnabled(False)
        self.knowledge_btn.setText("⏳ 引擎运转中...")
        import json as _json
        self.reader_browser.page().runJavaScript(f"renderNewText({_json.dumps('正在为您生成 ' + prompt_key + '，请稍候...')})")

        if prompt_key == "自定义指令":
            system_prompt = custom_prompt
        else:
            system_prompt = self.NOTES_FEATURE_PROMPTS.get(prompt_key, self.NOTES_FEATURE_PROMPTS["文献概述"])

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"以下是我对文章的全部笔记摘录，请按要求处理：\n\n{notes_text}"},
        ]

        try:
            if hasattr(self, '_kg_worker') and self._kg_worker and self._kg_worker.isRunning():
                self._kg_worker.quit()
                self._kg_worker.wait(500)
        except RuntimeError:
            self._kg_worker = None

        self._kg_worker = ChatWorker(messages)
        self._kg_worker.finished_signal.connect(lambda text: self._on_notes_feature_result(text, prompt_key))
        self._kg_worker.error_signal.connect(lambda e: self._on_notes_feature_error(str(e), prompt_key))
        self._kg_worker.start()

    def _on_notes_feature_result(self, text, prompt_key=""):
        path = getattr(self, '_current_pdf_path', None)
        if path:
            base_dir, full_name = os.path.split(os.path.abspath(path))
            doc_name, _ = os.path.splitext(full_name)
            safe_key = prompt_key if prompt_key else "笔记功能"
            output_name = f"【{safe_key}】{doc_name}.pdf"
            pdf_path = os.path.join(base_dir, output_name)
        else:
            safe_key = prompt_key.replace(" ", "_") if prompt_key else "笔记功能"
            output_name = f"{safe_key}.pdf"
            pdf_path = os.path.join(LIT_DIR, output_name)

        def open_result(p):
            idx = load_literature_index()
            tree = idx["tree"]
            name = os.path.basename(p)
            if path:
                found = self._find_folder_for_file(tree, path)
                if found:
                    _, folder_node = found
                else:
                    folder_node = tree.setdefault("__root__", {"files": [], "children": {}})
            else:
                folder_node = tree.setdefault("__root__", {"files": [], "children": {}})
            if name not in [f["name"] for f in folder_node.get("files", [])]:
                folder_node.setdefault("files", []).append({"name": name, "path": p})
            save_literature_index(idx)
            self._refresh_lit_tree()
            self._refresh_reader_file_list()
            self._open_in_reader(p, name)

        self._render_md_to_pdf(text, pdf_path, callback=open_result)
        self.knowledge_btn.setEnabled(True)
        self.knowledge_btn.setText("📋 笔记功能")

    def _on_notes_feature_error(self, err, prompt_key=""):
        import json as _json
        self.reader_browser.page().runJavaScript(f"renderNewText({_json.dumps('笔记功能生成失败: ' + err)})")
        self.knowledge_btn.setEnabled(True)
        self.knowledge_btn.setText("📋 笔记功能")

    def _render_md_to_pdf(self, md_text, output_path, callback=None):
        lines_json = json.dumps(md_text)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script> <script>window.MathJax={{tex:{{inlineMath:[['$','$'],['\\(','\\)']],displayMath:[['$$','$$'],['\\\\[','\\\\]']]}},
svg:{{fontCache:'global'}},options:{{enableMenu:false}}}}</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:false,theme:'default'}});</script>
<style>
body{{font-family:'Times New Roman','Kaiti SC',serif;color:#1D1D1F;font-size:14px;line-height:1.9;
padding:50px 55px;max-width:780px;margin:0 auto}}
h1{{font-size:22px;margin:28px 0 12px}}h2{{font-size:17px;margin:22px 0 8px}}
p{{margin:8px 0}}strong{{color:#1D1D1F}}
pre[class*="mermaid"]{{background:#f8f8ff;padding:16px;border-radius:8px}}

/* ===== 新增：优雅的表格样式 ===== */
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
th, td {{ border: 1px solid #D1D1D6; padding: 10px 12px; text-align: left; }}
th {{ background-color: #F5F5F7; font-weight: bold; color: #1D1D1F; }}
tr:nth-child(even) {{ background-color: #FAFAFC; }}
/* ================================= */

</style></head><body><div id="kg"></div>
<script>
var md = {lines_json};
// 核心修复：使用 marked 解析替代原本简陋的正则替换
document.getElementById('kg').innerHTML = marked.parse(md);

var promises = [MathJax.typesetPromise([document.body])];
var mermaidEls = document.querySelectorAll('pre code.language-mermaid, pre[class*="mermaid"]');
if (mermaidEls.length > 0) {{
    mermaidEls.forEach(function(el) {{
        var code = el.textContent || el.innerText;
        var div = document.createElement('div');
        div.className = 'mermaid';
        div.textContent = code;
        el.parentNode.replaceWith(div);
    }});
    promises.push(mermaid.run({{nodes: document.querySelectorAll('.mermaid')}}));
}}
Promise.all(promises).then(function(){{ window._kgReady = true; }}).catch(function(){{ window._kgReady = true; }});
</script></body></html>"""
        viewer = QWebEngineView()
        viewer.resize(800, 900)
        viewer.setHtml(html)
        def poll_and_print(tries=0):
            if tries > 60:
                viewer.deleteLater()
                return
            viewer.page().runJavaScript("window._kgReady", lambda v: _do_pdf(viewer, output_path, callback) if v else QTimer.singleShot(500, lambda: poll_and_print(tries + 1)))
        QTimer.singleShot(1500, lambda: poll_and_print())

    def _open_note_editor(self, item, block):
        path = getattr(self, '_current_pdf_path', None)
        from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                        QPushButton, QPlainTextEdit, QSplitter, QMessageBox)
        from PySide6.QtGui import QFont
        import json

        dialog = QDialog(self)
        dialog.setWindowTitle("Markdown 笔记编辑器")
        dialog.resize(960, 600)
        dialog.setStyleSheet("QDialog { background-color: #F5F5F7; }")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet("QSplitter::handle { background-color: #D1D1D6; }")

        editor = QPlainTextEdit()
        editor.setPlainText(block)

        font = QFont("Menlo", 13)
        font.setStyleHint(QFont.Monospace)
        editor.setFont(font)
        editor.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1E1E1E; color: #D4D4D4;
                border: 1px solid #D1D1D6; border-radius: 8px;
                padding: 12px; line-height: 1.6;
            }
        """)
        splitter.addWidget(editor)

        preview = QWebEngineView()
        preview.page().setBackgroundColor(Qt.transparent)
        splitter.addWidget(preview)

        splitter.setSizes([480, 480])
        layout.addWidget(splitter, 1)

        def update_preview():
            md_text = editor.toPlainText()
            html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <script>window.MathJax={{tex:{{inlineMath:[['$','$'],['\\(','\\)']],displayMath:[['$$','$$'],['\\\\[','\\\\]']]}},
            svg:{{fontCache:'global'}},options:{{enableMenu:false}}}};</script>
            <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
            <style>
                body {{ font-family: 'Times New Roman', 'Kaiti SC', sans-serif; color: #1D1D1F;
                       font-size: 15px; line-height: 1.8; padding: 10px 16px; background: transparent; }}
                ::-webkit-scrollbar {{ display: none; }}
                pre {{ background: #f4f4f4; padding: 12px; border-radius: 6px; overflow-x: auto; }}
                code {{ font-family: Menlo, monospace; font-size: 13px; background: #eee; padding: 2px 4px; border-radius: 4px; }}
                blockquote {{ border-left: 4px solid #007AFF; margin: 0; padding-left: 12px; color: #555; background: #f0f8ff; padding: 8px 12px; border-radius: 0 6px 6px 0; }}
            </style></head>
            <body><div id="content"></div>
            <script>
                document.getElementById('content').innerHTML = marked.parse({json.dumps(md_text)});
                MathJax.typesetPromise([document.getElementById('content')]);
            </script></body></html>"""
            preview.setHtml(html)

        update_preview()

        preview_timer = QTimer()
        preview_timer.setSingleShot(True)
        preview_timer.timeout.connect(update_preview)
        editor.textChanged.connect(lambda: preview_timer.start(500))

        btn_layout = QHBoxLayout()
        btn_style = "QPushButton { border: none; border-radius: 6px; padding: 8px 24px; font-size: 13px; font-weight: 500; }"
        delete_btn = QPushButton("🗑 删除笔记")
        delete_btn.setStyleSheet(btn_style + "QPushButton { background-color: #FF5F56; color: #FFF; } QPushButton:hover { background-color: #FF2E22; }")

        close_btn = QPushButton("完成并保存")
        close_btn.setStyleSheet(btn_style + "QPushButton { background-color: #007AFF; color: #FFF; } QPushButton:hover { background-color: #005BBF; }")

        def do_save():
            nonlocal block
            new_content = editor.toPlainText().strip()
            if not new_content or new_content == block.strip():
                return
            md_path = self._get_note_md_path(path) if path else None
            if not md_path:
                return
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()

                if block in content:
                    content = content.replace(block, new_content)
                else:
                    content += "\n\n" + new_content

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(content)

                item.setData(0, Qt.UserRole + 1, new_content)
                block = new_content
            except Exception as e:
                QMessageBox.warning(dialog, "保存失败", str(e))

        dialog.finished.connect(lambda: do_save())
        delete_btn.clicked.connect(lambda: [dialog.reject(), QTimer.singleShot(150, lambda: self._delete_note(item))])
        close_btn.clicked.connect(dialog.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(delete_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.exec()

    def _delete_note_folder(self, item):
        fdata = item.data(0, Qt.UserRole + 1)
        fname = fdata[1] if isinstance(fdata, tuple) else item.text(0)
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(self, "确认删除", f"确定删除文件夹 '{fname}'？笔记会移至「未分类」", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        data = self._load_notes_folders(path)
        folders = data.get("folders", [])
        if fname in folders:
            folders.remove(fname)
        mapping = data.get("mapping", {})
        for ts, fn in list(mapping.items()):
            if fn == fname:
                del mapping[ts]
        self._save_notes_folders(path, data)
        self._refresh_notes(path)

    def _note_context_menu(self, pos):
        item = self.reader_note_list.itemAt(pos)
        if not item:
            return
        item_type = item.data(0, Qt.UserRole + 1)
        is_folder = isinstance(item_type, tuple) and item_type[0] == "folder"
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        if is_folder:
            rename_action = menu.addAction("重命名文件夹")
            delete_action = menu.addAction("删除文件夹")
        else:
            rename_action = menu.addAction("重命名笔记")
            delete_action = menu.addAction("删除笔记")
        action = menu.exec(self.reader_note_list.viewport().mapToGlobal(pos))
        if action == rename_action:
            self._rename_note(item)
        elif action == delete_action:
            if is_folder:
                self._delete_note_folder(item)
            else:
                self._delete_note(item)

    def _rename_note(self, item):
        old_ts = item.data(0, Qt.UserRole)
        if not old_ts:
            return
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "重命名笔记", "新名称:", text=old_ts)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        md_path = self._get_note_md_path(path)
        if md_path and os.path.exists(md_path):
            try:
                import re
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = re.sub(rf'## (?:📌 )?笔记 - {re.escape(old_ts)}', f'## 笔记 - {new_name}', content)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(content)
            except:
                pass
        annot_path = self._get_annot_path(path)
        if annot_path and os.path.exists(annot_path):
            try:
                with open(annot_path, "r", encoding="utf-8") as f:
                    ann_data = json.load(f)
                for a in ann_data.get("annotations", []):
                    if a.get("created") == old_ts:
                        a["created"] = new_name
                with open(annot_path, "w", encoding="utf-8") as f:
                    json.dump(ann_data, f, ensure_ascii=False, indent=2)
            except:
                pass
        self._refresh_notes(path)

    def _delete_note(self, item):
        ts = item.data(0, Qt.UserRole)
        if not ts:
            return
        path = getattr(self, '_current_pdf_path', None)
        if not path:
            return
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(self, "确认删除", f"确定删除笔记 {ts}？", QMessageBox.Yes | QMessageBox.No)

        if reply != QMessageBox.Yes:
            return

        md_path = self._get_note_md_path(path)
        if md_path and os.path.exists(md_path):
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()

                import re
                blocks = re.split(r'\n(?=## (?:📌 )?笔记 - )', content)
                new_blocks = []
                for b in blocks:
                    if not b.strip():
                        continue
                    ts_match = re.match(r'## (?:📌 )?笔记 - (.+)', b.strip())
                    block_ts = ts_match.group(1).strip() if ts_match else ""

                    if block_ts != ts:
                        new_blocks.append(b)

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(new_blocks))
            except Exception as e:
                print(f"Error updating md: {e}")

        annot_path = self._get_annot_path(path)
        if annot_path and os.path.exists(annot_path):
            try:
                with open(annot_path, "r", encoding="utf-8") as f:
                    old_anns = json.load(f).get("annotations", [])

                removed_ids = [a.get("id", "") for a in old_anns if a.get("created", "") == ts]
                new_anns = [a for a in old_anns if a.get("created", "") != ts]

                with open(annot_path, "w", encoding="utf-8") as f:
                    json.dump({"annotations": new_anns}, f, ensure_ascii=False, indent=2)

                for rid in removed_ids:
                    if rid:
                        self.reader_pdf_view.page().runJavaScript(f"removeAnnotation('{rid}')")
            except Exception as e:
                print(f"Error updating annots: {e}")

        # 清理文件夹映射
        folders_data = self._load_notes_folders(path)
        folders_data.get("mapping", {}).pop(ts, None)
        self._save_notes_folders(path, folders_data)

        self._refresh_notes(path)

    def _move_associated_files(self, old_path, new_path):
        old_base, _ = os.path.splitext(old_path)
        new_base, _ = os.path.splitext(new_path)
        for suffix in ["_notes.md", "_annots.json", "_知识图谱.pdf"]:
            old_file = old_base + suffix
            new_file = new_base + suffix
            if os.path.exists(old_file):
                try:
                    os.rename(old_file, new_file)
                except:
                    pass

    def _delete_associated_files(self, path):
        base, _ = os.path.splitext(path)
        for suffix in ["_notes.md", "_annots.json", "_知识图谱.pdf", "_notes_folders.json"]:
            fpath = base + suffix
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except:
                    pass

    def showNormal(self):
        super().showNormal()
        self._refresh_stats()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = None


class MacNativeHelper:
    _available = None

    @classmethod
    def _ensure(cls):
        if cls._available is not None:
            return cls._available
        if sys.platform != "darwin":
            cls._available = False
            return False
        try:
            import AppKit
            cls._ak = AppKit
            cls.TypeLeftMouseDown = AppKit.NSEventTypeLeftMouseDown
            cls.TypeRightMouseDown = AppKit.NSEventTypeRightMouseDown
            cls.TypeFlagsChanged = AppKit.NSEventTypeFlagsChanged
            cls.TypeKeyUp = AppKit.NSEventTypeKeyUp
            cls.TypeKeyDown = AppKit.NSEventTypeKeyDown
            cls.MaskFlagsChanged = AppKit.NSEventMaskFlagsChanged
            cls.MaskKeyDown = AppKit.NSEventMaskKeyDown
            cls.MaskKeyUp = AppKit.NSEventMaskKeyUp
            cls.MaskLeftMouseDown = AppKit.NSEventMaskLeftMouseDown
            cls.MaskRightMouseDown = AppKit.NSEventMaskRightMouseDown
            cls._available = True
            return True
        except ImportError:
            cls._available = False
            return False

    @classmethod
    def set_collection_behavior(cls, window_title, behavior):
        if not cls._ensure(): return
        try:
            for win in cls._ak.NSApp.windows():
                if str(win.title()) == window_title:
                    win.setCollectionBehavior_(behavior)
        except Exception as e:
            print(f"macOS 权限注入失败: {e}", flush=True)

    @classmethod
    def add_global_monitor(cls, mask, handler):
        if not cls._ensure(): return None
        return cls._ak.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, handler)

    @classmethod
    def add_local_monitor(cls, mask, handler):
        if not cls._ensure(): return None
        return cls._ak.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, handler)

    @classmethod
    def remove_monitor(cls, monitor):
        if not cls._ensure() or monitor is None: return
        cls._ak.NSEvent.removeMonitor_(monitor)

    @classmethod
    def activate_app(cls):
        if not cls._ensure(): return
        cls._ak.NSApp.activateIgnoringOtherApps_(True)


class GlobalInputMonitor(QObject):
    sig_screenshot_requested = Signal()
    sig_toggle_requested = Signal()
    sig_external_clicked = Signal()

    _KEYCODE_MAP = {
        0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x",
        8: "c", 9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r",
        16: "y", 17: "t", 18: "1", 19: "2", 20: "3", 21: "4",
        22: "5", 23: "6", 24: "7", 25: "8", 26: "9", 27: "0",
        28: "-", 29: "=", 30: "[", 31: "o", 32: "u", 33: "]",
        34: "i", 35: "p", 37: "l", 38: "j", 39: "'", 40: "k",
        41: ";", 42: "\\", 43: ",", 44: "/", 45: "n", 46: "m",
        47: ".", 48: "tab", 49: "space", 50: "`", 51: "backspace",
        53: "escape",
    }

    def __init__(self):
        super().__init__()
        self._event_monitor = None
        self._local_monitor = None
        self._hotkey_fired = set()
        self._cmd_down = False
        self._shift_down = False
        self._alt_down = False
        self._ctrl_down = False

    def start(self):
        QTimer.singleShot(1000, self._do_start)

    def stop(self):
        MacNativeHelper.remove_monitor(self._event_monitor)
        self._event_monitor = None
        MacNativeHelper.remove_monitor(self._local_monitor)
        self._local_monitor = None

    def _do_start(self):
        if not MacNativeHelper._ensure():
            return
        if self._event_monitor is not None:
            return
        self._hotkey_fired.clear()
        self._cmd_down = False
        self._shift_down = False
        self._alt_down = False
        self._ctrl_down = False

        mask = (MacNativeHelper.MaskFlagsChanged | MacNativeHelper.MaskKeyDown | MacNativeHelper.MaskKeyUp |
                MacNativeHelper.MaskLeftMouseDown | MacNativeHelper.MaskRightMouseDown)
        wself = self

        def process_event(event, is_global):
            try:
                etype = event.type()

                if etype in (MacNativeHelper.TypeLeftMouseDown, MacNativeHelper.TypeRightMouseDown):
                    if is_global:
                        QTimer.singleShot(0, wself.sig_external_clicked.emit)
                    return False

                flags = event.modifierFlags()
                kc = event.keyCode()

                if etype == MacNativeHelper.TypeFlagsChanged:
                    wself._cmd_down = bool(flags & 0x100000)
                    wself._shift_down = bool(flags & 0x20000)
                    wself._alt_down = bool(flags & 0x80000)
                    wself._ctrl_down = bool(flags & 0x40000)
                    fired_key = f"{wself._cmd_down}:{wself._shift_down}:{wself._alt_down}:{wself._ctrl_down}"
                    wself._hotkey_fired = {k for k in wself._hotkey_fired if k.startswith(fired_key + ":")}
                    return False

                if etype == MacNativeHelper.TypeKeyUp:
                    wself._hotkey_fired = {k for k in wself._hotkey_fired if not k.endswith(f":{kc}")}
                    return False

                if etype == MacNativeHelper.TypeKeyDown:
                    chars = event.charactersIgnoringModifiers()
                    char = chars.lower() if chars else None
                    return wself._check_hotkey(kc, char)
            except Exception as e:
                print(f"monitor error: {e}", flush=True)
            return False

        def global_handler(event):
            process_event(event, True)
            return None

        def local_handler(event):
            if process_event(event, False):
                return None
            return event

        self._event_monitor = MacNativeHelper.add_global_monitor(mask, global_handler)
        self._local_monitor = MacNativeHelper.add_local_monitor(mask, local_handler)

    def _check_hotkey(self, key_code, event_char):
        hk = settings.get("hotkey", "<cmd>+k")
        tk = settings.get("toggle_hotkey", "<cmd>+`")

        cmd = self._cmd_down
        shift = self._shift_down
        alt = self._alt_down
        ctrl = self._ctrl_down

        actual_char = event_char
        if not actual_char:
            actual_char = self._KEYCODE_MAP.get(key_code, "").lower()

        for spec, action_type in [
            (hk, "screenshot"),
            (tk, "toggle"),
        ]:
            needs_cmd, needs_ctrl, needs_alt, needs_shift, key_char = _parse_mods(spec)

            if (cmd != needs_cmd) or (shift != needs_shift) or (alt != needs_alt) or (ctrl != needs_ctrl):
                continue

            if key_char:
                map_char = self._KEYCODE_MAP.get(key_code, "").lower()
                is_match = (actual_char and actual_char.lower() == key_char) or (map_char == key_char)
                if not is_match:
                    continue

            fired_key = f"{cmd}:{shift}:{alt}:{ctrl}:{key_code}"
            if fired_key in self._hotkey_fired:
                continue
            self._hotkey_fired.add(fired_key)

            if action_type == "screenshot":
                QTimer.singleShot(0, self.sig_screenshot_requested.emit)
            elif action_type == "toggle":
                QTimer.singleShot(0, self.sig_toggle_requested.emit)
            return True
        return False


# ==========================================
# 4. 前端 UI：毛玻璃窗口（弹窗模式 + 对话）
# ==========================================
class FloatingTranslatorView(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("FloatingTranslator")
        self.resize(850, 700)
        self._ignore_deactivate = False
        self.installEventFilter(self)

        from PySide6.QtCore import QSettings
        self._ws = QSettings("LatexTranslator", "FloatingWindow")
        if self._ws.value("geometry"):
            self.restoreGeometry(self._ws.value("geometry"))

        QTimer.singleShot(0, lambda: MacNativeHelper.set_collection_behavior("FloatingTranslator", 17))

    def closeEvent(self, event):
        self._ws.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    def hideEvent(self, event):
        self._ws.setValue("geometry", self.saveGeometry())
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        MacNativeHelper.set_collection_behavior("FloatingTranslator", 17)

    def eventFilter(self, obj, event):
        if obj == self and not self._ignore_deactivate and not getattr(self, '_screenshot_in_progress', True):
            if event.type() == QEvent.WindowDeactivate:
                if self.isVisible() and not self.isMinimized():
                    self.smooth_hide()
        return super().eventFilter(obj, event)

    def pop_up(self):
        if not self.isVisible() or self.isMinimized():
            self.showNormal()
            self.raise_()

    def smooth_hide(self):
        if self.isVisible() and not self.isMinimized():
            self.showMinimized()

    def render_content(self, chunk):
        pass


class LatexAppWindow(FloatingTranslatorView):
    def __init__(self, tray_icon_ref):
        super().__init__()
        self.tray_icon_ref = tray_icon_ref

        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(logo_path):
            self.setWindowIcon(QIcon(logo_path))

        self.chat_history = []

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame()
        self.container.setObjectName("translucentPanel")
        self.container.setStyleSheet("""
            QFrame#translucentPanel {
                background-color: rgba(255, 255, 255, 220);
                border-radius: 12px;
                border: 1px solid rgba(60, 60, 67, 20);
            }
        """)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 10, 10)
        container_layout.setSpacing(6)

        title_bar_bg = QFrame()
        title_bar_bg.setObjectName("titleBarBg")
        title_bar_bg.setStyleSheet("QFrame#titleBarBg { background-color: rgba(255,255,255,0); }")
        title_bar_bg_layout = QVBoxLayout(title_bar_bg)
        title_bar_bg_layout.setContentsMargins(10, 10, 0, 0)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(5, 0, 5, 0)
        title_bar.setSpacing(8)

        self.btn_min = QPushButton()
        self.btn_min.setFixedSize(14, 14)
        self.btn_min.setStyleSheet("QPushButton { background-color: #FFBD2E; border-radius: 7px; border: none; } QPushButton:hover { background-color: #E6A213; }")
        self.btn_min.clicked.connect(self.showMinimized)
        title_bar.addWidget(self.btn_min)

        self.btn_toggle_mode = QPushButton("⇱ 主控台")
        self.btn_toggle_mode.setFixedHeight(26)
        self.btn_toggle_mode.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.btn_toggle_mode.clicked.connect(self._switch_to_main)
        title_bar.addWidget(self.btn_toggle_mode)

        title_bar.addStretch()

        self.btn_screenshot = QPushButton("截图识别")
        self.btn_screenshot.setFixedHeight(26)
        self.btn_screenshot.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.btn_screenshot.clicked.connect(self._trigger_screenshot)
        title_bar.addWidget(self.btn_screenshot)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setFixedHeight(26)
        self.btn_settings.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED; color: #1D1D1F;
                border: none; border-radius: 6px; padding: 4px 12px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #D1D1D6; }
        """)
        self.btn_settings.clicked.connect(self._show_settings)
        title_bar.addWidget(self.btn_settings)

        title_bar_bg_layout.addLayout(title_bar)
        container_layout.addWidget(title_bar_bg)

        # 分割器：翻译结果 + 对话区域可拖拽调整
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background-color: rgba(255,255,255,30); }")

        # 上半部分：翻译结果
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.floating_translation_view = QWebEngineView(self)
        self.floating_translation_view.page().setBackgroundColor(Qt.transparent)
        self.floating_translation_view.setHtml(self._floating_translation_html())
        top_layout.addWidget(self.floating_translation_view)
        top_widget.setMinimumHeight(150)
        splitter.addWidget(top_widget)

        # 下半部分：对话区域
        bottom_widget = QWidget()
        bottom_widget.setStyleSheet("background-color: #FFFFFF; border-radius: 8px;")
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 6, 0, 0)
        bottom_layout.setSpacing(6)

        chat_header = QHBoxLayout()
        chat_label = QLabel("💬 解疑对话")
        chat_label.setStyleSheet("color: #6E6E73; font-size: 11px; padding-left: 2px;")
        chat_header.addWidget(chat_label)
        chat_header.addStretch()

        self.clear_chat_btn = QPushButton("清空对话")
        self.clear_chat_btn.setFixedSize(70, 22)
        self.clear_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: #E8E8ED;
                color: #1D1D1F;
                border: none;
                border-radius: 4px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #D1D1D6;
            }
        """)
        self.clear_chat_btn.clicked.connect(self._clear_chat)
        chat_header.addWidget(self.clear_chat_btn)
        bottom_layout.addLayout(chat_header)

        # 对话历史显示区（支持 LaTeX 渲染）
        self.chat_browser = QWebEngineView(self)
        self.chat_browser.setMinimumHeight(80)
        self.chat_browser.page().setBackgroundColor(Qt.transparent)
        chat_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <script>
                window.MathJax = {
                    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']] },
                    svg: { fontCache: 'global' },
                    options: { enableMenu: false, renderActions: { assistiveMml: [], enrich: [], addTex: [150, (doc) => { for (const math of doc.math) { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } }, (math) => { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } ] } }
                };

                function addChat(role, text) {
                    document.getElementById('empty-state').style.display = 'none';
                    const box = document.getElementById('chat-container');

                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'message ' + (role === '你' ? 'user' : 'ai');

                    const bubble = document.createElement('div');
                    bubble.className = 'bubble';
                    bubble.innerHTML = text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');

                    msgDiv.appendChild(bubble);
                    box.appendChild(msgDiv);

                    MathJax.typesetPromise([bubble]).then(() => {
                        updateScrollButton();
                    });
                }

                function clearChat() {
                    document.getElementById('chat-container').innerHTML = '';
                    document.getElementById('empty-state').style.display = 'block';
                }

                function showTyping() {
                    hideTyping();
                    document.getElementById('empty-state').style.display = 'none';
                    const box = document.getElementById('chat-container');
                    const indicator = document.createElement('div');
                    indicator.id = 'typing-indicator';
                    indicator.className = 'typing-indicator';
                    indicator.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
                    box.appendChild(indicator);
                    box.scrollTop = box.scrollHeight;
                }
                function hideTyping() {
                    const el = document.getElementById('typing-indicator');
                    if (el) el.remove();
                }
                function scrollToTop() {
                    document.getElementById('chat-container').scrollTop = 0;
                    updateScrollButton();
                }
                function updateScrollButton() {
                    const box = document.getElementById('chat-container');
                    const btn = document.getElementById('scroll-top-btn');
                    if (!btn) return;
                    btn.style.display = box.scrollTop > 200 ? 'flex' : 'none';
                }
                (function() {
                    const container = document.getElementById('chat-container');
                    if (container) container.addEventListener('scroll', updateScrollButton);
                })();

                document.addEventListener('copy', function(e) {
                    const sel = window.getSelection();
                    if (!sel.rangeCount) return;
                    const frag = sel.getRangeAt(0).cloneContents();
                    const div = document.createElement('div');
                    div.style.position = 'absolute';
                    div.style.left = '-9999px';
                    div.appendChild(frag);
                    document.body.appendChild(div);
                    div.querySelectorAll('mjx-container').forEach(function(mjx) {
                        const tex = mjx.getAttribute('data-tex');
                        const isDisplay = mjx.getAttribute('data-display') === 'true';
                        if (tex) {
                            const span = document.createElement('span');
                            span.textContent = isDisplay ? '\\n$$' + tex + '$$\\n' : '$' + tex + '$';
                            mjx.parentNode.replaceChild(span, mjx);
                        }
                    });
                    const text = div.innerText;
                    document.body.removeChild(div);
                    e.clipboardData.setData('text/plain', text);
                    e.preventDefault();
                });
            </script>
            <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #FFFFFF;
                    margin: 0; padding: 16px;
                    height: 100vh; box-sizing: border-box;
                    display: flex; flex-direction: column;
                    position: relative;
                }
                #empty-state {
                    position: absolute; top: 40%; left: 50%; transform: translate(-50%, -50%);
                    color: #A0A0A5; font-size: 14px; text-align: center;
                    line-height: 1.6; max-width: 80%; font-weight: 400;
                }
                #chat-container {
                    flex: 1; overflow-y: auto; padding-right: 4px;
                    display: flex; flex-direction: column; gap: 24px;
                }
                .message { display: flex; flex-direction: column; width: 100%; }
                .message.user { align-items: flex-end; }
                .message.ai { align-items: flex-start; }
                .bubble { max-width: 88%; font-size: 15px; line-height: 1.6; word-wrap: break-word; color: #0D0D0D; }

                .message.user .bubble {
                    background-color: #F4F4F4; padding: 10px 16px;
                    border-radius: 18px; border-bottom-right-radius: 4px;
                }
                .message.ai .bubble { background-color: transparent; padding: 0px 4px; }
                .message.ai .bubble p { margin: 0 0 10px 0; }
                .message.ai .bubble p:last-child { margin-bottom: 0; }

                .typing-indicator { display: flex; gap: 4px; padding: 4px 4px; align-items: center; }
                .typing-indicator .dot { width: 6px; height: 6px; border-radius: 50%; background: #A0A0A5; animation: bounce 1.4s infinite ease-in-out both; }
                .typing-indicator .dot:nth-child(1) { animation-delay: 0s; }
                .typing-indicator .dot:nth-child(2) { animation-delay: 0.2s; }
                .typing-indicator .dot:nth-child(3) { animation-delay: 0.4s; }
                @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

                #scroll-top-btn { position: fixed; bottom: 80px; right: 24px; width: 36px; height: 36px; border-radius: 18px; background: rgba(0,0,0,0.5); color: #FFF; border: none; font-size: 18px; display: none; align-items: center; justify-content: center; cursor: pointer; z-index: 100; }
                #scroll-top-btn:hover { background: rgba(0,0,0,0.7); }

                ::-webkit-scrollbar { width: 6px; }
                ::-webkit-scrollbar-thumb { background: #E5E5E5; border-radius: 3px; }
                ::-webkit-scrollbar-thumb:hover { background: #CCCCCC; }
            </style>
        </head>
        <body>
            <div id="empty-state">不时为拾到更光滑的石子或更美丽的贝壳而欢欣鼓舞。</div>
            <div id="chat-container"></div>
            <div id="scroll-top-btn" onclick="scrollToTop()">↑</div>
        </body>
        </html>
        """
        self.chat_browser.setHtml(chat_html)
        bottom_layout.addWidget(self.chat_browser)
        bottom_widget.setMinimumHeight(100)
        splitter.addWidget(bottom_widget)

        splitter.setSizes([600, 400])
        container_layout.addWidget(splitter)

        # 对话输入区（现代胶囊 UI）
        chat_input_layout = QHBoxLayout()
        chat_input_layout.setContentsMargins(10, 8, 10, 12)
        chat_input_layout.setSpacing(10)

        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("还想了解点什么？")
        self.chat_input.setStyleSheet("""
            QLineEdit {
                background-color: #FFFFFF; color: #0D0D0D;
                border: 1px solid #E5E5E5; border-radius: 20px;
                padding: 10px 18px; font-size: 14px;
            }
            QLineEdit:focus { border: 1px solid #B4B4B4; }
        """)
        self.chat_input.returnPressed.connect(self._send_chat)
        chat_input_layout.addWidget(self.chat_input)

        self.send_btn = QPushButton("↑")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #000000; color: #FFFFFF;
                border: none; border-radius: 18px;
                font-size: 18px; font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover { background-color: #333333; }
            QPushButton:pressed { background-color: #000000; }
        """)
        self.send_btn.clicked.connect(self._send_chat)
        chat_input_layout.addWidget(self.send_btn)

        container_layout.addLayout(chat_input_layout)

        main_layout.addWidget(self.container)
        self.old_pos = None

        self._screenshot_in_progress = False

    def _on_screenshot_hotkey(self):
        active_w = QApplication.activeWindow()
        if active_w and type(active_w).__name__ == "MainPanel":
            if active_w.stack.currentIndex() == 3:
                active_w._reader_take_screenshot()
            else:
                self._trigger_screenshot()
        else:
            self._trigger_screenshot()

    def _on_toggle_hotkey(self):
        active_w = QApplication.activeWindow()
        if active_w and type(active_w).__name__ == "MainPanel":
            active_w._switch_to_floating()
        else:
            self._toggle_window()

    def _on_external_clicked(self):
        if self.isVisible() and not self.isMinimized() and not self._screenshot_in_progress:
            self.smooth_hide()



    def _toggle_window(self):
        self._ignore_deactivate = True
        if self.isVisible() and not self.isMinimized():
            self.smooth_hide()
            QTimer.singleShot(300, lambda: setattr(self, '_ignore_deactivate', False))
        else:
            for w in QApplication.topLevelWidgets():
                if type(w).__name__ == "MainPanel":
                    w.hide()

            self.pop_up()
            QTimer.singleShot(500, lambda: setattr(self, '_ignore_deactivate', False))

    def _trigger_screenshot(self):
        for w in QApplication.topLevelWidgets():
            if type(w).__name__ == "MainPanel":
                w.hide()

        try:
            if hasattr(self, '_ocr_worker') and self._ocr_worker and self._ocr_worker.isRunning():
                return
        except RuntimeError:
            self._ocr_worker = None

        self._screenshot_in_progress = True
        self._ignore_deactivate = True
        self.floating_translation_view.page().runJavaScript("startStream();")
        self._ocr_worker = OCRWorker()

        self._ocr_worker.chunk_signal.connect(self.show_latex_chunk)
        self._ocr_worker.finished_signal.connect(self.show_latex)

        self._ocr_worker.error_signal.connect(self.show_error)
        self._ocr_worker.start()

    def _floating_translation_html(self):
        return """
        <!DOCTYPE html>
        <html><head><meta charset="utf-8">
        <script>
        window.MathJax = { tex: { inlineMath: [['$','$'], ['\\(', '\\)']], displayMath: [['$$','$$'], ['\\\\[', '\\\\]']] }, svg: { fontCache: 'global' }, options: { enableMenu: false, renderActions: { assistiveMml: [], enrich: [], addTex: [150, (doc) => { for (const math of doc.math) { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } }, (math) => { if (math.typesetRoot) { math.typesetRoot.setAttribute('data-tex', math.math); math.typesetRoot.setAttribute('data-display', math.display); } } ] } } };
        let currentRawText = ""; let renderTimer = null;
        function startStream() { const box = document.getElementById('cb'); if(!box)return; box.style.opacity = 1; currentRawText = ""; box.innerHTML = "大模型正在思考..."; }
        function appendStreamChunk(chunk) { const box = document.getElementById('cb'); if(!box)return; if(currentRawText==="") box.innerHTML=""; currentRawText += chunk; let ht = currentRawText.replace(/</g,'&lt;').replace(/>/g,'&gt;'); ht = ht.replace(/\\n*\\$\\$/g, '$$$$').replace(/\\$\\$\\n*/g, '$$$$'); box.innerHTML = ht.replace(/\\n/g, '<br>'); clearTimeout(renderTimer); renderTimer = setTimeout(()=>{ if(typeof MathJax!=='undefined')MathJax.typesetPromise([box]).catch(()=>{}); }, 200); }
        function finishStream() { const box = document.getElementById('cb'); if(!box)return; clearTimeout(renderTimer); if(typeof MathJax!=='undefined')MathJax.typesetPromise([box]).catch(()=>{}); }
        async function renderNewText(text) { startStream(); appendStreamChunk(text); finishStream(); }
        document.addEventListener('copy', function(e) { const sel = window.getSelection(); if (!sel.rangeCount) return; const frag = sel.getRangeAt(0).cloneContents(); const div = document.createElement('div'); div.style.position = 'absolute'; div.style.left = '-9999px'; div.appendChild(frag); document.body.appendChild(div); div.querySelectorAll('mjx-container').forEach(function(mjx) { const tex = mjx.getAttribute('data-tex'); const isDisplay = mjx.getAttribute('data-display') === 'true'; if (tex) { const span = document.createElement('span'); span.textContent = isDisplay ? '\\n$$' + tex + '$$\\n' : '$' + tex + '$'; mjx.parentNode.replaceChild(span, mjx); } }); const text = div.innerText; document.body.removeChild(div); e.clipboardData.setData('text/plain', text); e.preventDefault(); });
        </script>
        <script type="text/javascript" src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
        <style>
        body{font-family:'Times New Roman','Kaiti SC','STKaiti',serif;color:#1D1D1F;font-size:19px;line-height:1.8;padding:10px 20px;background:transparent;margin:0;text-align:justify}
        mjx-container[display="true"]{margin:0.5em 0!important;display:block}
        mjx-container{-webkit-user-select:all;user-select:all}
        mjx-container svg{pointer-events:none}
        ::-webkit-scrollbar{display:none}
        </style>
        </head>
        <body><div id="cb">等候翻译结果...</div></body>
        </html>
        """

    def _show_settings(self):
        for w in QApplication.topLevelWidgets():
            if isinstance(w, MainPanel):
                w.showNormal()
                w.raise_()
                w.activateWindow()
                w._switch_tab("config")
                break

    def _switch_to_main(self):
        from PySide6.QtWidgets import QApplication
        mp = None
        for w in QApplication.topLevelWidgets():
            if isinstance(w, MainPanel):
                mp = w
                break
        if mp is None:
            mp = MainPanel()
        self._ignore_deactivate = True
        self.hide()
        mp.showNormal()
        mp.raise_()
        mp.activateWindow()

    def show_latex_chunk(self, chunk):
        self.pop_up()

        import json
        self.floating_translation_view.page().runJavaScript(f"appendStreamChunk({json.dumps(chunk)});")

    def show_latex(self, text):
        self._screenshot_in_progress = False
        import json
        safe_text = json.dumps(text)
        self.floating_translation_view.page().runJavaScript("finishStream();")

        self.chat_history = [{"role": "system", "content": f"以下是翻译结果，供后续对话参考：\n{text}"}]
        self.chat_browser.page().runJavaScript("clearChat();")
        # 同步更新主控台文献阅读板块
        for w in QApplication.topLevelWidgets():
            if isinstance(w, MainPanel):
                if hasattr(w, 'reader_browser'):
                    w.reader_browser.page().runJavaScript(f"renderNewText({safe_text});")
                w._reader_chat_history = [{"role": "system", "content": f"以下是翻译结果，供后续对话参考：\n{text}"}]
                if hasattr(w, 'reader_chat_browser'):
                    w.reader_chat_browser.page().runJavaScript("clearChat();")
                if hasattr(w, 'reader_note_btn'):
                    w.reader_note_btn.setEnabled(True)
                if hasattr(w, 'reader_copy_btn'):
                    w.reader_copy_btn.setEnabled(True)
                    w._current_translation = text
                if hasattr(w, '_switch_tab'):
                    w._switch_tab("reader")

        if not self.isVisible() or self.isMinimized():
            self.pop_up()
        QTimer.singleShot(800, lambda: setattr(self, '_ignore_deactivate', False))

    def show_error(self, text):
        self.show_latex(f"⚠️ 识别失败: {text}")

    def _send_chat(self):
        question = self.chat_input.text().strip()
        if not question:
            return
        self.chat_input.clear()

        question_safe = json.dumps(question)
        self.chat_browser.page().runJavaScript(f"addChat('你', {question_safe});")

        messages = list(self.chat_history)

        instruction = settings.get("qa_prompt", "")
        combined_question = f"{question}\n\n【系统指令】：\n{instruction}"

        messages.append({"role": "user", "content": combined_question})

        self.chat_history.append({"role": "user", "content": question})

        try:
            if hasattr(self, '_chat_worker') and self._chat_worker and self._chat_worker.isRunning():
                self._chat_worker.quit()
                self._chat_worker.wait(500)
        except RuntimeError:
            self._chat_worker = None

        self._chat_worker = ChatWorker(messages)
        self._chat_worker.finished_signal.connect(self._on_chat_response)
        self._chat_worker.error_signal.connect(lambda e: self.chat_browser.page().runJavaScript(f"hideTyping(); addChat('错误', {json.dumps(e)});"))
        self._chat_worker.start()
        self.chat_browser.page().runJavaScript("showTyping();")

    def _on_chat_response(self, text):
        self.chat_browser.page().runJavaScript("hideTyping();")
        safe_text = json.dumps(text)
        self.chat_browser.page().runJavaScript(f"addChat('AI', {safe_text});")
        self.chat_history.append({"role": "assistant", "content": text})

    def _clear_chat(self):
        self.chat_history.clear()
        self.chat_browser.page().runJavaScript("clearChat();")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = event.globalPosition().toPoint() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        self.old_pos = None


# ==========================================
# 5. 生成动态托盘图标
# ==========================================
def create_tray_icon():
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_icon.png")
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setBrush(QColor(80, 80, 80))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, 18, 18)
    painter.end()
    return QIcon(pixmap)

def create_app_icon():
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.png")
    return QIcon(icon_path)


# ==========================================
# 6. 中枢神经
# ==========================================
class AppController:
    def __init__(self):
        self.main_panel = MainPanel()
        self.floating_window = LatexAppWindow(None)
        self.main_panel._floating_window = self.floating_window

        self.input_monitor = GlobalInputMonitor()
        self.input_monitor.sig_screenshot_requested.connect(self.floating_window._on_screenshot_hotkey)
        self.input_monitor.sig_toggle_requested.connect(self.floating_window._on_toggle_hotkey)
        self.input_monitor.sig_external_clicked.connect(self.floating_window._on_external_clicked)
        self.input_monitor.start()

    def show_main(self):
        self.floating_window._ignore_deactivate = True
        self.floating_window.hide()
        self.main_panel.showNormal()
        self.main_panel.raise_()
        self.main_panel.activateWindow()

    def show_floating(self):
        self.main_panel.hide()
        self.floating_window._ignore_deactivate = True
        self.floating_window.showNormal()
        self.floating_window.raise_()
        self.floating_window.activateWindow()
        QTimer.singleShot(500, lambda: setattr(self.floating_window, '_ignore_deactivate', False))

    def start(self):
        self.main_panel.showNormal()
        self.main_panel.raise_()
        self.main_panel.activateWindow()
        self.floating_window.hide()


# ==========================================
def main():
    import traceback
    try:
        app = QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        ctrl = AppController()
        main_panel = ctrl.main_panel
        floating_window = ctrl.floating_window

        ctrl.start()

        app.setWindowIcon(create_app_icon())

        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if tray_available:
            tray_icon = QSystemTrayIcon(create_tray_icon(), app)
            tray_menu = QMenu()

            action_trigger = QAction("截图识别", app)
            action_trigger.triggered.connect(lambda: (
                ctrl.show_floating(),
                QTimer.singleShot(500, floating_window._trigger_screenshot)
            ))

            action_main = QAction("主控台", app)
            action_main.triggered.connect(ctrl.show_main)

            action_floating = QAction("悬浮窗模式", app)
            action_floating.triggered.connect(ctrl.show_floating)

            action_quit = QAction("彻底退出", app)
            action_quit.triggered.connect(app.quit)

            tray_menu.addAction(action_trigger)
            tray_menu.addAction(action_main)
            tray_menu.addAction(action_floating)
            tray_menu.addSeparator()
            tray_menu.addAction(action_quit)

            tray_icon.setContextMenu(tray_menu)
            tray_icon.show()

        print("✅ 终极大一统引擎启动成功！", flush=True)
        print(f"👉 截图快捷键: {settings.get('hotkey', '<cmd>+k')}", flush=True)
        print(f"👉 弹窗快捷键: {settings.get('toggle_hotkey', '<cmd>+`')}", flush=True)
        if not tray_available and sys.platform == "darwin":
            print("ℹ️  macOS 需要打包为 .app 才能显示托盘图标。当前通过 Dock 图标管理。", flush=True)
            print("   建议: 使用 py2app 打包或按 Cmd+H 隐藏窗口后通过快捷键唤出。", flush=True)

        sys.exit(app.exec())
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
