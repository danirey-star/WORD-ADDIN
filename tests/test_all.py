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
    first = agent.status["now"][0]  # los juegos van primero
    assert first["name"] == "Roblox" and first["category"] == "game" and first["seconds_today"] == 5
    since = first["since"]
    agent.tick()
    assert agent.status["now"][0]["since"] == since  # sigue abierto: no se reinicia
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

    # El panel del hijo es de solo lectura
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


# ---------------------------------------------------------------- redes y vídeo
from control_parental.platform import hosts_with_block, HOSTS_BEGIN  # noqa: E402
from control_parental.rules import match_title  # noqa: E402


def test_social_titles():
    c = Classifier(merged_config(None))
    ok = {
        "Cómo hacer pan - YouTube - Google Chrome": "YouTube",
        "(3) YouTube y 2 páginas más - Personal - Microsoft\u200b Edge": "YouTube",
        "TikTok - Make Your Day — Mozilla Firefox": "TikTok",
        "(@pepe) • Instagram photos and videos - Google Chrome": "Instagram",
        "Inicio / X - Google Chrome": "X (Twitter)",
        "(2) Facebook - Brave": "Facebook",
        "r/gaming - Reddit - Google Chrome": "Reddit",
        "ibai - Twitch - Opera": "Twitch",
    }
    for title, name in ok.items():
        assert c.social_for("chrome.exe", title) == name, title
    for title in ["youtube - Buscar con Google - Google Chrome", "Wikipedia - Google Chrome",
                  "Xbox Game Pass - Microsoft Edge", "Mis vídeos de YouTube - Explorador de archivos"]:
        assert c.social_for("msedge.exe", title) is None, title
    assert c.social_for("explorer.exe", "YouTube") is None      # solo en navegadores
    assert c.social_for("TikTok.exe", "lo que sea") == "TikTok"  # app de escritorio
    assert "www.youtube.com" in c.blocked_domains() and "m.facebook.com" in c.blocked_domains()


def test_social_limit_and_block():
    cfg = merged_config({"daily_social_limit_min": {"mon": 60}})
    st = evaluate(cfg, MON_2230, 0, social_seconds_today=58 * 60)
    assert not st.social and st.social_minutes_left == 2
    assert any("redes y vídeo" in m for _, m in upcoming_warnings(cfg, MON_2230, st))
    assert evaluate(cfg, MON_2230, 0, social_seconds_today=60 * 60).social
    cfg = merged_config({"schedules": [{"days": ["mon"], "start": "22:00", "end": "23:00", "type": "social"}]})
    assert evaluate(cfg, MON_2230, 0).social


def test_hosts_block():
    base = "127.0.0.1 localhost\r\n"
    on = hosts_with_block(base, ["youtube.com"])
    assert HOSTS_BEGIN in on and "0.0.0.0 youtube.com" in on and ":: youtube.com" in on
    assert hosts_with_block(on, []) == base
    assert hosts_with_block(hosts_with_block(on, ["a.com"]), []) == base


def test_foreground_social_idle_and_close(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "chrome.exe", None, 1), Proc(2, "TikTok.exe", None, 1)]
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    api = __import__("control_parental.agent", fromlist=["LocalApi"]).LocalApi(agent)
    api.handle("POST", "/api/foreground", {}, {"exe": "chrome.exe", "title": "Gatos - YouTube - Google Chrome", "idle": 2}, None)
    agent.last_tick -= 5
    agent.tick()
    assert agent.status["foreground"]["exe"] == "chrome.exe"
    assert "YouTube" in agent.status["running"] and agent.status["social_seconds_today"] == 5
    # Inactivo 10 min en una web cualquiera: no cuenta como uso del ordenador
    api.handle("POST", "/api/foreground", {}, {"exe": "chrome.exe", "title": "Wikipedia - Google Chrome", "idle": 600}, None)
    pc_before = agent.store.seconds_by_name_on("d", date.today()).get(PC_NAME, 0)
    agent.last_tick -= 5
    agent.tick()
    assert agent.store.seconds_by_name_on("d", date.today()).get(PC_NAME, 0) == pc_before
    assert not agent.status["in_use"]
    # Bloqueo de redes: cierra la app, bloquea dominios y pide cerrar la pestaña
    agent.apply_config({"manual_blocks": [{"type": "social", "until": None}]})
    agent.tick()
    assert "TikTok.exe" in fake.killed and "www.youtube.com" in fake.blocked_domains
    r = api.handle("POST", "/api/foreground", {}, {"exe": "chrome.exe", "title": "Gatos - YouTube - Google Chrome", "idle": 1}, None)
    assert r["close"] and any("YouTube está bloqueado" in m for m in r["alerts"])  # lo muestra el ayudante
    assert not api.handle("POST", "/api/foreground", {}, {"exe": "chrome.exe", "title": "Wikipedia - Google Chrome", "idle": 1}, None)["close"]
    agent.apply_config({})
    agent.tick()
    assert fake.blocked_domains == []


def test_overview(tmp_path):
    s = Store(str(tmp_path / "a.db"))
    s.add_seconds("pc", "2026-11-16T10:00", [(PC_NAME, "pc"), ("Fortnite", "game"), ("Roblox", "game")], 60)
    s.add_seconds("pc", "2026-11-16T10:01", [(PC_NAME, "pc"), ("YouTube", "social")], 60)
    s.add_seconds("pc", "2026-11-16T10:02", [(PC_NAME, "pc")], 60)
    ov = s.usage_report("pc", "day", date(2026, 11, 16))["overview"]
    assert (ov["game"][10], ov["social"][10], ov["other"][10]) == (60, 60, 60)


def test_network_update(tmp_path, monkeypatch):
    import shutil
    import control_parental.agent as agent_mod
    from control_parental.update import apply_package, code_hash, make_package
    repo = Path(__file__).resolve().parent.parent
    srv_root, kid_root = tmp_path / "srv", tmp_path / "kid"
    for root in (srv_root, kid_root):
        shutil.copytree(repo / "control_parental", root / "control_parental",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for f in ("run_agent.py", "ayudante.py"):
            shutil.copy(repo / f, root / f)
    (srv_root / "control_parental" / "nuevo.py").write_text("X = 1\n", encoding="utf-8")
    store = Store(str(tmp_path / "srv.db"))
    srv = make_server(ServerApi(store, "secreto", code_root=srv_root), "127.0.0.1", 0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(agent_mod, "CODE_ROOT", kid_root)
    fake = FakePlatform()
    agent = Agent({"device": "d", "server_url": base, "token": "secreto"}, tmp_path, platform=fake)
    assert agent.code_hash != code_hash(srv_root)
    agent.sync()
    assert getattr(fake, "restarted", False) and code_hash(kid_root) == code_hash(srv_root)
    # Ya igual: no vuelve a actualizar
    fake.restarted = False
    agent.code_hash = code_hash(kid_root)
    agent.sync()
    assert not fake.restarted
    # Un paquete manipulado no se aplica
    h, z = make_package(srv_root)
    with pytest.raises(ValueError):
        apply_package(kid_root, z, "0" * 64)
    with pytest.raises(urllib.error.HTTPError):
        _req(base + "/api/update", token="malo")
    srv.shutdown()


def test_kid_alarms(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "RobloxPlayerBeta.exe", None, 1)]
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    api = __import__("control_parental.agent", fromlist=["LocalApi"]).LocalApi(agent)
    api.handle("POST", "/api/alarm", {}, {"kind": "timer", "minutes": 30, "text": "a cenar"}, None)
    api.handle("POST", "/api/alarm", {}, {"kind": "games", "minutes": 1}, None)
    with pytest.raises(Exception):
        api.handle("POST", "/api/alarm", {}, {"kind": "otra", "minutes": 5}, None)
    with pytest.raises(Exception):
        api.handle("POST", "/api/block", {}, {"type": "games"}, None)  # lo demás sigue siendo solo lectura
    assert len(Agent({"device": "d"}, tmp_path, platform=fake).alarms) == 2  # se guardan
    # Sin ayudante: el aviso va por msg.exe. 61 s de juego -> salta la de juego, no la de 30 min.
    for _ in range(13):
        agent.last_tick -= 5
        agent.tick()
    assert any("1 minutos de juego" in m for m in fake.messages)
    assert [a["kind"] for a in agent.alarms] == ["timer"]
    # Con ayudante: el aviso se le entrega a él
    api.handle("POST", "/api/foreground", {}, {"exe": "x.exe", "title": "", "idle": 0}, None)
    agent.tick(now=datetime.now() + timedelta(minutes=31))
    r = api.handle("POST", "/api/foreground", {}, {"exe": "x.exe", "title": "", "idle": 0}, None)
    assert r["alerts"] == ["⏰ a cenar"] and agent.alarms == []
    assert agent.status["alarms"] == []

def test_anticheat_game_cuts_internet(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "VALORANT-Win64-Shipping.exe", None, 1)]
    fake.unkillable = {"VALORANT-Win64-Shipping.exe"}
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    agent.apply_config({"manual_blocks": [{"type": "games", "until": None}]})
    agent.tick()
    assert fake.internet_blocked and any("No se pudo cerrar Valorant" in r for r in agent.status["blocks"]["reasons"])
    fake.procs = []  # lo cierra él: vuelve internet
    agent.tick()
    assert not fake.internet_blocked


def test_alarm_history_closed(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "FortniteClient-Win64-Shipping.exe", None, 1)]
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    api = __import__("control_parental.agent", fromlist=["LocalApi"]).LocalApi(agent)
    api.handle("POST", "/api/alarm", {}, {"kind": "timer", "minutes": 20}, None)
    t = datetime.now() + timedelta(minutes=20, seconds=5)
    agent.tick(now=t)                                   # suena con Fortnite abierto
    h = agent.status["alarm_history"][0]
    assert h["open"] == ["Fortnite"] and h["closed"] is None
    agent.tick(now=t + timedelta(minutes=7))            # sigue jugando
    fake.procs = []
    agent.tick(now=t + timedelta(minutes=7, seconds=30))  # lo cierra
    h = agent.status["alarm_history"][0]
    assert h["closed"] and any("7 min después de tu alarma" in m for m in fake.messages)
    assert Agent({"device": "d"}, tmp_path, platform=fake).history[0]["closed"] == h["closed"]  # se guarda


def test_overlap_counts_once(tmp_path):
    fake = FakePlatform()
    fake.procs = [Proc(1, "chrome.exe", None, 1), Proc(2, "FortniteClient-Win64-Shipping.exe", None, 1)]
    agent = Agent({"device": "d"}, tmp_path, platform=fake)
    api = __import__("control_parental.agent", fromlist=["LocalApi"]).LocalApi(agent)
    # YouTube delante con el juego abierto detrás: solo cuenta como redes
    api.handle("POST", "/api/foreground", {}, {"exe": "chrome.exe", "title": "Gatos - YouTube - Google Chrome", "idle": 1}, None)
    agent.last_tick -= 5
    agent.tick()
    assert agent.status["social_seconds_today"] == 5 and agent.status["game_seconds_today"] == 0
    # Vuelve al juego: solo cuenta como juego
    api.handle("POST", "/api/foreground", {}, {"exe": "FortniteClient-Win64-Shipping.exe", "title": "Fortnite", "idle": 1}, None)
    agent.last_tick -= 5
    agent.tick()
    assert agent.status["social_seconds_today"] == 5 and agent.status["game_seconds_today"] == 5
