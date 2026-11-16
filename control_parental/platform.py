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

    def _session_of(self, pid: int) -> int:
        sid = self.ctypes.c_ulong(0)
        ok = self.kernel32.ProcessIdToSessionId(self.ctypes.c_ulong(pid), self.ctypes.byref(sid))
        return sid.value if ok else -1

    def processes(self) -> list[Proc]:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                info = p.info
                out.append(Proc(info["pid"], info["name"] or "", info.get("exe"),
                                self._session_of(info["pid"])))
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
        except Exception as e:
            log.warning("No se pudo cerrar %s: %s", proc.name, e)

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
        self.sess = Session(1, False, "nino")
        self.killed: list[str] = []
        self.internet_blocked = False
        self.messages: list[str] = []

    def processes(self):
        return list(self.procs)

    def session(self, procs):
        return self.sess

    def kill(self, proc):
        self.killed.append(proc.name)
        self.procs = [p for p in self.procs if p.pid != proc.pid]

    def set_internet_block(self, blocked, allowed_ips):
        self.internet_blocked = blocked

    def notify(self, session, text):
        self.messages.append(text)


def default_platform():
    if sys.platform == "win32":
        return WindowsPlatform()
    log.warning("No es Windows: se usa una plataforma simulada (sin efectos reales)")
    return FakePlatform()
