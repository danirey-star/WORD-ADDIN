"""Agente: se ejecuta como SYSTEM en el PC del niño.

Cada pocos segundos mira qué juegos/apps están abiertos en la sesión activa,
suma el tiempo, aplica los bloqueos y cada cierto tiempo envía los datos al PC
del padre (y recibe de él la configuración). También sirve en
http://127.0.0.1:8766 una página de solo lectura para que el niño vea su uso."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .db import Store
from .platform import default_platform
from .rules import PC_NAME, Classifier, evaluate, merged_config, upcoming_warnings
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
        self.status: dict = {}
        self.server_ok = False

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

        running: dict[str, str] = {}
        game_procs, by_exe = [], {}
        for p in procs:
            if sess.id is None or p.session != sess.id:
                continue
            c = self.classifier.classify(p.name, p.path)
            if c:
                running[c[0]] = c[1]
                if c[1] == "game":
                    game_procs.append(p)
            by_exe.setdefault(p.name.lower(), []).append(p)

        if active and elapsed > 0:
            items = [(PC_NAME, "pc")] + list(running.items())
            self.store.add_seconds(self.device, now.strftime("%Y-%m-%dT%H:%M"), items, elapsed)

        game_secs = self.store.game_seconds_on(self.device, now.date())
        state = evaluate(self.config, now, game_secs)

        # Aplicar bloqueos
        to_kill = []
        if state.games:
            to_kill += game_procs
        for exe in state.apps:
            to_kill += by_exe.get(exe, [])
        killed = set()
        for p in to_kill:
            if p.pid not in killed:
                killed.add(p.pid)
                self.platform.kill(p)
        if killed and active:
            key = "killed:" + now.strftime("%Y-%m-%dT%H:%M")
            if key not in self.warned:
                self.warned.add(key)
                self.platform.notify(sess, "Programa cerrado por el control parental: "
                                     + "; ".join(state.reasons))
        self.platform.set_internet_block(state.internet, self.allowed_ips())

        if active:
            for key, msg in upcoming_warnings(self.config, now, state):
                if key not in self.warned:
                    self.warned.add(key)
                    self.platform.notify(sess, msg)

        self.status = {
            "version": __version__,
            "time": now.isoformat(timespec="seconds"),
            "session_active": active,
            "user": sess.user,
            "running": sorted(n for n, c in running.items()),
            "game_seconds_today": round(game_secs),
            "blocks": state.to_dict(),
            "server_ok": self.server_ok,
        }

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
            log.info("Panel del niño en http://127.0.0.1:%s", port)
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
    """API de solo lectura para el panel del niño."""

    def __init__(self, agent: Agent):
        self.agent = agent

    def handle(self, method, path, q, body, req):
        a = self.agent
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
