import json
import threading
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from control_parental.agent import Agent
from control_parental.db import Store
from control_parental.platform import FakePlatform, Proc
from control_parental.rules import (PC_NAME, Classifier, evaluate, merged_config,
                                    public_ipv4_ranges, schedule_active, upcoming_warnings)
from control_parental.server import ServerApi
from control_parental.web import make_server

MON_2230 = datetime(2026, 11, 16, 22, 30)  # lunes


def test_classifier():
    c = Classifier(merged_config(None))
    assert c.classify("FortniteClient-Win64-Shipping.exe", None) == ("Fortnite", "game")
    assert c.classify("steam.exe", r"C:\Steam\steam.exe") is None
    assert c.classify("hl.exe", r"D:\SteamLibrary\steamapps\common\Hollow Knight\hl.exe") == ("Hollow Knight", "game")
    assert c.classify("x.exe", r"C:\Program Files\Epic Games\Launcher\x.exe") is None
    assert c.classify("chrome.exe", None) == ("Navegador", "app")
    assert c.classify("notepad.exe", r"C:\Windows\notepad.exe") is None


def test_schedules_cross_midnight():
    s = {"days": ["mon"], "start": "22:00", "end": "07:00", "type": "internet"}
    assert schedule_active(s, MON_2230)
    assert schedule_active(s, datetime(2026, 11, 17, 6, 59))      # martes de madrugada
    assert not schedule_active(s, datetime(2026, 11, 17, 7, 0))
    assert not schedule_active(s, datetime(2026, 11, 16, 6, 0))   # lunes madrugada (domingo no está)
    s2 = {"days": ["sat", "sun"], "start": "10:00", "end": "12:00", "type": "games"}
    assert not schedule_active(s2, datetime(2026, 11, 16, 11, 0))
    assert schedule_active(s2, datetime(2026, 11, 21, 11, 0))


def test_evaluate_limits_and_manual():
    cfg = merged_config({"daily_game_limit_min": {"mon": 60},
                         "manual_blocks": [{"type": "apps", "apps": ["Discord.exe"], "until": "2026-11-16T23:00"},
                                           {"type": "internet", "until": "2026-11-16T20:00"}]})
    st = evaluate(cfg, MON_2230, game_seconds_today=30 * 60)
    assert not st.games and st.game_minutes_left == 30 and st.apps == {"discord.exe"} and not st.internet
    st = evaluate(cfg, MON_2230, game_seconds_today=61 * 60)
    assert st.games and st.game_minutes_left == 0
    st = evaluate(cfg, MON_2230, game_seconds_today=57 * 60)
    assert any("Te quedan 3" in m for _, m in upcoming_warnings(cfg, MON_2230, st))


def test_ip_ranges_exclude_lan():
    r = public_ipv4_ranges(["8.8.8.8"])
    assert r[0] == "1.0.0.0-8.8.8.7" and "8.8.8.9-9.255.255.255" in r
    assert "11.0.0.0-127.255.255.255" not in r
    assert "11.0.0.0-126.255.255.255" in r and r[-1] == "192.169.0.0-223.255.255.255"


def test_store_report(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    s.add_seconds("pc", "2026-11-16T10:00", [("Fortnite", "game"), (PC_NAME, "pc")], 40)
    s.add_seconds("pc", "2026-11-16T10:00", [("Fortnite", "game")], 40)  # tope 60 s/min
    s.add_seconds("pc", "2026-11-18T21:05", [("Roblox", "game")], 30)
    day = s.usage_report("pc", "day", date(2026, 11, 16))
    fn = next(x for x in day["series"] if x["name"] == "Fortnite")
    assert fn["total"] == 60 and fn["seconds"][10] == 60 and day["games_total"] == 60
    week = s.usage_report("pc", "week", date(2026, 11, 19))
    assert week["labels"][0] == "Lun 16" and week["games_total"] == 90
    month = s.usage_report("pc", "month", date(2026, 11, 1))
    assert len(month["labels"]) == 30


def _req(url, data=None, method=None, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Token"] = token
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                               method=method, headers=h)
    with urllib.request.urlopen(r, timeout=5) as resp:
        return json.loads(resp.read())


def test_end_to_end(tmp_path):
    store = Store(str(tmp_path / "srv.db"))
    srv = make_server(ServerApi(store, "secreto"), "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    fake = FakePlatform()
    fake.procs = [Proc(1, "RobloxPlayerBeta.exe", None, 1), Proc(2, "Discord.exe", None, 1),
                  Proc(3, "explorer.exe", None, 1), Proc(4, "cs2.exe", None, 0)]  # sesión 0 no cuenta
    agent = Agent({"device": "PC-Hijo", "server_url": base, "token": "secreto", "interval_seconds": 5},
                  tmp_path, platform=fake)
    agent.last_tick -= 5
    agent.tick()
    assert set(agent.status["running"]) == {"Roblox", "Discord"}
    agent.sync()
    assert agent.server_ok

    devs = _req(base + "/api/devices")
    assert devs[0]["device"] == "PC-Hijo" and devs[0]["online"]
    u = _req(base + f"/api/usage?device=PC-Hijo&period=day&date={date.today()}")
    assert {s["name"] for s in u["series"]} == {"Roblox", "Discord", PC_NAME}

    # El padre bloquea juegos e internet desde el panel
    _req(base + "/api/block?device=PC-Hijo", {"type": "games", "minutes": 30}, "POST")
    _req(base + "/api/block?device=PC-Hijo", {"type": "internet", "minutes": None}, "POST")
    agent.sync()
    agent.tick()
    assert fake.killed == ["RobloxPlayerBeta.exe"] and fake.internet_blocked
    assert fake.messages and "control parental" in fake.messages[0]

    # Franja horaria + guardado de configuración
    cfg = _req(base + "/api/config?device=PC-Hijo")
    cfg["schedules"] = [{"days": ["mon"], "start": "21:00", "end": "07:00", "type": "apps", "apps": ["Discord.exe"]}]
    _req(base + "/api/config?device=PC-Hijo", cfg, "PUT")
    _req(base + "/api/unblock?device=PC-Hijo", {"type": None}, "POST")
    agent.sync()
    agent.tick(now=MON_2230)
    assert "Discord.exe" in fake.killed and not fake.internet_blocked

    # Token incorrecto -> 401
    with pytest.raises(urllib.error.HTTPError) as e:
        _req(base + "/api/report", {"device": "x", "rows": []}, "POST", token="malo")
    assert e.value.code == 401

    # El panel del niño es de solo lectura
    kid = make_server(__import__("control_parental.agent", fromlist=["LocalApi"]).LocalApi(agent), "127.0.0.1", 0)
    kb = f"http://127.0.0.1:{kid.server_address[1]}"
    threading.Thread(target=kid.serve_forever, daemon=True).start()
    assert _req(kb + "/api/mode")["readonly"]
    with pytest.raises(urllib.error.HTTPError):
        _req(kb + "/api/block?device=PC-Hijo", {"type": "games"}, "POST")
    html = urllib.request.urlopen(kb + "/").read().decode()
    assert "Tiempo de pantalla" in html
    srv.shutdown(); kid.shutdown()


def test_locked_session_not_counted(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "cs2.exe", None, 1)]
    fake.sess.locked = True
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    agent.last_tick -= 5
    agent.tick()
    assert agent.store.dirty_rows("d") == []
