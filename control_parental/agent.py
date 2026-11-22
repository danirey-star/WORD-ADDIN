"""Agente: se ejecuta como SYSTEM en el PC del hijo.

Cada pocos segundos mira qué juegos/apps están abiertos en la sesión activa,
suma el tiempo, aplica los bloqueos y cada cierto tiempo envía los datos al PC
del padre (y recibe de él la configuración). También sirve en
http://127.0.0.1:8766 una página de solo lectura para que el hijo vea su uso."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .db import Store
from .platform import default_platform
from .rules import PC_NAME, Classifier, evaluate, merged_config, upcoming_warnings

CODE_ROOT = Path(__file__).resolve().parent.parent  # carpeta con run_agent.py
IDLE_SECONDS = 300   # sin tocar teclado/ratón más de 5 min = no se está usando
FG_FRESH = 15        # datos del ayudante válidos durante 15 s
# Alarmas que se pone el propio hijo desde su panel.
ALARM_KINDS = {"timer", "games", "social"}
ALARM_TEXT = {"timer": "Han pasado los {m} minutos que pediste.",
              "games": "Ya llevas {m} minutos de juego hoy.",
              "social": "Ya llevas {m} minutos de redes y vídeo hoy."}
from .update import apply_package, code_hash
from .web import ApiError, make_server, parse_date

log = logging.getLogger("control_parental")


class Agent:
    def __init__(self, settings: dict, data_dir: Path, platform=None):
        self.s = settings
        self.device = settings["device"]
        self.data_dir = data_dir
        self.store = Store(str(data_dir / "agente.db"))
        self.platform = platform or default_platform()
        self.cache_file = data_dir / "config_cache.json"
        self.config = merged_config(self._load_cache())
        self.classifier = Classifier(self.config)
        self.interval = float(settings.get("interval_seconds", 5))
        self.sync_every = float(settings.get("sync_seconds", 30))
        self.last_tick = time.monotonic()
        self.last_sync = 0.0
        self.warned: set[str] = set()
        self.running_since: dict[str, datetime] = {}  # nombre -> desde cuándo está abierto
        self.status: dict = {}
        self.server_ok = False
        self.fg: dict | None = None        # último dato del ayudante: exe, title, idle, t
        self.social_blocked = False
        self.block_reasons: list[str] = []
        self.port = int(settings.get("local_port", 8766) or 0)
        self.code_hash = code_hash(CODE_ROOT)
        self.update_failed: str | None = None
        self.alerts: list[str] = []        # avisos pendientes de mostrar por el ayudante
        self.alarms_file = data_dir / "alarmas.json"
        try:
            self.alarms: list[dict] = json.loads(self.alarms_file.read_text(encoding="utf-8"))
        except Exception:
            self.alarms = []
        self.history_file = data_dir / "alarmas_historial.json"
        try:
            self.history: list[dict] = json.loads(self.history_file.read_text(encoding="utf-8"))
        except Exception:
            self.history = []

    # --- configuración ------------------------------------------------------
    def _load_cache(self):
        try:
            return json.loads(self.cache_file.read_text(encoding="utf-8-sig"))
        except Exception:
            return None

    def apply_config(self, cfg: dict):
        if cfg != self._load_cache():
            self.cache_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        self.config = merged_config(cfg)
        self.classifier = Classifier(self.config)

    def allowed_ips(self) -> list[str]:
        host = urlparse(self.s.get("server_url", "")).hostname
        ips = []
        if host:
            import socket
            try:
                ips = list({ai[4][0] for ai in socket.getaddrinfo(host, None, socket.AF_INET)})
            except OSError:
                pass
        return ips + list(self.s.get("always_allowed_ips", []))

    # --- bucle principal ----------------------------------------------------
    def tick(self, now: datetime | None = None):
        now = now or datetime.now()
        mono = time.monotonic()
        # Si el PC estuvo suspendido no se cuenta ese hueco.
        elapsed = min(mono - self.last_tick, self.interval * 2)
        self.last_tick = mono

        procs = self.platform.processes()
        sess = self.platform.session(procs)
        active = sess.id is not None and not sess.locked
        if sess.id is not None and self.port:
            self.platform.ensure_helper(sess, procs, self.port)

        running: dict[str, str] = {}
        game_procs, social_procs, by_exe = [], [], {}
        social_exes = self.classifier.social_exes()
        for p in procs:
            if sess.id is None or p.session != sess.id:
                continue
            c = self.classifier.classify(p.name, p.path)
            if c:
                running[c[0]] = c[1]
                if c[1] == "game":
                    game_procs.append(p)
            if p.name.lower() in social_exes:
                social_procs.append(p)
            by_exe.setdefault(p.name.lower(), []).append(p)

        # Ventana en primer plano (la manda el ayudante). Las redes y el vídeo solo cuentan
        # mientras se están usando, no por estar abiertos.
        fg = self.fg if self.fg and mono - self.fg["t"] < FG_FRESH else None
        social = self.classifier.social_for(fg["exe"], fg["title"]) if fg and active else None
        if social:
            running[social] = "social"
        # Uso real: sesión desbloqueada y con actividad reciente (o jugando / viendo vídeo).
        # Si el ayudante no responde se cuenta como antes: sesión desbloqueada.
        in_use = active and (fg is None or fg["idle"] < IDLE_SECONDS or bool(game_procs) or bool(social))

        if in_use and elapsed > 0:
            # Cada momento cuenta una sola vez y solo lo que tiene delante (redes > programa
            # delante). Lo que está abierto detrás no suma, salvo un juego cuando no hay nada
            # conocido delante (o el ayudante no responde). Así la suma = uso real del ordenador.
            games = sorted(n for n, c in running.items() if c == "game")
            fg_prog = self.classifier.classify(fg["exe"], None) if fg else None
            if social:
                counted = social
            elif fg_prog and fg_prog[0] in running:
                counted = fg_prog[0]
            else:
                counted = games[0] if games else None
            items = [(PC_NAME, "pc")] + ([(counted, running[counted])] if counted else [])
            self.store.add_seconds(self.device, now.strftime("%Y-%m-%dT%H:%M"), items, elapsed)

        # Desde cuándo lleva abierto cada programa (se reinicia al cerrarlo o bloquear la sesión).
        if not active:
            self.running_since.clear()
        else:
            self.running_since = {n: self.running_since.get(n, now) for n in running}
        today = self.store.seconds_by_name_on(self.device, now.date())
        now_list = [{"name": n, "category": c,
                     "since": self.running_since[n].isoformat(timespec="minutes"),
                     "open_seconds": round((now - self.running_since[n]).total_seconds()),
                     "seconds_today": round(today.get(n, 0))}
                    for n, c in sorted(running.items(), key=lambda x: (x[1] != "game", x[0]))
                    ] if active else []

        game_secs = self.store.game_seconds_on(self.device, now.date())
        social_secs = self.store.category_seconds_on(self.device, now.date(), "social")
        state = evaluate(self.config, now, game_secs, social_secs)
        self.social_blocked, self.block_reasons = state.social, state.reasons

        # Aplicar bloqueos (las webs: archivo hosts + el ayudante cierra la pestaña abierta)
        to_kill = []
        if state.games:
            to_kill += game_procs
        if state.social:
            to_kill += social_procs
        self.platform.set_sites_block(self.classifier.blocked_domains() if state.social else [])
        for exe in state.apps:
            to_kill += by_exe.get(exe, [])
        killed, failed = set(), []
        game_pids = {p.pid for p in game_procs}
        for p in to_kill:
            if p.pid not in killed:
                killed.add(p.pid)
                if not self.platform.kill(p) and p.pid in game_pids:
                    failed.append(p)
        # Juegos con antitrampas pueden no dejarse cerrar: mientras sigan abiertos con el juego
        # bloqueado, se corta internet (Fortnite, Valorant... no funcionan sin conexión).
        if failed:
            names = sorted({self.classifier.classify(p.name, p.path)[0] for p in failed})
            state.internet = True
            state.reasons.append(f"No se pudo cerrar {', '.join(names)}: internet cortado mientras siga abierto")
        if killed and active:
            key = "killed:" + now.strftime("%Y-%m-%dT%H:%M")
            if key not in self.warned:
                self.warned.add(key)
                self.alert(sess, "Programa cerrado por el control parental: "
                                     + "; ".join(state.reasons))
        self.platform.set_internet_block(state.internet, self.allowed_ips())

        if active:
            for key, msg in upcoming_warnings(self.config, now, state):
                if key not in self.warned:
                    self.warned.add(key)
                    self.alert(sess, msg)
            self.check_alarms(now, sess, game_secs, social_secs, running)
        self.track_closures(now, sess, running, active)

        self.status = {
            "version": __version__,
            "time": now.isoformat(timespec="seconds"),
            "session_active": active,
            "in_use": in_use,
            "idle_seconds": round(fg["idle"]) if fg else None,
            "foreground": {"exe": fg["exe"], "title": fg["title"][:200]} if fg and active else None,
            "user": sess.user,
            "running": sorted(n for n, c in running.items()),
            "now": now_list,
            "game_seconds_today": round(game_secs),
            "social_seconds_today": round(social_secs),
            "blocks": state.to_dict(),
            "server_ok": self.server_ok,
            "alarms": self.alarms,
            "alarm_history": self.history[-20:],
        }

    # --- avisos y alarmas ----------------------------------------------------
    def helper_alive(self) -> bool:
        return bool(self.fg) and time.monotonic() - self.fg["t"] < FG_FRESH

    def alert(self, sess, text: str):
        """Aviso al hijo: lo muestra el ayudante (con sonido, funciona en Windows Home);
        si el ayudante no está, se usa msg.exe."""
        if self.helper_alive():
            self.alerts.append(text)
        else:
            self.platform.notify(sess, text)

    def _save_alarms(self):
        self.alarms_file.write_text(json.dumps(self.alarms, ensure_ascii=False), encoding="utf-8")

    def add_alarm(self, body: dict) -> list[dict]:
        kind = body.get("kind")
        if kind not in ALARM_KINDS:
            raise ApiError(400, "tipo de alarma no válido")
        minutes = int(body.get("minutes") or 0)
        if not 1 <= minutes <= 720:
            raise ApiError(400, "los minutos deben estar entre 1 y 720")
        if len(self.alarms) >= 10:
            raise ApiError(400, "como mucho 10 alarmas a la vez")
        now = datetime.now()
        a = {"id": now.strftime("%Y%m%d%H%M%S%f"), "kind": kind, "minutes": minutes,
             "text": str(body.get("text") or "").strip()[:100], "day": now.date().isoformat(),
             "created": now.isoformat(timespec="minutes")}
        if kind == "timer":
            a["due"] = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        self.alarms.append(a)
        self._save_alarms()
        return self.alarms

    def delete_alarm(self, alarm_id: str) -> list[dict]:
        self.alarms = [a for a in self.alarms if a["id"] != alarm_id]
        self._save_alarms()
        return self.alarms

    def check_alarms(self, now: datetime, sess, game_secs: float, social_secs: float,
                     running: dict[str, str] | None = None):
        running = running or {}
        keep, fired = [], False
        for a in self.alarms:
            if a["kind"] == "timer":
                due = now >= datetime.fromisoformat(a["due"])
            elif a["day"] != now.date().isoformat():
                fired = True  # alarma de "hoy" de un día anterior: se descarta
                continue
            else:
                secs = game_secs if a["kind"] == "games" else social_secs
                due = secs >= a["minutes"] * 60
            if due:
                fired = True
                self.alert(sess, "⏰ " + (a["text"] or ALARM_TEXT[a["kind"]].format(m=a["minutes"])))
                # Historial: qué tenía abierto al sonar, para saber cuándo lo cierra de verdad.
                cats = {"timer": ("game", "social"), "games": ("game",), "social": ("social",)}[a["kind"]]
                opened = sorted(n for n, c in running.items() if c in cats)
                stamp = now.isoformat(timespec="seconds")
                self.history.append({"id": a["id"], "kind": a["kind"], "minutes": a["minutes"],
                                     "text": a["text"], "fired": stamp, "open": opened,
                                     "last_open": stamp, "closed": None if opened else stamp})
            else:
                keep.append(a)
        if fired:
            self.alarms = keep
            self._save_alarms()
            self._save_history()

    def track_closures(self, now: datetime, sess, running: dict[str, str], active: bool):
        """Apunta cuándo cierra de verdad lo que tenía abierto al sonar cada alarma y se lo dice."""
        changed = False
        for h in self.history:
            if h["closed"]:
                continue
            still = [n for n in h["open"] if active and n in running]
            if still:
                stamp = now.isoformat(timespec="seconds")
                changed |= stamp[:16] != h["last_open"][:16]  # se guarda como mucho una vez por minuto
                h["last_open"] = stamp
                continue
            last = datetime.fromisoformat(h["last_open"])
            # Si el PC estuvo apagado o bloqueado, cuenta la última vez que se vio abierto.
            closed = now if (now - last).total_seconds() <= 120 else last
            h["closed"] = closed.isoformat(timespec="seconds")
            changed = True
            fired = datetime.fromisoformat(h["fired"])
            late = max(0, int((closed - fired).total_seconds() // 60))
            if active:
                self.alert(sess, f"Has cerrado {', '.join(h['open'])} a las {closed:%H:%M}, "
                                 + (f"{late} min después de tu alarma de las {fired:%H:%M}."
                                    if late else f"justo con tu alarma de las {fired:%H:%M}. ¡Bien!"))
        week_ago = (now - timedelta(days=7)).isoformat()
        if len(self.history) > 50 or any(h["fired"] < week_ago for h in self.history):
            self.history = [h for h in self.history if h["fired"] >= week_ago][-50:]
            changed = True
        if changed:
            self._save_history()

    def _save_history(self):
        self.history_file.write_text(json.dumps(self.history, ensure_ascii=False), encoding="utf-8")

    def self_update(self, url: str):
        """Descarga el código nuevo del PC del padre, lo instala y reinicia el agente."""
        req = urllib.request.Request(url.rstrip("/") + "/api/update",
                                     headers={"X-Token": self.s.get("token", "")})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                pkg = json.loads(r.read())
            apply_package(CODE_ROOT, pkg["zip"], pkg["hash"])
        except Exception as e:
            log.error("No se pudo actualizar: %s", e)
            self.update_failed = pkg.get("hash") if "pkg" in locals() else None
            return
        log.info("Actualizado a %s; reiniciando", pkg["hash"][:12])
        self.platform.restart_agent()

    def foreground_report(self, body: dict) -> dict:
        """Lo llama el ayudante cada 2 s. Responde close=True si tiene delante una web o app de
        redes/vídeo bloqueada, para que cierre la pestaña."""
        exe, title = str(body.get("exe") or "")[:100], str(body.get("title") or "")[:500]
        self.fg = {"exe": exe, "title": title, "idle": float(body.get("idle") or 0),
                   "t": time.monotonic()}
        name = self.classifier.social_for(exe, title) if self.social_blocked else None
        if name:
            key = "social:" + datetime.now().strftime("%Y-%m-%dT%H:%M")
            if key not in self.warned:
                self.warned.add(key)
                self.alerts.append(f"{name} está bloqueado ahora: " + "; ".join(self.block_reasons))
        alerts, self.alerts = self.alerts, []
        return {"close": bool(name), "alerts": alerts}

    def sync(self):
        url = self.s.get("server_url")
        if not url:
            return
        rows = self.store.dirty_rows(self.device)
        payload = json.dumps({"device": self.device, "rows": rows, "status": self.status}).encode()
        req = urllib.request.Request(url.rstrip("/") + "/api/report", data=payload, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Token": self.s.get("token", "")})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                resp = json.loads(r.read())
            self.store.mark_clean(self.device, rows)
            if isinstance(resp.get("config"), dict):
                self.apply_config(resp["config"])
            self.server_ok = True
            new = resp.get("code_hash")
            if new and new != self.code_hash and new != self.update_failed:
                self.self_update(url)
        except Exception as e:
            if self.server_ok:
                log.warning("No se pudo contactar con el PC del padre: %s", e)
            self.server_ok = False

    def run_forever(self):
        log.info("Agente %s iniciado para '%s'", __version__, self.device)
        port = int(self.s.get("local_port", 8766))
        if port:
            srv = make_server(LocalApi(self), "127.0.0.1", port)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            log.info("Panel del hijo en http://127.0.0.1:%s", port)
        while True:
            try:
                self.tick()
                if time.monotonic() - self.last_sync >= self.sync_every:
                    self.last_sync = time.monotonic()
                    self.sync()
            except Exception:
                log.exception("Error en el ciclo del agente")
            time.sleep(self.interval)


class LocalApi:
    """API de solo lectura para el panel del hijo."""

    def __init__(self, agent: Agent):
        self.agent = agent

    def handle(self, method, path, q, body, req):
        a = self.agent
        if method == "POST" and path == "/api/foreground":
            return a.foreground_report(body or {})
        # Lo único que el hijo puede cambiar: sus propias alarmas.
        if method == "POST" and path == "/api/alarm":
            return a.add_alarm(body or {})
        if method == "POST" and path == "/api/alarm/delete":
            return a.delete_alarm(str((body or {}).get("id")))
        if method != "GET":
            raise ApiError(403, "solo lectura")
        if path == "/api/mode":
            return {"readonly": True, "device": a.device}
        if path == "/api/devices":
            return [{"device": a.device, "last_seen": a.status.get("time"), "status": a.status,
                     "online": True}]
        if path == "/api/usage":
            return a.store.usage_report(a.device, q.get("period", "day"), parse_date(q))
        if path == "/api/config":
            return a.config
        raise ApiError(404, "no encontrado")
