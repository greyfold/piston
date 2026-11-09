"""L'application du travailleur : `jobsearch travailleur`, dans la machine virtuelle.

Les routes, toutes derrière le jeton partagé :
- GET  /etat                     version, connexion de Claude Code, sources confiées ;
- POST /moteur                   une tâche du moteur (dict de Tache + message) ;
- POST /sources/{nom}/tester     test de route d'une source confiée ;
- POST /sources/{nom}/offres     ses offres, avec les annonces déjà connues du Mac ;
- POST /sources/{nom}/detail     le texte complet d'une annonce ;
- POST /connecteurs/{nom}/tester état d'un connecteur du compte Claude (Gmail, Indeed) ;
- POST /courrier/gmail           le courrier lu dans Gmail.

Le travailleur n'a pas la base : ce qu'il doit savoir du registre (annonces déjà
connues) lui est envoyé, et il ne rend que des données. Rien n'est écrit ici,
sinon les journaux des sessions Claude.
"""

import hmac
from dataclasses import asdict
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import moteur
from ..config import reglages
from ..fingerprint import empreinte
from . import jeton

VERSION = "1"

app = FastAPI(title="Travailleur", docs_url=None, redoc_url=None, openapi_url=None)


def confiees():
    """Les sources que le Mac confie au travailleur (réglage travailleur.sources)."""
    return list((reglages().get("travailleur") or {}).get("sources") or ["indeed"])


@lru_cache
def _sources():
    from ..sources.catalogue import construire
    return {nom: src for nom, src in construire().items() if nom in confiees()}


def _source(nom):
    src = _sources().get(nom)
    if src is None:
        raise HTTPException(404, "source non confiée au travailleur : %s" % nom)
    return src


@app.middleware("http")
async def autoriser(request: Request, suite):
    attendu = jeton.lire(creer=False)
    recu = request.headers.get("authorization", "")
    if not attendu or not hmac.compare_digest(recu.encode(), ("Bearer " + attendu).encode()):
        return JSONResponse({"erreur": "jeton refusé"}, status_code=401)
    return await suite(request)


def _refus(e, **k):
    return JSONResponse(dict({"erreur": str(e)}, **k), status_code=422)


@app.get("/etat")
def etat():
    return {"version": VERSION, "moteur": moteur.etat(), "sources": confiees()}


@app.post("/moteur")
async def executer(request: Request):
    d = await request.json()
    t = moteur.Tache.depuis_dict(d["tache"])
    try:
        r = moteur.executer(t, d.get("message") or "")
    except moteur.MoteurImpossible as e:
        return _refus(e, bloquant=e.bloquant)
    return {"texte": r.texte, "brut": r.brut, "usage": r.usage, "cout_usd": r.cout_usd, "session": r.session}


@app.post("/connecteurs/{nom}/tester")
def tester_connecteur(nom: str):
    from .. import connecteurs
    if nom not in connecteurs.CATALOGUE or connecteurs.CATALOGUE[nom]["lieu"] != "compte":
        raise HTTPException(404, "connecteur inconnu dans la machine virtuelle : %s" % nom)
    ok, message = connecteurs.etat_connecteur(connecteurs.CATALOGUE[nom])
    return {"ok": ok, "message": message}


@app.post("/courrier/gmail")
async def courrier_gmail(request: Request):
    from .. import gmail
    d = await request.json()
    try:
        return gmail.lire(d.get("mots") or [], d["depuis"], int(d.get("limite") or 40),
                          longueur=int(d.get("longueur") or 1500))
    except Exception as e:
        return _refus(e)


@app.post("/sources/{nom}/tester")
def tester(nom: str):
    try:
        return {"n": _source(nom).tester()}
    except HTTPException:
        raise
    except Exception as e:
        return _refus(e)


def _a_lire(connues):
    """Le filtre du passage, sans la base : inconnue du Mac et pas écartée sur l'intitulé."""
    from .. import prefilter
    from ..ingest import cle_source
    cles, emps = set(connues.get("cles") or []), set(connues.get("empreintes") or [])

    def a_lire(a):
        if cle_source(a) in cles or empreinte(a.entreprise, a.titre) in emps:
            return False
        return not prefilter.evaluer({"titre": a.titre, "entreprise": a.entreprise, "requetes": a.requetes})
    return a_lire


@app.post("/sources/{nom}/offres")
async def offres(nom: str, request: Request):
    d = await request.json()
    src = _source(nom)
    kw = {}
    if d.get("connues") is not None and getattr(src, "detail_en_session", False):
        kw["a_lire"] = _a_lire(d["connues"])
    if d.get("jours") is not None and getattr(src, "selon_fenetre", False):
        kw["jours"] = d["jours"]
    try:
        return [asdict(a) for a in src.offres(**kw)]
    except Exception as e:
        return _refus(e)


@app.post("/sources/{nom}/detail")
async def detail(nom: str, request: Request):
    from ..sources.base import Annonce
    a = Annonce(**await request.json())
    try:
        return asdict(_source(nom).detail(a))
    except Exception as e:
        return _refus(e)
