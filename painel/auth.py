import base64
from aiohttp import web
import os

def _get_creds():
    return os.getenv("PANEL_USER", "admin"), os.getenv("PANEL_PASS", "troque-isto")

def check_basic_auth(request: web.Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        user_cfg, pass_cfg = _get_creds()
        return (user == user_cfg and pwd == pass_cfg)
    except Exception:
        return False

def require_auth(request: web.Request):
    if not check_basic_auth(request):
        raise web.HTTPUnauthorized(headers={"WWW-Authenticate": 'Basic realm="EVbabel Admin"'})
