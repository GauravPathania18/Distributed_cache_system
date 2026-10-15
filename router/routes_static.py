import os

from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from .app import app
from .config import FRONTEND_DIR


if os.path.isdir(FRONTEND_DIR):

    # Registered last on purpose: Starlette matches in registration
    # order, so the /cache, /search, /dashboard and /db routes above
    # always win and the static mount only answers what they do not.
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")

else:

    # A missing frontend folder must not stop the API from booting.
    print(f"[WARN] frontend directory not found: {FRONTEND_DIR}")


@app.get("/")
def root():
    """One command to reach the UI: open the router, land on /ui."""

    return RedirectResponse(url="/ui")
