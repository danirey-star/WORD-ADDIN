"""Arranca el servidor en el PC del padre:  python run_server.py"""
import json
import logging
import secrets
import sys
from pathlib import Path

from control_parental.db import Store
from control_parental.server import ServerApi
from control_parental.web import make_server

HERE = Path(__file__).resolve().parent


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "servidor.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"port": 8765, "token": secrets.token_urlsafe(24),
                                        "db": "servidor.db"}, indent=2), encoding="utf-8")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(cfg_path.parent / "servidor.log", encoding="utf-8")])
    db = Path(cfg["db"])
    store = Store(str(db if db.is_absolute() else cfg_path.parent / db))
    srv = make_server(ServerApi(store, cfg["token"]), "0.0.0.0", int(cfg["port"]))
    logging.info("Servidor escuchando en el puerto %s", cfg["port"])
    logging.info("Panel: http://127.0.0.1:%s   Token para los agentes: %s", cfg["port"], cfg["token"])
    srv.serve_forever()


if __name__ == "__main__":
    main()
