"""Servidor: se ejecuta en el PC del padre. Recibe los datos de uno o varios
PCs (agentes), guarda el histórico y ofrece el panel en http://127.0.0.1:8765
para ver estadísticas y cambiar horarios/bloqueos."""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timedelta

from .db import Store
from .rules import merged_config
from .web import ApiError, parse_date

log = logging.getLogger("control_parental")
ONLINE_SECONDS = 120
VALID_TYPES = {"internet", "games", "apps"}


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ApiError(400, "la configuración debe ser un objeto JSON")
    cfg = merged_config(cfg)
    for s in cfg["schedules"]:
        if s.get("type") not in VALID_TYPES:
            raise ApiError(400, f"tipo de horario no válido: {s.get('type')}")
        for k in ("start", "end"):
            datetime.strptime(s[k], "%H:%M")
    for b in cfg["manual_blocks"]:
        if b.get("type") not in VALID_TYPES:
            raise ApiError(400, f"tipo de bloqueo no válido: {b.get('type')}")
        if b.get("until"):
            datetime.fromisoformat(b["until"])
    for d, v in cfg["daily_game_limit_min"].items():
        if v is not None and (not isinstance(v, (int, float)) or v < 0):
            raise ApiError(400, f"límite no válido para {d}")
    return cfg


class ServerApi:
    def __init__(self, store: Store, token: str):
        self.store = store
        self.token = token

    def _config(self, device: str) -> dict:
        d = self.store.get_device(device)
        cfg = d["config"] if d and d["config"] else None
        if cfg is None:
            cfg = merged_config(None)
            self.store.set_config(device, cfg)
        return cfg

    def _prune_manual(self, cfg: dict) -> dict:
        now = datetime.now()
        cfg["manual_blocks"] = [b for b in cfg.get("manual_blocks", [])
                                if not b.get("until") or datetime.fromisoformat(b["until"]) > now]
        return cfg

    def handle(self, method, path, q, body, req):
        # Los agentes se autentican con token; el panel solo desde este mismo PC.
        if path == "/api/report" and method == "POST":
            tok = req.headers.get("X-Token", "")
            if not hmac.compare_digest(tok, self.token):
                raise ApiError(401, "token incorrecto")
            device = str(body["device"])[:64]
            self.store.upsert_rows(device, body.get("rows", []))
            self.store.touch_device(device, body.get("status") or {})
            return {"ok": True, "config": self._config(device)}
        if not req.is_local():
            raise ApiError(403, "el panel solo está disponible desde el PC del padre")

        if path == "/api/mode":
            return {"readonly": False}
        if path == "/api/devices":
            out = []
            for d in self.store.list_devices():
                seen = d["last_seen"]
                online = bool(seen) and (datetime.now() - datetime.fromisoformat(seen)
                                         ).total_seconds() < ONLINE_SECONDS
                out.append({"device": d["device"], "last_seen": seen, "status": d["status"],
                            "online": online})
            return out
        device = q.get("device") or ""
        if not device:
            raise ApiError(400, "falta device")
        if path == "/api/usage":
            return self.store.usage_report(device, q.get("period", "day"), parse_date(q))
        if path == "/api/config" and method == "GET":
            return self._prune_manual(self._config(device))
        if path == "/api/config" and method == "PUT":
            cfg = validate_config(body)
            self.store.set_config(device, cfg)
            return cfg
        if path == "/api/block" and method == "POST":
            kind = body.get("type")
            if kind not in VALID_TYPES:
                raise ApiError(400, "type debe ser internet, games o apps")
            minutes = body.get("minutes")
            until = None
            if minutes:
                until = (datetime.now() + timedelta(minutes=int(minutes))).isoformat(timespec="minutes")
            cfg = self._prune_manual(self._config(device))
            cfg["manual_blocks"] = [b for b in cfg["manual_blocks"] if b["type"] != kind or kind == "apps"]
            blk = {"type": kind, "until": until}
            if kind == "apps":
                apps = [a.strip() for a in body.get("apps", []) if a.strip()]
                if not apps:
                    raise ApiError(400, "indica qué apps bloquear")
                blk["apps"] = apps
            cfg["manual_blocks"].append(blk)
            self.store.set_config(device, cfg)
            return cfg
        if path == "/api/unblock" and method == "POST":
            cfg = self._config(device)
            kind = body.get("type")
            cfg["manual_blocks"] = [b for b in cfg.get("manual_blocks", [])
                                    if kind and b["type"] != kind]
            self.store.set_config(device, cfg)
            return cfg
        raise ApiError(404, "no encontrado")
