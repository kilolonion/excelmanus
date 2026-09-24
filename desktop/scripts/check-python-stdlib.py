"""Exercise the installed interpreter, including a real spawned Python child."""
from __future__ import annotations

from pathlib import Path
import sys


def child_job(value: int) -> int:
    # Imported in fresh spawn workers, not inherited from a warmed parent.
    import json
    import decimal
    import sqlite3
    import encodings.gb18030

    connection = sqlite3.connect(":memory:")
    try:
        result = connection.execute("select ? * ?", (value, value)).fetchone()[0]
        assert json.loads(json.dumps({"中文": result}))["中文"] == result
        return int(decimal.Decimal(result))
    finally:
        connection.close()


def check_stdlib() -> None:
    import bz2
    import csv
    import ctypes
    import hashlib
    import importlib
    import importlib.resources
    import inspect
    import io
    import json
    import lzma
    import multiprocessing
    import pkgutil
    import pydoc
    import ssl
    import subprocess
    import sysconfig
    import tempfile
    import zipfile
    import zlib
    from concurrent.futures import ProcessPoolExecutor
    from email.message import EmailMessage
    from email.parser import BytesParser

    for encoding in ("utf-8-sig", "gbk", "gb18030", "big5", "utf-16le"):
        assert "中文表格".encode(encoding).decode(encoding) == "中文表格"
    assert list(csv.reader(io.StringIO('字段,金额\n中文,42\n')))[1] == ["中文", "42"]
    data = "中文数据".encode()
    for module in (bz2, lzma, zlib):
        assert module.decompress(module.compress(data)) == data
    assert len(hashlib.sha256(data).digest()) == 32
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED
    assert ctypes.sizeof(ctypes.c_void_p) in (4, 8)
    assert sysconfig.get_config_var("EXT_SUFFIX")
    assert sysconfig.get_python_version() == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert "def dumps" in inspect.getsource(json.dumps)
    stdlib_archive = Path(sys.base_prefix) / ("" if sys.platform == "win32" else "lib") / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
    if not stdlib_archive.exists():
        assert "def dumps" in Path(json.__file__).read_text(encoding="utf-8")
    assert "json" in pydoc.render_doc(json)
    assert "email" in {item.name for item in pkgutil.iter_modules()}
    message = EmailMessage()
    message["Subject"] = "中文报告"
    message.set_content("金额：42")
    assert BytesParser().parsebytes(message.as_bytes()).get_payload(decode=True).decode().strip() == "金额：42"

    # These resources ship in python-build-standalone. Their modules and data
    # must have one coherent location, including through both resource APIs.
    stdlib = Path(sysconfig.get_path("stdlib"))
    for package, name in (("ctypes.macholib", "README.ctypes"), ("email", "architecture.rst")):
        file = stdlib.joinpath(*package.split("."), name)
        if file.is_file():
            module = importlib.import_module(package)
            assert pkgutil.get_data(package, name) == file.read_bytes()
            assert importlib.resources.files(module).joinpath(name).read_bytes() == file.read_bytes()

    with tempfile.TemporaryDirectory(prefix="stdlib-中文-") as directory:
        root = Path(directory)
        with zipfile.ZipFile(root / "数据.zip", "w") as bundle:
            bundle.writestr("中文.txt", data)
        with zipfile.ZipFile(root / "数据.zip") as bundle:
            assert bundle.read("中文.txt") == data
        # Exercise -m startup independently of this script's already-imported
        # modules and without development packages on sys.path.
        result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-m", "json.tool"],
                                input='{"中文":42}', text=True, encoding="utf8", capture_output=True, check=True, cwd=root, timeout=20)
        assert json.loads(result.stdout) == {"中文": 42}
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as executor:
        assert list(executor.map(child_job, [2, 3, 4])) == [4, 9, 16]
    print("Bundled stdlib: resources, spawn, encodings, SSL, SQLite, compression and CLI OK")


if __name__ == "__main__":
    check_stdlib()
