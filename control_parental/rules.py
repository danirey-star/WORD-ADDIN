"""Lógica pura (sin dependencias de Windows): configuración, clasificación de
procesos y evaluación de bloqueos. Todo lo de aquí se puede probar en cualquier SO."""
from __future__ import annotations

import copy
import ipaddress
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PC_NAME = "Ordenador (sesión activa)"

DEFAULT_CONFIG: dict = {
    # Nombre amigable -> ejecutables. Se comparan sin distinguir mayúsculas.
    "games": {
        "Fortnite": ["FortniteClient-Win64-Shipping.exe"],
        "Minecraft": ["Minecraft.Windows.exe", "javaw.exe"],
        "Roblox": ["RobloxPlayerBeta.exe"],
        "League of Legends": ["League of Legends.exe"],
        "Valorant": ["VALORANT-Win64-Shipping.exe"],
        "Counter-Strike 2": ["cs2.exe"],
        "Rocket League": ["RocketLeague.exe"],
        "GTA V": ["GTA5.exe", "GTA5_Enhanced.exe"],
        "EA Sports FC": ["FC25.exe", "FC26.exe"],
        "Brawlhalla": ["Brawlhalla.exe"],
    },
    # Otras apps que interesa medir (no cuentan para el límite de juegos).
    "apps": {
        "Discord": ["Discord.exe"],
        "Navegador": ["chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe"],
    },
    # Detecta como juego cualquier programa dentro de carpetas de Steam, Epic, etc.
    "auto_detect_games": True,
    # Ejecutables que nunca se cuentan (lanzadores, anti-trampas...).
    "ignore": [
        "steam.exe", "steamwebhelper.exe", "steamservice.exe",
        "EpicGamesLauncher.exe", "EpicWebHelper.exe", "UnrealCEFSubProcess.exe",
        "CrashReportClient.exe", "EasyAntiCheat.exe", "EasyAntiCheat_EOS.exe",
        "BEService.exe", "vgc.exe", "vgtray.exe",
        "RiotClientServices.exe", "RiotClientUx.exe", "RiotClientUxRender.exe",
        "RiotClientCrashHandler.exe", "FortniteLauncher.exe",
    ],
    # Minutos de juego por día de la semana (null = sin límite).
    "daily_game_limit_min": {d: None for d in DAYS},
    # Franjas horarias. type: "internet" | "games" | "apps".
    # Si end < start la franja cruza la medianoche (p.ej. 22:00-07:00).
    "schedules": [],
    # Bloqueos manuales: {"type": ..., "apps": [...], "until": "YYYY-MM-DDTHH:MM" | null}
    "manual_blocks": [],
    # Avisar al niño X minutos antes de que empiece un bloqueo / se acabe el tiempo.
    "warn_minutes": 5,
}


def merged_config(cfg: dict | None) -> dict:
    """Completa una configuración con los valores por defecto que falten."""
    out = copy.deepcopy(DEFAULT_CONFIG)
    if cfg:
        for k, v in cfg.items():
            out[k] = copy.deepcopy(v)
    for d in DAYS:
        out["daily_game_limit_min"].setdefault(d, None)
    return out


# ----------------------------------------------------------------------------
# Clasificación de procesos
# ----------------------------------------------------------------------------
_AUTO_PATTERNS = [
    re.compile(r"[\\/]steamapps[\\/]common[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]Epic Games[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]Riot Games[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]XboxGames[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]EA Games[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]Ubisoft Game Launcher[\\/]games[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]GOG Galaxy[\\/]Games[\\/]([^\\/]+)[\\/]", re.I),
    re.compile(r"[\\/]Roblox[\\/]Versions[\\/]", re.I),
]
_AUTO_EXCLUDED_FOLDERS = {"launcher", "riot client", "steamworks shared", "directxredist",
                          "epic online services"}


class Classifier:
    """Convierte (exe, ruta) en (nombre, categoría) o None si no interesa."""

    def __init__(self, cfg: dict):
        self.ignore = {e.lower() for e in cfg.get("ignore", [])}
        self.by_exe: dict[str, tuple[str, str]] = {}
        for name, exes in cfg.get("apps", {}).items():
            for e in exes:
                self.by_exe[e.lower()] = (name, "app")
        for name, exes in cfg.get("games", {}).items():
            for e in exes:
                self.by_exe[e.lower()] = (name, "game")
        self.auto = cfg.get("auto_detect_games", True)

    def classify(self, exe: str, path: str | None) -> tuple[str, str] | None:
        exe_l = (exe or "").lower()
        if not exe_l or exe_l in self.ignore:
            return None
        if exe_l in self.by_exe:
            return self.by_exe[exe_l]
        if self.auto and path:
            for pat in _AUTO_PATTERNS:
                m = pat.search(path)
                if m:
                    folder = m.group(1) if pat.groups else "Roblox"
                    if folder.lower() in _AUTO_EXCLUDED_FOLDERS:
                        return None
                    return (folder, "game")
        return None


# ----------------------------------------------------------------------------
# Evaluación de bloqueos
# ----------------------------------------------------------------------------
@dataclass
class BlockState:
    internet: bool = False
    games: bool = False
    apps: set[str] = field(default_factory=set)  # exes en minúscula
    reasons: list[str] = field(default_factory=list)
    game_minutes_left: float | None = None  # None = sin límite hoy

    def to_dict(self) -> dict:
        return {"internet": self.internet, "games": self.games, "apps": sorted(self.apps),
                "reasons": self.reasons, "game_minutes_left": self.game_minutes_left}


def _hm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def schedule_active(sched: dict, now: datetime) -> bool:
    days = sched.get("days") or DAYS
    start, end = _hm(sched["start"]), _hm(sched["end"])
    t = now.hour * 60 + now.minute
    today = DAYS[now.weekday()]
    yesterday = DAYS[(now.weekday() - 1) % 7]
    if start == end:
        return today in days  # todo el día
    if start < end:
        return today in days and start <= t < end
    # Cruza la medianoche: el día indicado es el día en que empieza.
    return (today in days and t >= start) or (yesterday in days and t < end)


def parse_until(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def manual_active(block: dict, now: datetime) -> bool:
    until = parse_until(block.get("until"))
    return until is None or now < until


def _apply(state: BlockState, rule: dict, reason: str) -> None:
    kind = rule.get("type")
    if kind == "internet":
        state.internet = True
    elif kind == "games":
        state.games = True
    elif kind == "apps":
        state.apps.update(a.lower() for a in rule.get("apps", []))
    else:
        return
    state.reasons.append(reason)


def _describe(rule: dict) -> str:
    kind = rule.get("type")
    if kind == "apps":
        return "apps (" + ", ".join(rule.get("apps", [])) + ")"
    return {"internet": "internet", "games": "juegos"}.get(kind, str(kind))


def evaluate(cfg: dict, now: datetime, game_seconds_today: float) -> BlockState:
    st = BlockState()
    for s in cfg.get("schedules", []):
        if s.get("enabled", True) and schedule_active(s, now):
            _apply(st, s, f"Horario {s['start']}-{s['end']}: bloqueo de {_describe(s)}")
    for b in cfg.get("manual_blocks", []):
        if manual_active(b, now):
            until = b.get("until")
            txt = f"hasta las {until[11:16]}" if until else "hasta que se desbloquee"
            _apply(st, b, f"Bloqueo manual de {_describe(b)} {txt}")
    limit = cfg.get("daily_game_limit_min", {}).get(DAYS[now.weekday()])
    if limit is not None:
        left = limit - game_seconds_today / 60
        st.game_minutes_left = max(0.0, round(left, 1))
        if left <= 0:
            st.games = True
            st.reasons.append(f"Límite diario de juego alcanzado ({limit} min)")
    return st


def upcoming_warnings(cfg: dict, now: datetime, state: BlockState) -> list[tuple[str, str]]:
    """Devuelve [(clave_única, mensaje)] para avisar antes de un bloqueo."""
    warn = cfg.get("warn_minutes", 5) or 0
    if warn <= 0:
        return []
    out = []
    soon = now + timedelta(minutes=warn)
    for s in cfg.get("schedules", []):
        if not s.get("enabled", True) or schedule_active(s, now):
            continue
        if schedule_active(s, soon):
            key = f"sched:{s['start']}:{s['type']}:{now.date()}"
            out.append((key, f"En {warn} minutos empieza el bloqueo de {_describe(s)} "
                             f"({s['start']}-{s['end']}). ¡Ve guardando la partida!"))
    left = state.game_minutes_left
    if left is not None and 0 < left <= warn and not state.games:
        out.append((f"limit:{now.date()}",
                    f"Te quedan {int(round(left))} minutos de juego hoy. ¡Ve guardando la partida!"))
    return out


# ----------------------------------------------------------------------------
# Rangos de IP para el bloqueo de internet
# ----------------------------------------------------------------------------
_LOCAL_V4 = ["0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16",
             "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/3"]


def public_ipv4_ranges(extra_allowed: list[str] | None = None) -> list[str]:
    """Rangos 'a-b' que cubren todo IPv4 salvo la red local (y las IPs extra
    permitidas). Se usan en una regla de bloqueo del Firewall de Windows, de
    forma que la red de casa (y el PC del padre) siguen accesibles."""
    excluded = [ipaddress.ip_network(n) for n in _LOCAL_V4]
    for ip in extra_allowed or []:
        excluded.append(ipaddress.ip_network(ip, strict=False))
    spans = sorted((int(n.network_address), int(n.broadcast_address)) for n in excluded
                   if n.version == 4)
    out, cur = [], 0
    for a, b in spans:
        if a > cur:
            out.append((cur, a - 1))
        cur = max(cur, b + 1)
    if cur <= 0xFFFFFFFF:
        out.append((cur, 0xFFFFFFFF))
    return [f"{ipaddress.IPv4Address(a)}-{ipaddress.IPv4Address(b)}" for a, b in out]
