# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_memorymirror.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui'), ('data/demo.db', 'data'), ('data/faiss_index.bin', 'data'), ('data/faiss_meta.jsonl', 'data'), ('backend', 'backend')],
    hiddenimports=['uvicorn', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websocket.auto', 'uvicorn.middleware.asgi2', 'uvicorn.middleware.wsgi', 'uvicorn.lifespan.on', 'fastapi', 'pydantic', 'jieba', 'jieba.posseg', 'jieba.analyse', 'faiss', 'numpy', 'multipart', 'sse_starlette', 'starlette', 'starlette.routing', 'starlette.middleware', 'starlette.staticfiles', 'websockets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemoryMirror',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MemoryMirror',
)
