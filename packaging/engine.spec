# PyInstaller spec — freezes the engine into one self-contained binary.
#
# This is what makes the app installable for people who do not have Python:
# the user downloads a .dmg or .exe, and the Python runtime rides inside it.
# Electron bundles its own Node the same way, so neither runtime is ever the
# user's problem.
#
# Build:  pyinstaller packaging/engine.spec --distpath packaging/engine-dist
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + ["uvicorn.protocols.websockets.websockets_impl",
       "uvicorn.protocols.http.httptools_impl",
       "uvicorn.lifespan.on"]
)

a = Analysis(
    ["../engine/meshharness/__main__.py"],
    pathex=["../engine"],
    binaries=[],
    # The routing table is DATA, not code — PyInstaller will not find it by
    # import analysis, and without it smart routing silently degrades to the
    # pinned model.
    datas=[("../engine/meshharness/core/routing_table.json",
            "meshharness/core")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "rich", "prompt_toolkit"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="mesh-code-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # stdout carries the ready handshake
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name="mesh-code-engine",
)
