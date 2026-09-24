"""Refuse publishing a wheel with missing or stale workbook renderer assets."""
import hashlib
import json
from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py


class CheckedBuild(build_py):
    def run(self):
        root = Path(__file__).parent
        assets = root / "excelmanus/workbook/render_assets"
        try:
            manifest = json.loads((assets / "manifest.json").read_text())
            for name, digest in manifest["outputs"].items():
                assert hashlib.sha256((assets / name).read_bytes()).hexdigest() == digest, name
            for name, digest in manifest["sources"].items():
                source = root / name
                if source.is_file():
                    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
        except (OSError, ValueError, KeyError, AssertionError) as exc:
            raise RuntimeError("Missing/stale preview assets. Run npm --prefix web ci then npm --prefix web run build:preview before building the Python package") from exc
        super().run()


setup(cmdclass={"build_py": CheckedBuild})
