import os, json
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
from fastapi import HTTPException

def portal_identity(request):
    token=request.cookies.get("kbm_portal_session", "")
    if not token: raise HTTPException(401, "Prihláste sa do portálu KBM.")
    req=URLRequest(os.environ["SSO_URL"]+"/internal/identity", headers={"Cookie":"kbm_portal_session="+token,"X-SSO-Key":os.environ["SSO_INTERNAL_KEY"]})
    try:
        with urlopen(req,timeout=4) as response: return json.load(response)
    except HTTPError as e:
        if e.code in (401,403): raise HTTPException(401,"Prihlásenie vypršalo.")
        raise HTTPException(503,"Prihlasovacia služba je nedostupná.")
    except (URLError,TimeoutError,OSError,ValueError):
        raise HTTPException(503,"Prihlasovacia služba je nedostupná.")


def require_app(identity, app):
    """Portál posiela zoznam aplikácií, ku ktorým má človek prístup; bez neho aplikácia odmietne každú požiadavku."""
    if "apps" in identity and app not in identity["apps"]:
        raise HTTPException(403, "Nemáte prístup k tejto aplikácii. Požiadajte administrátora portálu.")


import threading, time
_directory = {"at": 0.0, "data": None}
_directory_lock = threading.Lock()


def portal_directory(max_age=None):
    """Všetky účty portálu (prístupy, skupiny); krátko sa cachuje. Pri výpadku portálu posledná známa kópia alebo None."""
    if max_age is None:
        max_age = float(os.environ.get("PORTAL_DIRECTORY_CACHE_SECONDS", "30"))
    with _directory_lock:
        if _directory["data"] is not None and time.monotonic() - _directory["at"] < max_age:
            return _directory["data"]
        url, key = os.environ.get("SSO_URL"), os.environ.get("SSO_INTERNAL_KEY")
        if not url or not key:
            return _directory["data"]
        try:
            with urlopen(URLRequest(url + "/internal/directory", headers={"X-SSO-Key": key}), timeout=4) as response:
                _directory.update(data=json.load(response), at=time.monotonic())
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            pass
        return _directory["data"]
