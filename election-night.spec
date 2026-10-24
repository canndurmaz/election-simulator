# PyInstaller spec for the desktop app:  pyinstaller election-night.spec
# Output: dist/ElectionNight/ (folder build; start ElectionNight or ElectionNight.exe inside it).
from PyInstaller.utils.hooks import collect_submodules

hidden = (
    # uvicorn picks its loop / HTTP / WebSocket implementations by import string at runtime,
    # which PyInstaller's static analysis cannot see.
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + ["app.server"]
)

a = Analysis(
    ["backend/desktop.py"],
    pathex=["backend"],
    # Layout inside the bundle mirrors what the code expects: app/ next to maps/ (maps.py looks
    # one level above its package), frontend/ is located via ELECTION_FRONTEND set by desktop.py.
    datas=[("backend/maps", "maps"), ("frontend", "frontend")],
    hiddenimports=hidden,
    excludes=["pytest", "httpx", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ElectionNight",
    console=False,
    icon="assets/icon.png",
)
coll = COLLECT(exe, a.binaries, a.datas, name="ElectionNight")
