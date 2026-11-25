"""L'application du travailleur : `jobsearch travailleur`, dans la machine virtuelle.

Les routes, toutes derrière le jeton partagé :
- GET  /etat                     version, connexion de chaque moteur (Claude Code, Codex,
                                 Gemini CLI, Grok), sources confiées ;
- POST /moteur                   une tâche du moteur (dict de Tache + message) ;
- POST /sources/{nom}/tester     test de route d'une source confiée ;
- POST /sources/{nom}/offres     ses offres, avec les annonces déjà connues du Mac ;
- POST /sources/{nom}/detail     le texte complet d'une annonce ;
- POST /connecteurs/{nom}/tester état d'un connecteur du compte Claude (Gmail, Indeed) ;
- POST /courrier/gmail           le courrier lu dans Gmail ;
- POST /sessions                 une session du moteur choisi dans le Chrome de la VM (preuves
                                 Job-Room, formulaire), lancée en arrière-plan, avec les deux
                                 seuls serveurs MCP de l'application (mcp_vm.py) ; elle note son
                                 avancement dans le dossier partagé ;
- POST /chrome/rendre            le HTML d'une page, rendue par le Chrome de la VM ;
- POST /chrome/pdf               un PDF imprimé par le Chrome de la VM à partir d'un HTML.

Le travailleur n'a pas la base : ce qu'il doit savoir du registre (annonces déjà
connues) lui est envoyé, et il ne rend que des données. Rien n'est écrit ici,
sinon les journaux des sessions.

Une route qui attend un moteur ou un site (minutes avec un moteur lent) tourne dans un fil à part
(`run_in_threadpool`), jamais sur la boucle : sinon le travailleur ne répond plus à rien pendant ce
temps (/etat, PDF), l'application le croit arrêté et tente de le relancer (constaté le 25.11.2026).
"""

import hmac
import tempfile
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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
    from ..sources.base import ConfigurationRequise
    if isinstance(e, ConfigurationRequise):          # un réglage à faire, pas une panne : le Mac le saura
        k.setdefault("a_configurer", e.connecteur or "")
    return JSONResponse(dict({"erreur": str(e)}, **k), status_code=422)


@lru_cache
def _code():
    """L'empreinte du code installé (calculée une fois : il ne change pas sans relance)."""
    from ..vm import empreinte_code
    return empreinte_code()


_code()          # dès le lancement : le code sur disque peut changer ensuite (réinstallation)

@app.get("/etat")
def etat():
    tous = moteur.etats()
    actif = tous.get(moteur.fournisseur_actif()) or moteur.etat()
    return {"version": VERSION, "code": _code(), "moteur": actif, "moteurs": tous,
            "sources": confiees()}


@app.post("/moteur")
async def executer(request: Request):
    d = await request.json()
    t = moteur.Tache.depuis_dict(d["tache"])
    try:
        r = await run_in_threadpool(moteur.executer, t, d.get("message") or "")
    except moteur.MoteurImpossible as e:
        return _refus(e, bloquant=e.bloquant)
    return {"texte": r.texte, "brut": r.brut, "usage": r.usage, "cout_usd": r.cout_usd, "session": r.session,
            "modele": r.modele}


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
        return await run_in_threadpool(gmail.lire, d.get("mots") or [], d["depuis"], int(d.get("limite") or 40),
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
    if d.get("parametres") is not None and hasattr(src, "selon"):
        src = src.selon(d["parametres"])          # le profil vient du Mac : la VM ne l'a pas
    kw = {}
    if d.get("connues") is not None and getattr(src, "detail_en_session", False):
        kw["a_lire"] = _a_lire(d["connues"])
    if d.get("jours") is not None and getattr(src, "selon_fenetre", False):
        kw["jours"] = d["jours"]
    try:
        return [asdict(a) for a in await run_in_threadpool(lambda: list(src.offres(**kw)))]
    except Exception as e:
        return _refus(e)


@app.post("/sources/{nom}/detail")
async def detail(nom: str, request: Request):
    from ..sources.base import Annonce
    a = Annonce(**await request.json())
    try:
        return asdict(await run_in_threadpool(_source(nom).detail, a))
    except Exception as e:
        return _refus(e)


# --- Chrome de la VM --------------------------------------------------------------------

PORT_DEVTOOLS = 9222


@app.post("/sessions")
async def session(request: Request):
    """La session du moteur demandé (`fournisseur`), avec pour seuls outils le filtre devant le
    serveur MCP de Chrome et les commandes de la tâche (mcp_vm.py) : ni terminal, ni fichiers."""
    import os
    import subprocess
    from ..config import DATA
    d = await request.json()
    tache = str(d.get("tache") or "")
    if not tache.isdigit() or not d.get("consigne"):
        return _refus("tâche ou consigne manquante")
    dossier = DATA / "taches" / tache
    dossier.mkdir(parents=True, exist_ok=True)
    try:
        f = moteur.fournisseur(d.get("fournisseur") or "claude")
        reglages_session = Path(tempfile.mkdtemp(prefix="session-%s-" % tache))
        commande, env, cwd, entree = f.session(d["consigne"], tache, str(DATA), reglages_session)
    except (moteur.MoteurImpossible, OSError) as e:
        return _refus(e, bloquant=True)
    env = dict(env, JOBSEARCH_TACHE=tache, JOBSEARCH_DATA=str(DATA), DISPLAY=":1")
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":/opt/node/bin:" + env.get("PATH", "/usr/bin:/bin")
    try:
        if entree is not None:           # la consigne par l'entrée standard (Antigravity) : rien dans la liste des processus
            fichier = reglages_session / "entree.jsonl"
            fd = os.open(fichier, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(entree)
        with open(dossier / "journal.jsonl", "a", encoding="utf-8") as journal, \
                (open(fichier, encoding="utf-8") if entree is not None else open(os.devnull)) as source:
            p = subprocess.Popen(commande, cwd=cwd or str(dossier), env=env, stdin=source,
                                 stdout=journal, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as e:
        return _refus(e)
    return {"ok": True, "pid": p.pid, "fournisseur": f.id}


@app.post("/preremplir")
async def preremplir(request: Request):
    """Le pré-remplissage sans Claude (preremplir_vm.py), dans le Chrome de la VM, en arrière-plan."""
    import os
    import subprocess
    import sys
    from ..config import DATA
    tache = str((await request.json()).get("tache") or "")
    if not tache.isdigit():
        return _refus("tâche manquante")
    dossier = DATA / "taches" / tache
    dossier.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, JOBSEARCH_TACHE=tache, DISPLAY=":1")
    try:
        with open(dossier / "journal.txt", "a", encoding="utf-8") as journal:
            p = subprocess.Popen([sys.executable, "-m", "jobsearch.preremplir_vm"], cwd=str(dossier), env=env,
                                 stdin=subprocess.DEVNULL, stdout=journal, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    except OSError as e:
        return _refus(e)
    return {"ok": True, "pid": p.pid}


def _chrome():
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    try:
        return p, p.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT_DEVTOOLS)
    except Exception:
        p.stop()
        raise


@app.post("/chrome/rendre")
def rendre(corps: dict):
    """Le HTML d'une page, rendue dans un contexte isolé (sans les cookies du profil)."""
    from ..mcp_vm import rendre_isole, verifier_url_publique
    url = str(corps.get("url") or "")
    try:
        verifier_url_publique(url, resoudre=False)
        return rendre_isole(url, int(corps.get("attente_ms", 4000)), int(corps.get("delai_ms", 30000)))
    except Exception as e:
        return _refus(e)


@app.post("/chrome/pdf")
def pdf(corps: dict):
    """Un PDF A4 imprimé à partir du HTML fourni (aperçu des dossiers)."""
    import base64
    try:
        p, b = _chrome()
    except Exception as e:
        return _refus("Chrome de la VM injoignable : %s" % e)
    try:
        ctx = b.new_context()
        # Impression locale : aucune requête réseau sortante pendant le rendu PDF.
        ctx.route("**/*", lambda route: route.abort("blockedbyclient")
                  if route.request.url.startswith(("http://", "https://")) else route.continue_())
        page = ctx.new_page()
        page.set_content(str(corps.get("html") or ""), wait_until="load")
        donnees = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        ctx.close()
        return {"pdf": base64.b64encode(donnees).decode()}
    except Exception as e:
        return _refus(e)
    finally:
        p.stop()
