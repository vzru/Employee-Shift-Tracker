"""
PyInstaller entry point (and a convenient dev launcher).

    python run.py                 # start the server + open the browser
    python run.py --reset-admin   # reset the admin password in a terminal

Kept at the repo root so PyInstaller has a top-level script that imports the
``app`` package normally (its modules use package-relative imports).
"""

from app.main import main

if __name__ == "__main__":
    main()
