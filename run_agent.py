"""Arranca el agente en el PC del hijo:  python run_agent.py [agente.json]"""
import json
import logging
import logging.handlers
import sys
from pathlib import Path

from control_parental.agent import Agent

HERE = Path(__file__).resolve().parent


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "agente.json"
    settings = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    data_dir = cfg_path.parent
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.handlers.RotatingFileHandler(data_dir / "agente.log", maxBytes=1_000_000,
                                                       backupCount=3, encoding="utf-8"),
                  logging.StreamHandler()])
    Agent(settings, data_dir).run_forever()


if __name__ == "__main__":
    main()
