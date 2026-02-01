"""
AutoClicker - Универсальный автокликер для игр и приложений
Работает при зажатии кнопок мыши, включается/выключается горячей клавишей
УЛУЧШЕННАЯ ВЕРСИЯ: высокий приоритет процесса, SendInput API, права администратора
"""

import sys
import threading
import time
import json
import os
import random
import winsound  # Для звуковых уведомлений
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QSlider, QFrame, QSystemTrayIcon, QMenu, QAction,
    QTabWidget, QCheckBox, QGroupBox
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QIcon, QPalette, QColor

import ctypes
import ctypes.wintypes

# pynput больше не нужен - используем только Windows API


# ============ ПОВЫШЕНИЕ ПРИОРИТЕТА ПРОЦЕССА ============
def set_high_priority():
    """Устанавливает высокий приоритет процесса"""
    try:
        # Приоритеты Windows
        # IDLE_PRIORITY_CLASS = 0x40
        # BELOW_NORMAL_PRIORITY_CLASS = 0x4000
        # NORMAL_PRIORITY_CLASS = 0x20
        # ABOVE_NORMAL_PRIORITY_CLASS = 0x8000
        # HIGH_PRIORITY_CLASS = 0x80
        # REALTIME_PRIORITY_CLASS = 0x100 (опасно, может подвесить систему)
        
        HIGH_PRIORITY_CLASS = 0x80
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
        print("[OK] Приоритет процесса установлен на HIGH")
    except Exception as e:
        print(f"[WARN] Не удалось установить приоритет: {e}")


def set_thread_high_priority():
    """Устанавливает высокий приоритет текущего потока"""
    try:
        THREAD_PRIORITY_HIGHEST = 2
        THREAD_PRIORITY_TIME_CRITICAL = 15
        handle = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(handle, THREAD_PRIORITY_HIGHEST)
    except Exception:
        pass


# ============ ЗАПУСК ОТ АДМИНИСТРАТОРА ============
def is_admin():
    """Проверяет, запущено ли приложение с правами администратора"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """Перезапускает приложение с правами администратора"""
    if sys.platform == 'win32':
        try:
            if getattr(sys, 'frozen', False):
                script = sys.executable
            else:
                script = os.path.abspath(sys.argv[0])
            
            params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
            
            # ShellExecute с runas
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{script}" {params}', None, 1
            )
            if ret > 32:
                sys.exit(0)
        except Exception as e:
            print(f"Ошибка запуска от администратора: {e}")


# ============ СТРУКТУРЫ ДЛЯ SendInput ============
# SendInput работает лучше чем mouse_event в играх с DirectInput

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

INPUT_MOUSE = 0

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


def send_mouse_input(flags):
    """Отправляет событие мыши через SendInput (низкоуровневый API)"""
    extra = ctypes.pointer(ctypes.wintypes.ULONG(0))
    ii = INPUT_UNION()
    ii.mi = MOUSEINPUT(0, 0, 0, flags, 0, extra)
    inp = INPUT(INPUT_MOUSE, ii)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

# Virtual Key Codes
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02


# ============ СИГНАЛЫ ДЛЯ QT ============
class SignalBridge(QObject):
    """Мост для передачи сигналов между потоками и Qt"""
    status_changed = pyqtSignal(bool)
    hotkey_captured = pyqtSignal(str)


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# ============ НИЗКОУРОВНЕВЫЙ ХУКА МЫШИ ============
# Это единственный способ перехватить физическое нажатие и заменить его

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205

# Тип callback функции для хука
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", ctypes.wintypes.POINT),
        ("mouseData", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class ClickerEngine:
    """Движок автокликера для MINECRAFT
    
    Методы отправки (в порядке приоритета):
    1. PostMessage в окно Minecraft (работает даже в фоне)
    2. mouse_event как fallback
    """
    
    # Window messages
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONDOWN = 0x0204
    WM_RBUTTONUP = 0x0205
    
    def __init__(self, left_cps: int = 12, right_cps: int = 12, left_randomize: int = 0, right_randomize: int = 0, randomization_enabled: bool = False):
        self.left_cps = left_cps
        self.right_cps = right_cps
        self.left_randomize = left_randomize
        self.right_randomize = right_randomize
        self.randomization_enabled = randomization_enabled
        self.enabled = False
        self.running = True
        self.minecraft_hwnd = None
        
        # Запускаем поток кликов
        self.click_thread = threading.Thread(target=self._click_loop, daemon=True)
        self.click_thread.start()
        
        # Поток для поиска окна Minecraft
        self.find_thread = threading.Thread(target=self._find_minecraft_loop, daemon=True)
        self.find_thread.start()
    
    def _find_minecraft_loop(self):
        """Постоянно ищет окно Minecraft"""
        while self.running:
            self._find_minecraft_window()
            time.sleep(2)  # Проверяем каждые 2 секунды
    
    def _find_minecraft_window(self):
        """Находит окно Minecraft"""
        # Ищем по разным названиям окон
        titles = [
            "Minecraft",
            "Minecraft 1.8",
            "Minecraft 1.8.9",
            "Lunar Client",
            "Badlion Client",
            "Feather Client",
        ]
        
        for title in titles:
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if hwnd:
                self.minecraft_hwnd = hwnd
                return
        
        # Пробуем найти по частичному совпадению
        def enum_callback(hwnd, _):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value.lower()
                if 'minecraft' in title or 'lunar' in title or 'badlion' in title:
                    self.minecraft_hwnd = hwnd
                    return False
            return True
        
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(enum_callback), 0)
    
    def set_left_cps(self, cps: int):
        self.left_cps = max(1, min(100, cps))

    def set_right_cps(self, cps: int):
        self.right_cps = max(1, min(100, cps))
    
    def set_left_randomize(self, percent: int):
        self.left_randomize = max(0, min(100, percent))

    def set_right_randomize(self, percent: int):
        self.right_randomize = max(0, min(100, percent))

    def set_randomization_enabled(self, enabled: bool):
        self.randomization_enabled = enabled
    
    def set_enabled(self, enabled: bool):
        self.enabled = enabled
    
    def _precise_sleep(self, seconds: float):
        """Высокоточное ожидание с использованием busy-wait для точности"""
        if seconds <= 0:
            return
        
        # Используем QueryPerformanceCounter для высокой точности
        end_time = time.perf_counter() + seconds
        
        # Сначала sleep для основной части (экономит CPU)
        if seconds > 0.002:
            time.sleep(seconds - 0.002)
        
        # Затем busy-wait для точности последних миллисекунд
        while time.perf_counter() < end_time:
            pass
    
    def _send_click(self, is_left: bool):
        """Отправляет клик - пробует несколько методов"""
        # Минимальная фиксированная задержка между down/up (10ms - достаточно для регистрации)
        CLICK_HOLD_TIME = 0.010
        
        # Метод 1: PostMessage в окно Minecraft (если найдено)
        if self.minecraft_hwnd:
            try:
                if is_left:
                    ctypes.windll.user32.PostMessageW(self.minecraft_hwnd, self.WM_LBUTTONDOWN, 0x0001, 0)
                    self._precise_sleep(CLICK_HOLD_TIME)
                    ctypes.windll.user32.PostMessageW(self.minecraft_hwnd, self.WM_LBUTTONUP, 0, 0)
                else:
                    ctypes.windll.user32.PostMessageW(self.minecraft_hwnd, self.WM_RBUTTONDOWN, 0x0002, 0)
                    self._precise_sleep(CLICK_HOLD_TIME)
                    ctypes.windll.user32.PostMessageW(self.minecraft_hwnd, self.WM_RBUTTONUP, 0, 0)
                return
            except:
                pass
        
        # Метод 2: mouse_event (fallback)
        if is_left:
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            self._precise_sleep(CLICK_HOLD_TIME)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
        else:
            ctypes.windll.user32.mouse_event(0x0008, 0, 0, 0, 0)
            self._precise_sleep(CLICK_HOLD_TIME)
            ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0)
    
    def _click_loop(self):
        """Основной цикл кликов с высокоточным таймингом"""
        set_thread_high_priority()
        
        # Время следующего клика для каждой кнопки
        next_left_click = 0.0
        next_right_click = 0.0
        
        while self.running:
            if self.enabled:
                current_time = time.perf_counter()
                left_held = bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
                right_held = bool(ctypes.windll.user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
                
                clicked = False
                
                # Обработка ЛКМ
                if left_held:
                    if current_time >= next_left_click:
                        self._send_click(is_left=True)
                        clicked = True
                        
                        # Вычисляем интервал
                        base_interval = 1.0 / self.left_cps
                        if self.randomization_enabled and self.left_randomize > 0:
                            delta = base_interval * (self.left_randomize / 100.0)
                            interval = base_interval + random.uniform(-delta, delta)
                            interval = max(0.010, interval)
                        else:
                            interval = base_interval
                        
                        next_left_click = current_time + interval
                else:
                    # Сброс таймера при отпускании
                    next_left_click = 0.0
                
                # Обработка ПКМ
                if right_held:
                    if current_time >= next_right_click:
                        self._send_click(is_left=False)
                        clicked = True
                        
                        # Вычисляем интервал
                        base_interval = 1.0 / self.right_cps
                        if self.randomization_enabled and self.right_randomize > 0:
                            delta = base_interval * (self.right_randomize / 100.0)
                            interval = base_interval + random.uniform(-delta, delta)
                            interval = max(0.010, interval)
                        else:
                            interval = base_interval
                        
                        next_right_click = current_time + interval
                else:
                    # Сброс таймера при отпускании
                    next_right_click = 0.0
                
                # Короткое ожидание для проверки состояния (высокоточное)
                if not clicked:
                    # Busy-wait для минимальной задержки
                    time.sleep(0.001)
            else:
                time.sleep(0.01)
    
    def stop(self):
        self.running = False


# ============ VIRTUAL KEY CODES ДЛЯ КЛАВИАТУРЫ ============
# F-клавиши
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B

# Маппинг названий клавиш к VK кодам
KEY_MAP = {
    'f1': VK_F1, 'f2': VK_F2, 'f3': VK_F3, 'f4': VK_F4,
    'f5': VK_F5, 'f6': VK_F6, 'f7': VK_F7, 'f8': VK_F8,
    'f9': VK_F9, 'f10': VK_F10, 'f11': VK_F11, 'f12': VK_F12,
    # Буквы
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45,
    'f': 0x46, 'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A,
    'k': 0x4B, 'l': 0x4C, 'm': 0x4D, 'n': 0x4E, 'o': 0x4F,
    'p': 0x50, 'q': 0x51, 'r': 0x52, 's': 0x53, 't': 0x54,
    'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58, 'y': 0x59, 'z': 0x5A,
    # Цифры
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    # Специальные
    'space': 0x20, 'enter': 0x0D, 'escape': 0x1B, 'tab': 0x09,
    'shift': 0x10, 'ctrl': 0x11, 'alt': 0x12,
    'insert': 0x2D, 'delete': 0x2E, 'home': 0x24, 'end': 0x23,
    'pageup': 0x21, 'pagedown': 0x22,
    'numpad0': 0x60, 'numpad1': 0x61, 'numpad2': 0x62, 'numpad3': 0x63,
    'numpad4': 0x64, 'numpad5': 0x65, 'numpad6': 0x66, 'numpad7': 0x67,
    'numpad8': 0x68, 'numpad9': 0x69,
}

# Обратный маппинг для захвата клавиш
VK_TO_NAME = {v: k for k, v in KEY_MAP.items()}


class HotkeyManager:
    """Менеджер горячих клавиш - ИСПОЛЬЗУЕТ НИЗКОУРОВНЕВЫЙ WINDOWS API
    Работает в любых играх, включая полноэкранные DirectX/Vulkan"""
    
    def __init__(self, callback, signal_bridge: SignalBridge):
        self.callback = callback
        self.signal_bridge = signal_bridge
        self.current_hotkey = 'f6'
        self.current_vk = KEY_MAP.get('f6', VK_F6)
        self.capturing = False
        self.running = True
        self.key_was_pressed = False  # Для отслеживания отпускания
        
        # Запускаем поток опроса клавиш
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()
    
    def _poll_loop(self):
        """Цикл опроса клавиатуры через GetAsyncKeyState - работает ВЕЗДЕ"""
        set_thread_high_priority()
        
        while self.running:
            if self.capturing:
                # Режим захвата - ищем любую нажатую клавишу
                for vk_code in range(0x01, 0xFF):
                    if ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000:
                        key_name = VK_TO_NAME.get(vk_code)
                        if key_name:
                            self.current_hotkey = key_name
                            self.current_vk = vk_code
                            self.capturing = False
                            self.signal_bridge.hotkey_captured.emit(key_name)
                            # Ждём отпускания клавиши
                            while ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000:
                                time.sleep(0.01)
                            break
            else:
                # Обычный режим - проверяем горячую клавишу
                key_pressed = bool(ctypes.windll.user32.GetAsyncKeyState(self.current_vk) & 0x8000)
                
                # Реагируем только на НАЖАТИЕ (переход из не-нажато в нажато)
                if key_pressed and not self.key_was_pressed:
                    self.callback()
                
                self.key_was_pressed = key_pressed
            
            time.sleep(0.01)  # 10ms polling - быстро и не грузит CPU
    
    def start_capture(self):
        """Начать захват новой горячей клавиши"""
        self.capturing = True
    
    def set_hotkey(self, key_name: str):
        """Установить горячую клавишу"""
        key_name = key_name.lower()
        self.current_hotkey = key_name
        self.current_vk = KEY_MAP.get(key_name, VK_F6)
    
    def stop(self):
        """Остановить менеджер"""
        self.running = False


class OverlayWindow(QWidget):
    """Оверлей для отображения статуса поверх всех окон - АГРЕССИВНАЯ ВЕРСИЯ для игр"""
    def __init__(self):
        super().__init__()
        # Настройка окна: поверх всех, без рамки, не в панели задач (Tool)
        # Добавляем X11BypassWindowManagerHint для обхода некоторых WM на Windows
        self.setWindowFlags(
            Qt.ToolTip | 
            Qt.FramelessWindowHint | 
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)  # Не забирать фокус
        self.setGeometry(50, 50, 160, 40)
        
        # Основной контейнер
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.frame = QFrame()
        self.frame.setStyleSheet("""
            QFrame {
                background-color: rgba(30, 30, 30, 180);
                border: 1px solid #555;
                border-radius: 5px;
            }
        """)
        frame_layout = QHBoxLayout(self.frame)
        frame_layout.setContentsMargins(8, 2, 8, 2)
        
        self.status_icon = QLabel()
        self.status_icon.setFixedSize(10, 10)
        self.status_icon.setStyleSheet("background-color: #ff5555; border-radius: 5px;")
        
        self.label = QLabel("OFF")
        self.label.setFont(QFont('Segoe UI', 10, QFont.Bold))
        self.label.setStyleSheet("color: white;")
        
        frame_layout.addWidget(self.status_icon)
        frame_layout.addWidget(self.label)
        frame_layout.addStretch()
        
        layout.addWidget(self.frame)
        
        # Перетаскивание окна
        self.old_pos = None

        # Force Topmost once
        self.ensure_topmost()

    def set_no_activate(self):
        """Устанавливает флаг WS_EX_NOACTIVATE чтобы окно не забирало фокус при клике"""
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOPMOST = 0x00000008
            
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # Combine NOACTIVATE and TOPMOST
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        except Exception:
            pass

    def ensure_topmost(self):
        """Принудительно ставит окно поверх всех (HWND_TOPMOST) - вызывается олин раз"""
        try:
            hwnd = int(self.winId())
            # HWND_TOPMOST = -1, SWP flags
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
        except Exception:
            pass

    def showEvent(self, event):
        self.ensure_topmost()
        self.set_no_activate()
        super().showEvent(event)

    def update_status(self, enabled: bool):
        if enabled:
            self.label.setText("ON")
            self.status_icon.setStyleSheet("background-color: #50fa7b; border-radius: 5px;")
            self.frame.setStyleSheet("""
                QFrame {
                    background-color: rgba(30, 30, 30, 200);
                    border: 1px solid #50fa7b;
                    border-radius: 5px;
                }
            """)
        else:
            self.label.setText("OFF")
            self.status_icon.setStyleSheet("background-color: #ff5555; border-radius: 5px;")
            self.frame.setStyleSheet("""
                QFrame {
                    background-color: rgba(30, 30, 30, 180);
                    border: 1px solid #555;
                    border-radius: 5px;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPos() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.old_pos = None


class MainWindow(QMainWindow):
    """Главное окно приложения"""
    
    def __init__(self):
        super().__init__()
        
        self.signal_bridge = SignalBridge()
        self.clicker_enabled = False
        
        # Инициализация компонентов
        # Инициализация компонентов
        self.clicker_engine = ClickerEngine(
            left_cps=12, right_cps=12,
            left_randomize=0, right_randomize=0,
            randomization_enabled=False
        )
        self.hotkey_manager = HotkeyManager(
            callback=self.toggle_clicker,
            signal_bridge=self.signal_bridge
        )
        
        # Подключаем сигналы
        self.signal_bridge.status_changed.connect(self._update_status_ui)
        self.signal_bridge.hotkey_captured.connect(self._on_hotkey_captured)
        
        self.signal_bridge.hotkey_captured.connect(self._on_hotkey_captured)
        
        # Загружаем настройки
        # Настройки храним РЯДОМ с экзешником (или в %APPDATA%), чтобы они сохранялись
        if getattr(sys, 'frozen', False):
            self.base_dir = Path(sys.executable).parent
        else:
            self.base_dir = Path(__file__).parent
            
        self.settings_path = self.base_dir / 'autoclicker_settings.json'
        
        # Значения по умолчанию для настроек
        self.sound_enabled = True
        self.overlay_enabled = True
        
        self.load_settings()
        
        # Создаем UI
        self.init_ui()
        
        # Системный трей
        self.init_tray()

        # Оверлей
        self.overlay = OverlayWindow()
        if self.overlay_enabled:
            self.overlay.show()
    
    def init_ui(self):
        """Инициализация пользовательского интерфейса"""
        # === Настройки окна ===
        self.setWindowTitle('AutoClicker')
        self.setFixedSize(500, 600) # Fixed size
        
        # Icon
        icon_path = resource_path('icon.png')
        self.setWindowIcon(QIcon(icon_path))
        
        # Центральный виджет
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(10) # Tight spacing
        layout.setContentsMargins(15, 15, 15, 15)
        
        # === Статус ===
        status_frame = QFrame()
        status_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 15px;
            }
        """)
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 20, 10, 20) # Explicit layout margins instead of CSS padding
        
        status_title = QLabel('Статус')
        status_title.setFont(QFont('Segoe UI', 12)) # Reduced font
        status_title.setStyleSheet('color: #aaa;')
        status_title.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(status_title)
        
        self.status_label = QLabel('ВЫКЛЮЧЕН')
        self.status_label.setFont(QFont('Segoe UI', 20, QFont.Bold)) # Reduced font slightly
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet('color: #ff5555;')
        status_layout.addWidget(self.status_label)
        
        layout.addWidget(status_frame)
        
        # === Горячая клавиша ===
        hotkey_frame = QFrame()
        hotkey_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 15px;
                padding: 15px;
            }
        """)
        hotkey_layout = QHBoxLayout(hotkey_frame)
        
        hotkey_label = QLabel('Горячая клавиша:')
        hotkey_label.setFont(QFont('Segoe UI', 14))
        hotkey_label.setStyleSheet('color: #ddd;')
        hotkey_layout.addWidget(hotkey_label)
        
        self.hotkey_btn = QPushButton(self.hotkey_manager.current_hotkey.upper())
        self.hotkey_btn.setFont(QFont('Segoe UI', 14, QFont.Bold))
        self.hotkey_btn.setMinimumHeight(55) # Increased
        self.hotkey_btn.setMinimumWidth(140) # Increased
        self.hotkey_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a4a4a;
                color: #fff;
                border: none;
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #5a5a5a;
            }
            QPushButton:pressed {
                background-color: #3a3a3a;
            }
        """)
        self.hotkey_btn.clicked.connect(self.start_hotkey_capture)
        hotkey_layout.addWidget(self.hotkey_btn)
        
        layout.addWidget(hotkey_frame)
        
        # === Вкладки для настроек ===
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #444;
                border-radius: 5px;
                background: #2d2d2d;
            }
            QTabBar::tab {
                background: #3a3a3a;
                color: #aaa;
                padding: 12px 25px; # Increased padding
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #2d2d2d;
                color: #fff;
                font-weight: bold;
            }
        """)
        
        # Вкладка ЛКМ
        self.left_tab = QWidget()
        self.init_click_tab(self.left_tab, "Left")
        self.tabs.addTab(self.left_tab, "Левая кнопка")
        
        # Вкладка ПКМ
        self.right_tab = QWidget()
        self.init_click_tab(self.right_tab, "Right")
        self.tabs.addTab(self.right_tab, "Правая кнопка")
        
        # Вкладка Настройки
        self.settings_tab = QWidget()
        self.init_settings_tab(self.settings_tab)
        self.tabs.addTab(self.settings_tab, "Настройки")
        
        layout.addWidget(self.tabs)

        # === Общие настройки (Рандомизация и Трей) ===
        common_frame = QFrame()
        common_frame.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-radius: 10px;
                padding: 10px;
                margin-top: 10px;
            }
        """)
        common_layout = QVBoxLayout(common_frame)
        
        # Чекбокс рандомизации
        self.rand_check = QCheckBox("Включить случайный разброс (Legit Mode)")
        self.rand_check.setFont(QFont('Segoe UI', 11))
        self.rand_check.setStyleSheet("QCheckBox { color: #ddd; } QCheckBox::indicator { width: 18px; height: 18px; }")
        self.rand_check.setChecked(self.clicker_engine.randomization_enabled)
        self.rand_check.toggled.connect(self.on_randomization_toggled)
        common_layout.addWidget(self.rand_check)

        layout.addWidget(common_frame)
        
        # === Кнопка свернуть ===
        minimize_btn = QPushButton('Свернуть в трей')
        minimize_btn.setFont(QFont('Segoe UI', 12, QFont.Bold))
        minimize_btn.setMinimumHeight(45)
        minimize_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d5afe;
                color: #fff;
                border: none;
                border-radius: 10px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #536dfe;
            }
            QPushButton:pressed {
                background-color: #304ffe;
            }
        """)
        minimize_btn.clicked.connect(self.hide)
        minimize_btn.clicked.connect(self.hide)
        layout.addWidget(minimize_btn)
        
        # Styles
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QSpinBox {
                background-color: #4a4a4a;
                color: #fff;
                border: 1px solid #555;
                border-radius: 5px;
                padding: 5px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #5a5a5a;
                width: 30px;
                border-radius: 2px;
                margin: 1px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #6a6a6a;
            }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
                background-color: #3a3a3a;
            }
            QSpinBox::up-arrow {
                image: url(%ARROW_UP%);
                width: 12px;
                height: 12px;
            }
            QSpinBox::down-arrow {
                image: url(%ARROW_DOWN%);
                width: 12px;
                height: 12px;
            }
        """.replace('%ARROW_UP%', resource_path('arrow_up.png').replace('\\', '/'))
           .replace('%ARROW_DOWN%', resource_path('arrow_down.png').replace('\\', '/'))
        )

    def init_click_tab(self, tab, mode):
        """Инициализация вкладки настроек клика"""
        layout = QVBoxLayout(tab)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # CPS
        cps_layout = QHBoxLayout()
        cps_label = QLabel('Скорость (CPS):')
        cps_label.setFont(QFont('Segoe UI', 12))
        cps_label.setStyleSheet('color: #ddd;')
        cps_layout.addWidget(cps_label)
        
        cps_spin = QSpinBox()
        cps_spin.setRange(1, 100)
        cps_spin.setFont(QFont('Segoe UI', 12))
        cps_spin.setMinimumHeight(45) # Increased
        cps_spin.setMinimumWidth(120) # Increased
        cps_spin = QSpinBox()
        cps_spin.setRange(1, 100)
        cps_spin.setFont(QFont('Segoe UI', 12))
        cps_spin.setMinimumHeight(45) 
        cps_spin.setMinimumWidth(120) 
        # Stylesheet moved to main window for global application
       
        
        if mode == "Left":
            cps_spin.setValue(self.clicker_engine.left_cps)
            cps_spin.valueChanged.connect(self.on_left_cps_changed)
            self.left_cps_spin = cps_spin
        else:
            cps_spin.setValue(self.clicker_engine.right_cps)
            cps_spin.valueChanged.connect(self.on_right_cps_changed)
            self.right_cps_spin = cps_spin
            
        cps_layout.addWidget(cps_spin)
        layout.addLayout(cps_layout)
        
        # Randomize Slider
        rand_group = QGroupBox("Сила разброса")
        rand_group.setStyleSheet("QGroupBox { color: #aaa; border: 1px solid #555; border-radius: 5px; margin-top: 10px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }")
        rand_layout = QVBoxLayout(rand_group)
        
        slider_layout = QHBoxLayout()
        rand_slider = QSlider(Qt.Horizontal)
        rand_slider.setRange(0, 100)
        rand_slider.setStyleSheet("""
            QSlider::groove:horizontal { border: 1px solid #3d3d3d; height: 8px; background: #4a4a4a; margin: 2px 0; border-radius: 4px; }
            QSlider::handle:horizontal { background: #3d5afe; border: 1px solid #3d5afe; width: 18px; height: 18px; margin: -5px 0; border-radius: 9px; }
        """)
        
        rand_val_label = QLabel('0%')
        rand_val_label.setFont(QFont('Segoe UI', 11))
        rand_val_label.setStyleSheet('color: #ddd;')
        rand_val_label.setFixedWidth(40)
        rand_val_label.setAlignment(Qt.AlignCenter)
        
        if mode == "Left":
            rand_slider.setValue(self.clicker_engine.left_randomize)
            rand_slider.valueChanged.connect(lambda v: self.on_left_rand_changed(v, rand_val_label))
            rand_val_label.setText(f"{self.clicker_engine.left_randomize}%")
            self.left_rand_slider = rand_slider
        else:
            rand_slider.setValue(self.clicker_engine.right_randomize)
            rand_slider.valueChanged.connect(lambda v: self.on_right_rand_changed(v, rand_val_label))
            rand_val_label.setText(f"{self.clicker_engine.right_randomize}%")
            self.right_rand_slider = rand_slider
            
        slider_layout.addWidget(rand_slider)
        slider_layout.addWidget(rand_val_label)
        rand_layout.addLayout(slider_layout)
        
        layout.addWidget(rand_group)
        layout.addStretch()
    
    def init_settings_tab(self, tab):
        """Инициализация вкладки общих настроек"""
        layout = QVBoxLayout(tab)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # Sound Toggle
        self.sound_check = QCheckBox("Звуковое уведомление (Beep)")
        self.sound_check.setFont(QFont('Segoe UI', 12))
        self.sound_check.setStyleSheet("QCheckBox { color: #ddd; } QCheckBox::indicator { width: 22px; height: 22px; }")
        self.sound_check.setChecked(self.sound_enabled)
        self.sound_check.toggled.connect(self.on_sound_toggled)
        layout.addWidget(self.sound_check)
        
        # Overlay Toggle
        self.overlay_check = QCheckBox("Показывать оверлей (Status)")
        self.overlay_check.setFont(QFont('Segoe UI', 12))
        self.overlay_check.setStyleSheet("QCheckBox { color: #ddd; } QCheckBox::indicator { width: 22px; height: 22px; }")
        self.overlay_check.setChecked(self.overlay_enabled)
        self.overlay_check.toggled.connect(self.on_overlay_toggled)
        layout.addWidget(self.overlay_check)
        
        layout.addStretch()

    def init_tray(self):
        """Инициализация системного трея"""
        # Load icon
        icon_path = resource_path('icon.png')
        icon = QIcon(icon_path)
            
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip('AutoClicker')
        
        # Меню трея
        tray_menu = QMenu()
        
        show_action = QAction('Показать', self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction('Выход', self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
    
    def toggle_clicker(self):
        """Переключить состояние автокликера"""
        self.clicker_enabled = not self.clicker_enabled
        self.clicker_engine.set_enabled(self.clicker_enabled)
        self.signal_bridge.status_changed.emit(self.clicker_enabled)
        
        # ЗВУКОВОЙ СИГНАЛ
        if self.sound_enabled:
            try:
                if self.clicker_enabled:
                    # Высокий тон - ВКЛЮЧЕНО (880Hz, 150ms)
                    threading.Thread(target=lambda: winsound.Beep(880, 150), daemon=True).start()
                else:
                    # Низкий тон - ВЫКЛЮЧЕНО (440Hz, 150ms)
                    threading.Thread(target=lambda: winsound.Beep(440, 150), daemon=True).start()
            except Exception:
                pass
    
    def _update_status_ui(self, enabled: bool):
        """Обновить UI статуса"""
        self.overlay.update_status(enabled)  # Обновляем оверлей
        if enabled:
            self.status_label.setText('ВКЛЮЧЕН')
            self.status_label.setStyleSheet('color: #50fa7b;')
        else:
            self.status_label.setText('ВЫКЛЮЧЕН')
            self.status_label.setStyleSheet('color: #ff5555;')
    
    def start_hotkey_capture(self):
        """Начать захват новой горячей клавиши"""
        self.hotkey_btn.setText('...')
        self.hotkey_manager.start_capture()
    
    def _on_hotkey_captured(self, key_name: str):
        """Обработчик захвата новой горячей клавиши"""
        self.hotkey_btn.setText(key_name.upper())
        self.save_settings()
    
    def on_left_cps_changed(self, value: int):
        self.clicker_engine.set_left_cps(value)
        self.save_settings()

    def on_right_cps_changed(self, value: int):
        self.clicker_engine.set_right_cps(value)
        self.save_settings()
    
    def on_left_rand_changed(self, value: int, label: QLabel):
        self.clicker_engine.set_left_randomize(value)
        label.setText(f"{value}%")
        self.save_settings()

    def on_right_rand_changed(self, value: int, label: QLabel):
        self.clicker_engine.set_right_randomize(value)
        label.setText(f"{value}%")
        self.save_settings()

    def on_sound_toggled(self, checked: bool):
        self.sound_enabled = checked
        self.save_settings()

    def on_overlay_toggled(self, checked: bool):
        self.overlay_enabled = checked
        if checked:
            self.overlay.show()
        else:
            self.overlay.hide()
        self.save_settings()

    def on_randomization_toggled(self, checked: bool):
        self.clicker_engine.set_randomization_enabled(checked)
        self.save_settings()
    
    def on_tray_activated(self, reason):
        """Обработчик клика по иконке в трее"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.show()
            self.activateWindow()
    
    def load_settings(self):
        """Загрузить настройки"""
        try:
            if self.settings_path.exists():
                with open(self.settings_path, 'r') as f:
                    settings = json.load(f)
                    self.hotkey_manager.set_hotkey(settings.get('hotkey', 'f6'))
                    
                    self.clicker_engine.set_left_cps(settings.get('left_cps', 12))
                    self.clicker_engine.set_right_cps(settings.get('right_cps', 12))
                    
                    self.clicker_engine.set_left_randomize(settings.get('left_randomize', 0))
                    self.clicker_engine.set_right_randomize(settings.get('right_randomize', 0))
                    
                    self.clicker_engine.set_randomization_enabled(settings.get('randomization_enabled', False))
                    
                    self.sound_enabled = settings.get('sound_enabled', True)
                    self.overlay_enabled = settings.get('overlay_enabled', True)
                    
                    if hasattr(self, 'sound_check'): self.sound_check.setChecked(self.sound_enabled)
                    if hasattr(self, 'overlay_check'): self.overlay_check.setChecked(self.overlay_enabled)
        except Exception:
            pass
    
    def save_settings(self):
        """Сохранить настройки"""
        try:
            settings = {
                'hotkey': self.hotkey_manager.current_hotkey,
                'left_cps': self.clicker_engine.left_cps,
                'right_cps': self.clicker_engine.right_cps,
                'left_randomize': self.clicker_engine.left_randomize,
                'right_randomize': self.clicker_engine.right_randomize,
                'randomization_enabled': self.clicker_engine.randomization_enabled,
                'sound_enabled': self.sound_enabled,
                'overlay_enabled': self.overlay_enabled
            }
            with open(self.settings_path, 'w') as f:
                json.dump(settings, f)
        except Exception:
            pass
    
    def closeEvent(self, event):
        """Закрытие окна - полностью закрываем приложение"""
        event.accept()
        self.quit_app()
    
    def quit_app(self):
        """Полное закрытие приложения"""
        self.save_settings()
        self.clicker_engine.stop()
        self.hotkey_manager.stop()
        self.overlay.close()
        self.tray_icon.hide()
        QApplication.quit()


def main():
    # ============ ПРОВЕРКА ПРАВ АДМИНИСТРАТОРА ============
    if not is_admin():
        print("[!] Запуск без прав администратора. Запрашиваем повышение...")
        run_as_admin()
        # Если мы здесь, значит пользователь отказался или произошла ошибка
        # Продолжаем работу без админ прав
        print("[!] Продолжаем без прав администратора (возможны ограничения в играх)")
    else:
        print("[OK] Запущено с правами администратора")
    
    # ============ УСТАНОВКА ВЫСОКОГО ПРИОРИТЕТА ============
    set_high_priority()
    
    # ============ HIGH DPI SCALING ============
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    
    # Скорректируем DPI политику для Windows (если нужно)
    if hasattr(Qt, 'AA_Use96Dpi'):
        QApplication.setAttribute(Qt.AA_Use96Dpi, True) # Иногда это помогает от размытости, но AA_EnableHighDpiScaling лучше для масштаба

    app = QApplication(sys.argv)
    
    # Установка шрифта по умолчанию для всего приложения
    font = QFont('Segoe UI', 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)
    
    app.setQuitOnLastWindowClosed(False)  # Не закрывать приложение при закрытии окна
    app.setStyle('Fusion')
    
    # Темная палитра
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    palette.setColor(QPalette.Base, QColor(45, 45, 45))
    palette.setColor(QPalette.AlternateBase, QColor(30, 30, 30))
    palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
    palette.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
    palette.setColor(QPalette.Text, QColor(255, 255, 255))
    palette.setColor(QPalette.Button, QColor(45, 45, 45))
    palette.setColor(QPalette.ButtonText, QColor(255, 255, 255))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Highlight, QColor(61, 90, 254))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
