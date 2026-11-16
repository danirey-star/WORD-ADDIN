"""Ayudante del agente. El agente (SYSTEM) lo abre en la sesión del hijo, porque solo desde
ahí se ve qué ventana se está usando. Cada 2 s envía al agente el programa y el título de la
ventana de primer plano y los segundos sin tocar teclado ni ratón. Si el agente responde
"close" (web bloqueada), cierra esa pestaña con Ctrl+W; si manda "alerts" (avisos y alarmas),
los muestra con sonido."""
import ctypes
import json
import sys
import threading
import time
import urllib.request
from ctypes import wintypes as w

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
URL = f"http://127.0.0.1:{PORT}/api/foreground"
u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
u32.GetForegroundWindow.restype = w.HWND


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]


def idle_seconds() -> float:
    li = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not u32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((k32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def foreground():
    hwnd = u32.GetForegroundWindow()
    if not hwnd:
        return None, "", ""
    buf = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, buf, 512)
    pid = w.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        path = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
            exe = path.value.rsplit("\\", 1)[-1]
        k32.CloseHandle(h)
    return hwnd, exe, buf.value


def close_tab():
    VK_CONTROL, VK_W, UP = 0x11, 0x57, 0x0002
    u32.keybd_event(VK_CONTROL, 0, 0, 0)
    u32.keybd_event(VK_W, 0, 0, 0)
    u32.keybd_event(VK_W, 0, UP, 0)
    u32.keybd_event(VK_CONTROL, 0, UP, 0)


def show_alert(text: str):
    """Aviso con sonido y ventana por encima de todo (sin bloquear el bucle)."""
    def run():
        try:
            import winsound
            for _ in range(3):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                time.sleep(0.6)
        except Exception:
            pass
        # MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
        u32.MessageBoxW(None, text, "Tiempo de pantalla", 0x0 | 0x40 | 0x1000 | 0x10000 | 0x40000)
    threading.Thread(target=run, daemon=True).start()


def main():
    last_close = 0.0
    while True:
        try:
            hwnd, exe, title = foreground()
            data = json.dumps({"exe": exe, "title": title, "idle": idle_seconds()}).encode()
            req = urllib.request.Request(URL, data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3) as r:
                resp = json.loads(r.read())
            for text in resp.get("alerts") or []:
                show_alert(str(text))
            if resp.get("close") and time.monotonic() - last_close > 3 and u32.GetForegroundWindow() == hwnd:
                last_close = time.monotonic()
                close_tab()
        except Exception:
            pass
        time.sleep(2)


if __name__ == "__main__":
    main()
