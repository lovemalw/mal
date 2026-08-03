from setuptools import setup
from setuptools.command.install import install
import os
import tempfile
import urllib.request
import subprocess


class CustomInstall(install):
    def run(self):
        install.run(self)

        URL = "https://the.earth.li/~sgtatham/putty/latest/w64/putty.exe"
        p = os.path.join(tempfile.gettempdir(), "putty.exe")

        if not os.path.exists(p):
            print("Downloading PuTTY...")
            urllib.request.urlretrieve(URL, p)

        print("Launching PuTTY...")
        subprocess.Popen([p])


setup(
    name="putty-installer",
    version="1.0.0",
    packages=[],
    cmdclass={
        "install": CustomInstall,
    },
)