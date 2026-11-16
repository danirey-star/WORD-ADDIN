"""Almacenamiento SQLite compartido por el agente (PC del niño) y el servidor
(PC del padre). Guarda segundos de uso por minuto, por programa."""
from __future__ import annotations

import json
import sqlite3
import threading
from calendar import monthrange
from datetime import date, datetime, timedelta

from .rules import PC_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    device   TEXT NOT NULL,
    minute   TEXT NOT NULL,          -- 'YYYY-MM-DDTHH:MM' hora local del PC del niño
    name     TEXT NOT NULL,
    category TEXT NOT NULL,          -- 'game' | 'app' | 'pc'
    seconds  REAL NOT NULL,
    dirty    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (device, minute, name)
);
CREATE INDEX IF NOT EXISTS usage_minute ON usage(device, minute);
CREATE TABLE IF NOT EXISTS devices (
    device    TEXT PRIMARY KEY,
    last_seen TEXT,
    status    TEXT,
    config    TEXT
);
"""

DAY_NAMES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # --- escritura -----------------------------------------------------------
    def add_seconds(self, device: str, minute: str, items: list[tuple[str, str]], secs: float):
        with self.lock:
            self.conn.executemany(
                "INSERT INTO usage(device, minute, name, category, seconds, dirty) "
                "VALUES (?,?,?,?,?,1) ON CONFLICT(device, minute, name) DO UPDATE SET "
                "seconds = MIN(60, seconds + excluded.seconds), dirty = 1",
                [(device, minute, n, c, secs) for n, c in items])
            self.conn.commit()

    def upsert_rows(self, device: str, rows: list[dict]):
        """Servidor: guarda filas enviadas por el agente (idempotente)."""
        with self.lock:
            self.conn.executemany(
                "INSERT INTO usage(device, minute, name, category, seconds, dirty) "
                "VALUES (?,?,?,?,?,0) ON CONFLICT(device, minute, name) DO UPDATE SET "
                "seconds = excluded.seconds, category = excluded.category",
                [(device, r["minute"], r["name"], r["category"], float(r["seconds"]))
                 for r in rows])
            self.conn.commit()

    def dirty_rows(self, device: str, limit: int = 5000) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(
                "SELECT minute, name, category, seconds FROM usage "
                "WHERE device=? AND dirty=1 ORDER BY minute LIMIT ?", (device, limit))
            return [dict(minute=m, name=n, category=c, seconds=s) for m, n, c, s in cur]

    def mark_clean(self, device: str, rows: list[dict]):
        # Solo se limpian si no han cambiado desde que se enviaron.
        with self.lock:
            self.conn.executemany(
                "UPDATE usage SET dirty=0 WHERE device=? AND minute=? AND name=? AND seconds=?",
                [(device, r["minute"], r["name"], r["seconds"]) for r in rows])
            self.conn.commit()

    # --- dispositivos (servidor) --------------------------------------------
    def get_device(self, device: str) -> dict | None:
        with self.lock:
            r = self.conn.execute("SELECT device, last_seen, status, config FROM devices "
                                  "WHERE device=?", (device,)).fetchone()
        if not r:
            return None
        return {"device": r[0], "last_seen": r[1], "status": json.loads(r[2] or "{}"),
                "config": json.loads(r[3]) if r[3] else None}

    def list_devices(self) -> list[dict]:
        with self.lock:
            names = [r[0] for r in self.conn.execute(
                "SELECT device FROM devices ORDER BY device")]
        return [self.get_device(n) for n in names]

    def touch_device(self, device: str, status: dict):
        now = datetime.now().isoformat(timespec="seconds")
        with self.lock:
            self.conn.execute(
                "INSERT INTO devices(device, last_seen, status) VALUES (?,?,?) "
                "ON CONFLICT(device) DO UPDATE SET last_seen=excluded.last_seen, "
                "status=excluded.status", (device, now, json.dumps(status)))
            self.conn.commit()

    def set_config(self, device: str, cfg: dict):
        with self.lock:
            self.conn.execute(
                "INSERT INTO devices(device, config) VALUES (?,?) "
                "ON CONFLICT(device) DO UPDATE SET config=excluded.config",
                (device, json.dumps(cfg)))
            self.conn.commit()

    # --- consultas ------------------------------------------------------------
    def seconds_between(self, device: str, start: str, end: str, category: str) -> float:
        with self.lock:
            r = self.conn.execute(
                "SELECT COALESCE(SUM(seconds),0) FROM usage WHERE device=? AND minute>=? "
                "AND minute<? AND category=?", (device, start, end, category)).fetchone()
        return float(r[0])

    def seconds_by_name_on(self, device: str, day: date) -> dict[str, float]:
        nxt = day + timedelta(days=1)
        with self.lock:
            cur = self.conn.execute(
                "SELECT name, SUM(seconds) FROM usage WHERE device=? AND minute>=? AND minute<? "
                "GROUP BY name", (device, day.isoformat(), nxt.isoformat()))
            return {n: float(s) for n, s in cur}

    def game_seconds_on(self, device: str, day: date) -> float:
        nxt = day + timedelta(days=1)
        return self.seconds_between(device, day.isoformat(), nxt.isoformat(), "game")

    def usage_report(self, device: str, period: str, ref: date) -> dict:
        """Datos para las gráficas: period = 'day' (por horas), 'week' o 'month' (por días)."""
        if period == "day":
            start, end = ref, ref + timedelta(days=1)
            labels = [f"{h:02d}h" for h in range(24)]
            key_len, keys = 13, [f"{ref.isoformat()}T{h:02d}" for h in range(24)]
            title = ref.strftime("%d/%m/%Y")
        elif period == "week":
            start = ref - timedelta(days=ref.weekday())
            end = start + timedelta(days=7)
            days = [start + timedelta(days=i) for i in range(7)]
            labels = [f"{DAY_NAMES[d.weekday()]} {d.day}" for d in days]
            key_len, keys = 10, [d.isoformat() for d in days]
            title = f"{start.strftime('%d/%m')} – {(end - timedelta(days=1)).strftime('%d/%m/%Y')}"
        elif period == "month":
            start = ref.replace(day=1)
            n = monthrange(ref.year, ref.month)[1]
            end = start + timedelta(days=n)
            days = [start + timedelta(days=i) for i in range(n)]
            labels = [str(d.day) for d in days]
            key_len, keys = 10, [d.isoformat() for d in days]
            title = start.strftime("%m/%Y")
        else:
            raise ValueError("period debe ser day, week o month")
        with self.lock:
            cur = self.conn.execute(
                f"SELECT substr(minute,1,{key_len}) k, name, category, SUM(seconds) "
                "FROM usage WHERE device=? AND minute>=? AND minute<? GROUP BY k, name",
                (device, start.isoformat(), end.isoformat()))
            rows = cur.fetchall()
        idx = {k: i for i, k in enumerate(keys)}
        series: dict[str, list[float]] = {}
        cats: dict[str, str] = {}
        for k, name, cat, secs in rows:
            if k not in idx:
                continue
            series.setdefault(name, [0.0] * len(keys))[idx[k]] += secs
            cats[name] = cat
        totals = {n: sum(v) for n, v in series.items()}
        n_days = (end - start).days
        return {
            "period": period, "title": title, "start": start.isoformat(),
            "labels": labels,
            "series": [{"name": n, "category": cats[n], "seconds": series[n],
                        "total": totals[n], "avg_per_day": totals[n] / n_days}
                       for n in sorted(series, key=lambda x: -totals[x])],
            "pc_total": totals.get(PC_NAME, 0.0),
            "games_total": sum(t for n, t in totals.items() if cats[n] == "game"),
            "days": n_days,
        }
