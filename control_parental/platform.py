"""Acceso al sistema operativo. WindowsPlatform es la implementación real;
FakePlatform permite probar el agente en cualquier sistema."""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass

from .rules import public_ipv4_ranges

log = logging.getLogger("control_parental")

FW_RULE_V4 = "ControlParental - Bloqueo internet IPv4"
FW_RULE_V6 = "ControlParental - Bloqueo internet IPv6"
CREATE_NO_WINDOW = 0x08000000
HOSTS = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_BEGIN = "# >>> ControlParental (no editar a mano)"
HOSTS_END = "# <<< ControlParental"
HELPER = "ayudante.py"


def hosts_with_block(text: str, domains: list[str]) -> str:
    """Devuelve el contenido de hosts con el bloque de ControlParental sustituido."""
    lines, skip = [], False
    for ln in text.splitlines():
        if ln.strip() == HOSTS_BEGIN:
            skip = True
        elif ln.strip() == HOSTS_END:
            skip = False
        elif not skip:
            lines.append(ln)
    while lines and not lines[-1].strip():
        lines.pop()
    if domains:
        lines += ["", HOSTS_BEGIN] + [f"0.0.0.0 {d}\n:: {d}".strip() for d in domains] + [HOSTS_END]
    return "\r\n".join("\r\n".join(lines).splitlines()) + "\r\n"


@dataclass
class Proc:
    pid: int
    name: str
    path: str | None
    session: int


@dataclass
class Session:
    id: int | None   # sesión de consola activa (None = nadie conectado)
    locked: bool
    user: str | None


class WindowsPlatform:
    def __init__(self):
        import ctypes
        import psutil  # noqa: F401  (se importa aquí para que falle pronto si falta)
        self.ctypes = ctypes
        self.kernel32 = ctypes.windll.kernel32
        self._fw_state: bool | None = None
        self._hosts_state: list[str] | None = None
        self._helper_try: dict[int, float] = {}
        self._helpers_checked = False

    def _session_of(self, pid: int) -> int:
        sid = self.ctypes.c_ulong(0)
        ok = self.kernel32.ProcessIdToSessionId(self.ctypes.c_ulong(pid), self.ctypes.byref(sid))
        return sid.value if ok else -1

    def _wts_processes(self) -> dict[int, tuple[int, str]]:
        """pid -> (sesión, nombre) de todos los procesos, sin abrir ninguno. Hace falta para los
        juegos con antitrampas (Valorant, Fortnite...), que no dejan abrir su proceso."""
        ctypes = self.ctypes
        from ctypes import wintypes as w

        class WTS_PROCESS_INFOW(ctypes.Structure):
            _fields_ = [("SessionId", w.DWORD), ("ProcessId", w.DWORD),
                        ("pProcessName", w.LPWSTR), ("pUserSid", ctypes.c_void_p)]

        wts = ctypes.WinDLL("wtsapi32")
        wts.WTSEnumerateProcessesW.argtypes = [w.HANDLE, w.DWORD, w.DWORD,
                                               ctypes.POINTER(ctypes.POINTER(WTS_PROCESS_INFOW)),
                                               ctypes.POINTER(w.DWORD)]
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        ptr, count = ctypes.POINTER(WTS_PROCESS_INFOW)(), w.DWORD()
        if not wts.WTSEnumerateProcessesW(None, 0, 1, ctypes.byref(ptr), ctypes.byref(count)):
            return {}
        try:
            return {ptr[i].ProcessId: (ptr[i].SessionId, ptr[i].pProcessName or "")
                    for i in range(count.value)}
        finally:
            wts.WTSFreeMemory(ptr)

    def processes(self) -> list[Proc]:
        import psutil
        try:
            wts = self._wts_processes()
        except Exception as e:
            log.warning("WTSEnumerateProcesses falló: %s", e)
            wts = {}
        out = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = p.info
                pid = info["pid"]
                sess, wname = wts.get(pid, (None, ""))
                if sess is None:
                    sess = self._session_of(pid)
                out.append(Proc(pid, info["name"] or wname, info.get("exe"), sess))
            except Exception:
                continue
        return out

    def session(self, procs: list[Proc]) -> Session:
        sid = self.kernel32.WTSGetActiveConsoleSessionId()
        if sid == 0xFFFFFFFF:
            return Session(None, False, None)
        in_sess = {p.name.lower() for p in procs if p.session == sid}
        if "explorer.exe" not in in_sess:
            return Session(None, False, None)  # pantalla de inicio de sesión
        user = None
        try:
            import psutil
            for u in psutil.users():
                user = u.name
                break
        except Exception:
            pass
        # Con la sesión bloqueada (Win+L) Windows ejecuta LogonUI.exe en ella.
        return Session(sid, "logonui.exe" in in_sess, user)

    def kill(self, proc: Proc) -> None:
        import psutil
        try:
            psutil.Process(proc.pid).kill()
            log.info("Cerrado %s (pid %s)", proc.name, proc.pid)
            return True
        except psutil.NoSuchProcess:
            return True
        except Exception as e:
            log.warning("No se pudo cerrar %s: %s", proc.name, e)
        # Segundo intento con taskkill (a veces lo permite cuando lo anterior no).
        r = subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0

    def _netsh(self, *args: str) -> bool:
        r = subprocess.run(["netsh", "advfirewall", "firewall", *args], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0

    def set_internet_block(self, blocked: bool, allowed_ips: list[str]) -> None:
        if blocked == self._fw_state:
            return
        self._netsh("delete", "rule", f"name={FW_RULE_V4}")
        self._netsh("delete", "rule", f"name={FW_RULE_V6}")
        if blocked:
            ranges = ",".join(public_ipv4_ranges(allowed_ips))
            ok = self._netsh("add", "rule", f"name={FW_RULE_V4}", "dir=out", "action=block",
                             "profile=any", "enable=yes", f"remoteip={ranges}")
            # IPv6 global (2000::/3); se mantienen link-local y ULA de la red de casa.
            ok &= self._netsh("add", "rule", f"name={FW_RULE_V6}", "dir=out", "action=block",
                              "profile=any", "enable=yes", "remoteip=2000::/3")
            if not ok:
                log.error("No se pudo crear la regla de firewall (¿se ejecuta como SYSTEM/admin?)")
                return
        log.info("Internet %s", "BLOQUEADO" if blocked else "desbloqueado")
        self._fw_state = blocked

    def set_sites_block(self, domains: list[str]) -> None:
        """Bloquea webs con el archivo hosts (solo se reescribe si cambia)."""
        if domains == self._hosts_state:
            return
        try:
            with open(HOSTS, encoding="utf-8-sig", errors="replace") as f:
                old = f.read()
            new = hosts_with_block(old, domains)
            if new != old:
                with open(HOSTS, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
                subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=CREATE_NO_WINDOW)
            log.info("Webs bloqueadas: %s", ", ".join(domains) or "ninguna")
            self._hosts_state = list(domains)
        except Exception as e:
            log.error("No se pudo actualizar hosts: %s", e)

    def restart_agent(self) -> None:
        """Sale y vuelve a arrancar la tarea programada unos segundos después."""
        import os
        subprocess.Popen('cmd /c ping -n 5 127.0.0.1 >nul & schtasks /run /tn "ControlParental-Agente"',
                         creationflags=CREATE_NO_WINDOW | 0x00000008)  # DETACHED_PROCESS
        os._exit(0)

    def ensure_helper(self, session: Session, procs: list[Proc], port: int) -> None:
        """Mantiene el ayudante (ayudante.py) abierto en la sesión del hijo: es lo único que puede
        ver qué ventana está usando. Si lo cierra, se vuelve a abrir en unos segundos."""
        import os
        import time
        import psutil
        if session.id is None:
            return
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        for p in procs:
            if p.session == session.id and p.name.lower() == "pythonw.exe":
                try:
                    if any(a.endswith(HELPER) for a in psutil.Process(p.pid).cmdline()):
                        if self._helpers_checked:
                            return
                        psutil.Process(p.pid).kill()  # de antes de arrancar (o actualizar) el agente
                except Exception:
                    continue
        self._helpers_checked = True
        if time.monotonic() - self._helper_try.get(session.id, -1e9) < 20:
            return
        self._helper_try[session.id] = time.monotonic()
        helper = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), HELPER)
        try:
            self._launch_in_session(session.id, f'"{pyw}" "{helper}" {port}', os.path.dirname(helper))
            log.info("Ayudante iniciado en la sesión %s", session.id)
        except Exception as e:
            log.warning("No se pudo iniciar el ayudante: %s", e)

    def _launch_in_session(self, session_id: int, cmd: str, cwd: str) -> None:
        import ctypes
        from ctypes import wintypes as w

        class STARTUPINFO(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                        ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                        ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                        ("lpReserved2", ctypes.c_void_p), ("hStdInput", w.HANDLE),
                        ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        wts, adv, uenv, k32 = (ctypes.WinDLL(n, use_last_error=True)
                               for n in ("wtsapi32", "advapi32", "userenv", "kernel32"))
        wts.WTSQueryUserToken.argtypes = [w.ULONG, ctypes.POINTER(w.HANDLE)]
        uenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.BOOL]
        uenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
        adv.CreateProcessAsUserW.argtypes = [w.HANDLE, w.LPCWSTR, w.LPWSTR, ctypes.c_void_p,
                                             ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p,
                                             w.LPCWSTR, ctypes.POINTER(STARTUPINFO),
                                             ctypes.POINTER(PROCESS_INFORMATION)]
        k32.CloseHandle.argtypes = [w.HANDLE]
        tok = w.HANDLE()
        if not wts.WTSQueryUserToken(session_id, ctypes.byref(tok)):
            raise OSError(ctypes.get_last_error(), "WTSQueryUserToken")
        env = ctypes.c_void_p()
        try:
            uenv.CreateEnvironmentBlock(ctypes.byref(env), tok, False)
            si = STARTUPINFO(cb=ctypes.sizeof(STARTUPINFO), lpDesktop="winsta0\\default")
            pi = PROCESS_INFORMATION()
            flags = 0x00000400 | CREATE_NO_WINDOW  # CREATE_UNICODE_ENVIRONMENT
            if not adv.CreateProcessAsUserW(tok, None, ctypes.create_unicode_buffer(cmd), None, None,
                                            False, flags, env, cwd, ctypes.byref(si), ctypes.byref(pi)):
                raise OSError(ctypes.get_last_error(), "CreateProcessAsUser")
            k32.CloseHandle(pi.hProcess)
            k32.CloseHandle(pi.hThread)
        finally:
            if env:
                uenv.DestroyEnvironmentBlock(env)
            k32.CloseHandle(tok)

    def notify(self, session: Session, text: str) -> None:
        if session.id is None:
            return
        try:
            subprocess.run(["msg", str(session.id), "/TIME:60", text],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception as e:  # msg.exe no existe en algunas ediciones Home
            log.warning("No se pudo mostrar aviso: %s", e)


class FakePlatform:
    """Plataforma simulada para pruebas y desarrollo fuera de Windows."""

    def __init__(self):
        self.procs: list[Proc] = []
        self.sess = Session(1, False, "hijo")
        self.killed: list[str] = []
        self.internet_blocked = False
        self.blocked_domains: list[str] = []
        self.unkillable: set[str] = set()   # simula juegos con antitrampas que no se dejan cerrar
        self.messages: list[str] = []

    def processes(self):
        return list(self.procs)

    def session(self, procs):
        return self.sess

    def kill(self, proc):
        if proc.name in self.unkillable:
            return False
        self.killed.append(proc.name)
        self.procs = [p for p in self.procs if p.pid != proc.pid]
        return True

    def set_internet_block(self, blocked, allowed_ips):
        self.internet_blocked = blocked

    def set_sites_block(self, domains):
        self.blocked_domains = list(domains)

    def ensure_helper(self, session, procs, port):
        pass

    def restart_agent(self):
        self.restarted = True

    def notify(self, session, text):
        self.messages.append(text)


def default_platform():
    if sys.platform == "win32":
        return WindowsPlatform()
    log.warning("No es Windows: se usa una plataforma simulada (sin efectos reales)")
    return FakePlatform()
