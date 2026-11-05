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
