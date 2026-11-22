"""Actualización por red: el servidor (PC del padre) ofrece el código del agente y el agente
(PC del hijo) lo descarga cuando cambia. Así solo hay que instalar a mano una vez."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import zipfile
from pathlib import Path

# Lo que se actualiza: el código. Python, la configuración y los datos no se tocan.
ROOT_FILES = ["run_agent.py", "ayudante.py"]


def code_files(root: Path) -> dict[str, bytes]:
    out = {}
    for name in ROOT_FILES:
        p = root / name
        if p.is_file():
            out[name] = p.read_bytes()
    pkg = root / "control_parental"
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".html"):
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def files_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + hashlib.sha256(files[name]).digest())
    return h.hexdigest()


def code_hash(root: Path) -> str:
    return files_hash(code_files(root))


def make_package(root: Path) -> tuple[str, str]:
    """(hash, zip en base64) con el código del agente que hay en root."""
    files = code_files(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    return files_hash(files), base64.b64encode(buf.getvalue()).decode()


def apply_package(root: Path, b64: str, expected: str) -> None:
    """Comprueba el paquete y sustituye los archivos. Si algo no cuadra no toca nada."""
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))) as z:
        files = {}
        for name in z.namelist():
            parts = Path(name).parts
            if name.startswith(("/", "\\")) or ".." in parts or ":" in name:
                raise ValueError(f"ruta no válida en la actualización: {name}")
            files[name] = z.read(name)
    if files_hash(files) != expected:
        raise ValueError("la actualización no coincide con la esperada")
    if "run_agent.py" not in files or not any(n.startswith("control_parental/") for n in files):
        raise ValueError("actualización incompleta")
    for name, data in files.items():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".nuevo")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
