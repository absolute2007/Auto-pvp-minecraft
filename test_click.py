"""Тест автоклика - проверяем работает ли SendInput"""
import ctypes
import ctypes.wintypes
import time

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
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
    _fields_ = [("mi", MOUSEINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]

def send_click():
    extra = ctypes.pointer(ctypes.wintypes.ULONG(0))
    
    # DOWN
    ii = INPUT_UNION()
    ii.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, extra)
    inp = INPUT(INPUT_MOUSE, ii)
    result1 = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    
    time.sleep(0.01)
    
    # UP
    ii2 = INPUT_UNION()
    ii2.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, extra)
    inp2 = INPUT(INPUT_MOUSE, ii2)
    result2 = ctypes.windll.user32.SendInput(1, ctypes.byref(inp2), ctypes.sizeof(inp2))
    
    return result1, result2

print("Тест SendInput клика...")
print("Наведи курсор на какое-нибудь поле ввода и жди 3 секунды...")
time.sleep(3)

for i in range(5):
    r1, r2 = send_click()
    print(f"Клик {i+1}: DOWN={r1}, UP={r2}")
    time.sleep(0.2)

print("Тест завершен!")
