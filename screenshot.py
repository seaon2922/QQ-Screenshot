# -*- coding: utf-8 -*-
"""
QQ 风格区域截图工具 v4
- 快捷键 Ctrl+Alt+A
- 选区后弹出工具栏：红圈/矩形/箭头/画笔/文字/撤销/保存/复制/完成
- 自动保存到 D:\\Desktop
"""
import sys, os, traceback, json
from datetime import datetime

# 打包后配置/日志写到 exe 所在目录；开发时写到脚本目录
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(_BASE_DIR, "error.log")
CONFIG_PATH = os.path.join(_BASE_DIR, "config.json")
def _excepthook(t, v, tb):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== {datetime.now()} ===\n")
        traceback.print_exception(t, v, tb, file=f)
sys.excepthook = _excepthook

from PyQt5.QtWidgets import (QApplication, QWidget, QMessageBox,
                             QSystemTrayIcon, QMenu, QAction, QPushButton,
                             QHBoxLayout, QColorDialog, QInputDialog, QFileDialog)
from PyQt5.QtCore import Qt, QRect, QPoint, QTimer
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QCursor, QPolygon
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_A = 0x41
HOTKEY_ID = 1

DEFAULT_SAVE_DIR = r"D:\Desktop"

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg.get("save_dir", DEFAULT_SAVE_DIR)
    except Exception:
        return None  # 首次使用

def save_config(save_dir):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"save_dir": save_dir}, f, ensure_ascii=False, indent=2)

SAVE_DIR = load_config() or DEFAULT_SAVE_DIR
PEN_COLOR = QColor(255, 60, 60)   # 默认红色
PEN_WIDTH = 3


def grab_desktop() -> QPixmap:
    screen = QApplication.primaryScreen()
    geo = QApplication.desktop().geometry()
    return screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())


class Toolbar(QWidget):
    """浮动工具栏，放在选区下方"""
    TOOL_BUTTONS = {"ellipse", "rect", "arrow", "pen", "text"}

    def __init__(self, parent, on_action):
        super().__init__(parent)
        self.on_action = on_action
        self.buttons = {}
        self.setWindowFlags(Qt.Widget)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        buttons = [
            ("⭕", "ellipse", "红圈"),
            ("▭", "rect", "矩形"),
            ("➡", "arrow", "箭头"),
            ("✏", "pen", "画笔"),
            ("T", "text", "文字"),
            ("↶", "undo", "撤销"),
            ("💾", "save", "保存"),
            ("📋", "copy", "复制"),
            ("✓", "done", "完成"),
        ]
        for text, key, tip in buttons:
            b = QPushButton(text, self)
            b.setFixedSize(64, 56)
            b.setToolTip(tip)
            if key in self.TOOL_BUTTONS:
                b.setCheckable(True)
                b.clicked.connect(lambda checked, k=key: self.on_action(k))
            else:
                b.clicked.connect(lambda _, k=key: self.on_action(k))
            self.buttons[key] = b
            lay.addWidget(b)
        self.setStyleSheet(
            "QWidget{background:rgba(40,40,40,230);border-radius:8px;}"
            "QPushButton{color:white;border:none;font-size:24px;"
            "border-radius:6px;background:transparent;}"
            "QPushButton:hover{background:rgba(90,90,90,230);}"
            "QPushButton:checked{background:rgba(42,130,218,230);"
            "color:white;border:2px solid rgba(120,200,255,255);}"
        )
        self.adjustSize()

    def set_active_tool(self, tool):
        """激活工具按钮（发光），其他工具按钮取消"""
        for k, b in self.buttons.items():
            if k in self.TOOL_BUTTONS:
                b.setChecked(k == tool)


class Selector(QWidget):
    def __init__(self, bg):
        super().__init__()
        self.bg = bg
        self.origin = None       # 选区起点
        self.current = None      # 选区当前点
        self.sel_rect = None     # 确定后的选区
        self.annots = []         # 标注列表
        self.tool = None         # 当前绘制工具
        self.draft = None        # 正在绘制的标注 (start, current)
        self.toolbar = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(QCursor(Qt.CrossCursor))
        geo = QApplication.desktop().geometry()
        self.setGeometry(geo)

    # ---------- 鼠标事件 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.close(); return
        if e.button() != Qt.LeftButton: return

        # 阶段1：还没选区，开始拖拽选区
        if self.sel_rect is None:
            self.origin = e.pos()
            self.current = e.pos()
            self.update()
            return

        # 阶段2：已选区，用当前工具绘制标注
        if self.tool:
            self.draft = (e.pos(), e.pos())

    def mouseMoveEvent(self, e):
        if self.sel_rect is None:
            if self.origin is not None:
                self.current = e.pos()
                self.update()
            return
        if self.draft is not None:
            self.draft = (self.draft[0], e.pos())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton: return

        # 阶段1：完成选区
        if self.sel_rect is None:
            if self.origin is None: return
            self.current = e.pos()
            rect = QRect(self.origin, self.current).normalized()
            if rect.width() < 3 or rect.height() < 3:
                self.close(); return
            self.sel_rect = rect
            self.origin = None
            self.current = None
            self._show_toolbar()
            self.update()
            return

        # 阶段2：完成一个标注
        if self.draft is not None:
            start, end = self.draft
            if self.tool == "text":
                text, ok = QInputDialog.getText(self, "文字标注", "输入文字:")
                if ok and text:
                    self.annots.append(("text", start, None, text))
            else:
                # 过滤掉太小的误点
                if (end - start).manhattanLength() > 3:
                    self.annots.append((self.tool, start, end, None))
            self.draft = None
            self.update()

    # ---------- 工具栏动作 ----------
    def _show_toolbar(self):
        self.toolbar = Toolbar(self, self._on_tool)
        # 工具栏放在选区下方中央
        x = self.sel_rect.center().x() - self.toolbar.width() // 2
        y = self.sel_rect.bottom() + 8
        # 如果超出屏幕底部，放到选区上方
        if y + self.toolbar.height() > self.height():
            y = self.sel_rect.top() - self.toolbar.height() - 8
        self.toolbar.move(x, y)
        self.toolbar.show()

    def _on_tool(self, action):
        if action == "undo":
            if self.annots:
                self.annots.pop()
                self.update()
            return
        if action == "save":
            self._save_clipboard(copy_only=False)
            return
        if action == "copy":
            self._save_clipboard(copy_only=True)
            return
        if action == "done":
            self._save_clipboard(copy_only=False)
            self.close()
            return
        # 工具按钮：再点一次取消激活
        if action == self.tool:
            self.tool = None
        else:
            self.tool = action
        self.toolbar.set_active_tool(self.tool)
        self.setCursor(QCursor(Qt.CrossCursor) if self.tool else QCursor(Qt.ArrowCursor))

    # ---------- 保存 ----------
    def _grab_selection(self) -> QPixmap:
        dpr = self.bg.devicePixelRatio()
        return self.bg.copy(
            int(self.sel_rect.x()*dpr), int(self.sel_rect.y()*dpr),
            int(self.sel_rect.width()*dpr), int(self.sel_rect.height()*dpr))

    def _render_annots(self, pix: QPixmap) -> QPixmap:
        """把标注画到选区 pixmap 上（坐标相对选区）"""
        p = QPainter(pix)
        p.setPen(QPen(PEN_COLOR, PEN_WIDTH))
        off = self.sel_rect.topLeft()
        for kind, start, end, text in self.annots:
            s = start - off
            if kind == "text":
                p.setPen(QPen(PEN_COLOR, 1))
                p.setFont(QFont("Microsoft YaHei", 12))
                p.drawText(s, text)
                continue
            e = end - off
            if kind == "ellipse":
                from PyQt5.QtCore import QRectF
                p.drawEllipse(QRectF(s, e))
            elif kind == "rect":
                p.drawRect(QRect(s, e))
            elif kind == "arrow":
                p.drawLine(s, e)
                # 箭头头部
                import math
                angle = math.atan2(e.y()-s.y(), e.x()-s.x())
                size = 12
                p1 = QPoint(int(e.x()-size*math.cos(angle-0.4)),
                            int(e.y()-size*math.sin(angle-0.4)))
                p2 = QPoint(int(e.x()-size*math.cos(angle+0.4)),
                            int(e.y()-size*math.sin(angle+0.4)))
                p.drawPolygon(QPolygon([e, p1, p2]))
            elif kind == "pen":
                # 画笔：单段线（简化版，多段可扩展）
                p.drawLine(s, e)
        # 草稿中的标注
        if self.draft:
            s = self.draft[0] - off
            e = self.draft[1] - off
            p.setPen(QPen(PEN_COLOR, PEN_WIDTH, Qt.DashLine))
            if self.tool == "ellipse":
                from PyQt5.QtCore import QRectF
                p.drawEllipse(QRectF(s, e))
            elif self.tool == "rect":
                p.drawRect(QRect(s, e))
            elif self.tool == "arrow":
                p.drawLine(s, e)
            elif self.tool == "pen":
                p.drawLine(s, e)
        p.end()
        return pix

    def _next_index(self) -> int:
        """找下一个编号：扫描 SAVE_DIR 下 N.png，距上次>10分钟则从1开始"""
        import re, time
        os.makedirs(SAVE_DIR, exist_ok=True)
        max_n = 0
        max_mtime = 0
        for f in os.listdir(SAVE_DIR):
            m = re.fullmatch(r"(\d+)\.png", f, re.IGNORECASE)
            if not m: continue
            n = int(m.group(1))
            fp = os.path.join(SAVE_DIR, f)
            mt = os.path.getmtime(fp)
            if n > max_n:
                max_n = n
                max_mtime = mt
        # 距上次截图超过 10 分钟（600 秒）则从头开始
        if max_n == 0 or (time.time() - max_mtime) > 600:
            return 1
        return max_n + 1

    def _save_clipboard(self, copy_only: bool):
        pix = self._grab_selection()
        pix = self._render_annots(pix)
        QApplication.clipboard().setPixmap(pix)
        os.makedirs(SAVE_DIR, exist_ok=True)
        n = self._next_index()
        path = os.path.join(SAVE_DIR, f"{n}.png")
        pix.save(path, "PNG")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() == Qt.Key_Return:
            self._on_tool("done")

    # ---------- 绘制全屏遮罩 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.bg)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))

        # 选区内挖空
        if self.sel_rect:
            dpr = self.bg.devicePixelRatio()
            p.drawPixmap(self.sel_rect, self.bg, QRect(
                int(self.sel_rect.x()*dpr), int(self.sel_rect.y()*dpr),
                int(self.sel_rect.width()*dpr), int(self.sel_rect.height()*dpr)))
            p.setPen(QPen(QColor(42,130,218), 2))
            p.drawRect(self.sel_rect)
            p.setPen(QColor(255,255,255))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.sel_rect.topLeft()+QPoint(5,-8),
                       f"{self.sel_rect.width()} × {self.sel_rect.height()}")
        elif self.origin and self.current:
            # 正在拖拽选区
            rect = QRect(self.origin, self.current).normalized()
            dpr = self.bg.devicePixelRatio()
            p.drawPixmap(rect, self.bg, QRect(
                int(rect.x()*dpr), int(rect.y()*dpr),
                int(rect.width()*dpr), int(rect.height()*dpr)))
            p.setPen(QPen(QColor(42,130,218), 2))
            p.drawRect(rect)

        # 实时绘制已完成的标注（全屏坐标）
        if self.annots:
            p.setPen(QPen(PEN_COLOR, PEN_WIDTH))
            for kind, start, end, text in self.annots:
                self._draw_annot_fullscreen(p, kind, start, end, text)
        # 实时绘制正在拖拽的草稿
        if self.draft:
            p.setPen(QPen(PEN_COLOR, PEN_WIDTH, Qt.DashLine))
            self._draw_annot_fullscreen(p, self.tool, self.draft[0], self.draft[1], None)

    def _draw_annot_fullscreen(self, p, kind, start, end, text):
        """在全屏坐标下画一个标注"""
        from PyQt5.QtCore import QRectF
        import math
        if kind == "text":
            p.setPen(QPen(PEN_COLOR, 1))
            p.setFont(QFont("Microsoft YaHei", 12))
            p.drawText(start, text)
            return
        if kind == "ellipse":
            p.drawEllipse(QRectF(start, end))
        elif kind == "rect":
            p.drawRect(QRect(start, end))
        elif kind == "arrow":
            p.drawLine(start, end)
            angle = math.atan2(end.y()-start.y(), end.x()-start.x())
            size = 12
            p1 = QPoint(int(end.x()-size*math.cos(angle-0.4)),
                        int(end.y()-size*math.sin(angle-0.4)))
            p2 = QPoint(int(end.x()-size*math.cos(angle+0.4)),
                        int(end.y()-size*math.sin(angle+0.4)))
            p.drawPolygon(QPolygon([end, p1, p2]))
        elif kind == "pen":
            p.drawLine(start, end)


class HotkeyWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.resize(400, 300)
        self.setWindowOpacity(0.0)
        self.show()

        hwnd = int(self.winId())
        ok = user32.RegisterHotKey(
            hwnd, HOTKEY_ID,
            MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
            VK_A
        )
        if not ok:
            QMessageBox.warning(None, "截图工具",
                "注册热键 Ctrl+Alt+A 失败！\n可能被其他软件（如 QQ）占用。")

    def nativeEvent(self, eventType, message):
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.take_screenshot()
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def take_screenshot(self):
        bg = grab_desktop()
        self.sel = Selector(bg)
        self.sel.show()
        # --test 模式：自动模拟选区+标注
        if "--test" in sys.argv:
            QTimer.singleShot(500, self._auto_test)

    def _auto_test(self):
        sel = self.sel
        # 自动选一个 800x500 的区域（屏幕中央偏左上）
        from PyQt5.QtCore import QRect
        sel.sel_rect = QRect(400, 300, 900, 550)
        sel.origin = None
        sel.current = None
        sel._show_toolbar()
        # 自动加几个标注
        sel.annots = [
            ("ellipse", QPoint(500, 400), QPoint(800, 600), None),
            ("arrow",   QPoint(900, 500), QPoint(1100, 700), None),
            ("text",    QPoint(450, 750), None, "测试标注 ABC"),
            ("rect",    QPoint(600, 800), QPoint(1000, 900), None),
        ]
        sel.update()
        # 2 秒后自动保存
        QTimer.singleShot(2000, lambda: sel._save_clipboard(False))


def choose_save_dir(parent=None):
    """弹目录选择框，确认后更新全局 SAVE_DIR 并写配置"""
    global SAVE_DIR
    d = QFileDialog.getExistingDirectory(parent, "选择截图保存目录", SAVE_DIR)
    if d:
        SAVE_DIR = d
        save_config(d)
        QMessageBox.information(parent, "设置成功", f"截图将保存到：\n{d}")
    return d

def main():
    global SAVE_DIR
    # 单实例检测：命名互斥体
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    mutex = kernel32.CreateMutexW(None, False, "Global\\QQScreenshotTool_Mutex")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        app = QApplication(sys.argv)
        QMessageBox.information(None, "截图工具",
            "截图工具已经在运行中！\n\n按 Ctrl+Alt+A 即可截图。")
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    win = HotkeyWindow()

    tray = QSystemTrayIcon()
    tray.setIcon(app.style().standardIcon(app.style().SP_ComputerIcon))
    tray.setToolTip("截图工具 (Ctrl+Alt+A)")
    menu = QMenu()
    act_shot = QAction("立即截图", menu)
    act_dir = QAction("设置保存目录", menu)
    act_quit = QAction("退出", menu)
    act_shot.triggered.connect(win.take_screenshot)
    act_dir.triggered.connect(lambda: choose_save_dir(win))
    act_quit.triggered.connect(app.quit)
    menu.addAction(act_shot)
    menu.addAction(act_dir)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: win.take_screenshot() if r == QSystemTrayIcon.Trigger else None)
    tray.show()

    # 首次使用：没有配置文件，弹目录选择
    if not os.path.exists(CONFIG_PATH):
        QTimer.singleShot(300, lambda: (
            QMessageBox.information(None, "欢迎使用截图工具",
                "首次使用，请选择截图保存目录。"),
            choose_save_dir(win)
        ))

    tray.showMessage("截图工具", f"已启动，按 Ctrl+Alt+A 截图\n保存到：{SAVE_DIR}",
                     QSystemTrayIcon.Information, 2000)

    if "--test" in sys.argv:
        QTimer.singleShot(1500, win.take_screenshot)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
