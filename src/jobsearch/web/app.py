"""Le tableau de bord : où la personne tranche et suit, sur le Mac ou le téléphone.

Pages rendues par le serveur, formulaires simples, aucune dépendance JS : un
clic écrit directement dans le registre (actions.appliquer), il n'y a plus
de file d'attente à rapprocher au passage suivant.
"""

import datetime as dt
import json
from pathlib import Path
from urllib.parse import quote

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from .. import moteur as moteur_mod
from .. import actions, ajout, cles, courrier, dossier, doublons, entretiens, evaluation, followups, parcours
from .. import profil as profil_mod
from .. import repertoire as repertoire_mod
from ..formatage import annonce_html
from ..config import CONFIG, DATA, coffre_actif, criteres, neutre, reglages
from ..db import ENVOYEES, PIPELINE, STATUTS, Registre
from .auth import COOKIE, autorise, depuis_le_mac, jeton, meme_origine

ICI = Path(__file__).parent
app = FastAPI(title="Recherche d'emploi", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ICI / "static"), name="static")
tpl = Jinja2Templates(directory=ICI / "templates")

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def date_fr(v, court=False):
    if not v:
        return ""
    try:
        d = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return str(v)
    return "%d %s" % (d.day, MOIS[d.month - 1]) if court else "%d %s %d" % (d.day, MOIS[d.month - 1], d.year)


def jour_fr(v):
    """« mardi 3 nov. », ou « aujourd'hui » / « demain » quand c'est le cas."""
    d = v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])
    n = (d - dt.date.today()).days
    return "aujourd'hui" if n == 0 else "demain" if n == 1 else "%s %s" % (JOURS[d.weekday()], date_fr(d, True))


def il_y_a(v):
    if not v:
        return ""
    n = (dt.date.today() - dt.date.fromisoformat(str(v)[:10])).days
    return "aujourd'hui" if n == 0 else "hier" if n == 1 else "il y a %d j" % n if n > 0 else "dans %d j" % -n


def jours_restants(v):
    if not v:
        return None
    return (dt.date.fromisoformat(str(v)[:10]) - dt.date.today()).days


def priorite(f, note=None):
    """Ordre « à postuler en premier » : délai proche d'abord, puis la note, puis la fraîcheur.
    Un délai dépassé passe en dernier."""
    j = jours_restants(f.get("delai_postulation"))
    pub = -(int((f.get("publiee_le") or f.get("vue_le") or "0")[:10].replace("-", "") or 0))
    if j is not None and j < 0:
        return (3, 0, 0, pub)
    if j is not None and j <= 14:
        return (0, j, -(note or 0), pub)
    return (1 if note is not None else 2, 0, -(note or 0), pub)


def texte_html(t):
    return Markup(md.markdown(t or "", extensions=["sane_lists", "tables"]))


tpl.env.filters.update(date_fr=date_fr, jour_fr=jour_fr, il_y_a=il_y_a, texte_html=texte_html, annonce_html=annonce_html,
                       jours_restants=jours_restants)
tpl.env.globals.update(TYPES=actions.TYPES, STATUTS=STATUTS, libelle_dossier=dossier.libelle)
# La feuille de style porte sa date dans son adresse : un navigateur (ou la vue web de
# l'application Mac) ne garde jamais une ancienne version en cache.
tpl.env.globals["version_css"] = lambda: int(max((ICI / "static" / f).stat().st_mtime for f in ("style.css", "champs.js", "compositeur.js")))


def _decomposer_tel(numero, defaut):
    from ..telephone import decomposer
    return decomposer(numero, defaut)


def _date_suisse(iso):
    """aaaa-mm-jj → jj.mm.aaaa (ce que le champ date affiche)."""
    p = (iso or "").split("-")
    return "%s.%s.%s" % (p[2], p[1], p[0]) if len(p) == 3 else (iso or "")


tpl.env.globals["decomposer_tel"] = _decomposer_tel
tpl.env.filters["date_suisse"] = _date_suisse


def _reg():
    return Registre()


@app.middleware("http")
async def garde(request: Request, call_next):
    if request.url.path.startswith("/static") or request.url.path == "/connexion":
        return await call_next(request)
    if not autorise(request):
        return RedirectResponse("/connexion", status_code=303)
    if request.method == "POST" and not meme_origine(request):
        return HTMLResponse("Origine refusée.", status_code=403)
    return await call_next(request)


def page(request, nom, **ctx):
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("ok", request.query_params.get("ok") == "1")
    ctx["nav"] = request.url.path
    ctx["dans_reglages"] = any(ctx["nav"] == r or ctx["nav"].startswith(r + "/") for r in REGLAGES)
    ctx["raisons"] = criteres().get("raisons") or {}
    ctx["aujourdhui"] = dt.date.today().isoformat()
    ctx["profil_manquants"] = profil_mod.manquants()
    ctx["accueil"] = request.url.path.startswith("/bienvenue") and profil_mod.en_accueil()   # sans menu
    p = profil_mod.charger()
    ctx["premiers_pas_affiches"] = p.get(profil_mod.PREMIERS_PAS) == "affichee"
    ctx["fonctions"] = profil_mod.fonctions(p, ia_connectee() if profil_mod.progressif(p) else True)
    return tpl.TemplateResponse(request, nom, ctx)


# --- Santé du système (bandeau commun) ----------------------------------------


def etat_passage(reg):
    r = reg.cx.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    termine = reg.cx.execute("SELECT MAX(fin) FROM runs WHERE statut IN ('complet','partiel')").fetchone()[0]
    seuil = float((reglages().get("surveillance") or {}).get("alerte_apres_heures", 26))
    age = (dt.datetime.now() - dt.datetime.fromisoformat(termine)).total_seconds() / 3600 if termine else None
    niveau = "ok"
    if r is not None and r["statut"] == "demarre" and (age is None or age <= seuil):
        niveau, texte = "attention", "Passage en cours depuis %s." % r["debut"]
    elif r is None or age is None:
        niveau, texte = "alerte", "Aucun passage de la plateforme pour l'instant."
    elif age > seuil:
        niveau, texte = "alerte", "Dernier passage terminé il y a %d h : la chaîne a raté au moins un matin." % age
    elif r["statut"] == "partiel":
        niveau, texte = "attention", "Dernier passage partiel (%s) : une étape a échoué." % r["fin"]
    elif r["statut"] == "demarre":
        niveau, texte = "attention", "Passage en cours depuis %s." % r["debut"]
    else:
        texte = "Dernier passage complet : %s." % r["fin"]
    return {"niveau": niveau, "texte": texte, "run": dict(r) if r else None}


# --- Pages --------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def accueil(request: Request, tri: str = "urgence"):
    if profil_mod.en_accueil():                    # premier lancement : les avantages, puis l'assistant
        return RedirectResponse("/bienvenue", status_code=303)
    reg = _reg()
    try:
        ordre = {"A": 0, "B": 1, "nouvelle": 2}
        evals = evaluation.toutes(reg)
        note = lambda f: evals[f["empreinte"]]["note_globale"] if f["empreinte"] in evals else None
        fraicheur = lambda f: -(int((f.get("publiee_le") or f.get("vue_le") or "0")[:10].replace("-", "") or 0))
        cles = {"urgence": lambda f: priorite(f, note(f)),
                "note": lambda f: (-(note(f) if note(f) is not None else -1), fraicheur(f)),
                "recence": fraicheur,
                "statut": lambda f: (ordre[f["statut"]], -(note(f) or -1), fraicheur(f))}
        tri = tri if tri in cles else "urgence"
        a_trancher = sorted(reg.liste(["A", "B", "nouvelle"]), key=cles[tri])
        limite = (dt.date.today() - dt.timedelta(days=14)).isoformat()
        limites = [f for f in reg.liste(["C"]) if f.get("limite") and (f.get("vue_le") or "") >= limite]
        dossiers = {f["empreinte"]: infos_dossier(reg, f) for f in reg.liste(["retenue"])}
        from .. import agent_claude
        taches = {f["empreinte"]: agent_claude.vue(agent_claude.derniere(reg, "formulaire", f["empreinte"]))
                  for f in reg.liste(["retenue"])}
        tache_jobroom = agent_claude.vue(agent_claude.derniere(reg, "jobroom"))
        retenues = sorted(reg.liste(["retenue"]), key=lambda f: priorite(f, note(f)))
        p = profil_mod.charger()
        premiers = (profil_mod.premiers_pas(p, ia_connectee=bool(etat_claude().get("connecte")))
                    if p.get(profil_mod.PREMIERS_PAS) == "affichee" else [])
        return page(request, "accueil.html", etat=etat_passage(reg), a_trancher=a_trancher, evals=evals, tri=tri,
                    premiers_pas=premiers,
                    taches=taches, tache_jobroom=tache_jobroom, etats_taches=agent_claude.ETATS,
                    dossiers=dossiers, courrier_a_trancher=courrier.pour_le_tableau(reg, reg.liste(["candidature", "entretien", "offre"])),
                    courrier_recent=courrier.recents(reg), ouvertes=reg.liste(["candidature", "entretien", "offre"]),
                    retenues=retenues, relances=followups.relances(reg),
                    jobroom=followups.jobroom(reg), limites=limites,
                    sans_reponse=followups.sans_reponse(reg), stats=reg.stats(),
                    entretiens=entretiens.a_venir(reg),
                    rythme=followups.rythme(reg))
    finally:
        reg.fermer()


@app.get("/annonce/{emp}", response_class=HTMLResponse)
def annonce(request: Request, emp: str, onglet: str = "annonce"):
    reg = _reg()
    try:
        plein = reg.resoudre(emp) or emp
        f = reg.get(plein)
        if not f:
            return HTMLResponse("Annonce introuvable.", status_code=404)
        hist = [dict(r) for r in reg.cx.execute(
            "SELECT * FROM actions WHERE empreinte=? ORDER BY id DESC LIMIT 20", (plein,))]
        jr_limite = followups.echeance_jobroom(f["postulee_le"]) if f.get("postulee_le") else None
        ent = entretiens.lire(plein)
        return page(request, "annonce.html", f=f, journal=reg.journal(plein), historique=hist,
                    ev=evaluation.lire(reg, plein), dos=infos_dossier(reg, f),
                    jr_limite=jr_limite, envoyee=f["statut"] in ENVOYEES, ent=ent,
                    evaluation_en_cours=plein in ajout.EN_COURS,
                    onglet="entretien" if ent and onglet == "entretien" else "annonce")
    finally:
        reg.fermer()


@app.post("/annonce/{emp}/original")
def annonce_original(emp: str):
    from .. import originaux
    from ..pipeline import Passage
    reg = _reg()
    try:
        f = reg.get(emp)
        if not f:
            return HTMLResponse("Annonce introuvable.", status_code=404)
        ok = originaux.recharger(reg, f, Passage(reg).sources())
        msg = "Texte original rechargé." if ok else "Annonce originale introuvable (retirée des plateformes ?)."
        return RedirectResponse("/annonce/%s?msg=%s&ok=%d" % (emp, quote(msg), int(ok)), status_code=303)
    finally:
        reg.fermer()


@app.post("/annonce/{emp}/evaluer")
def annonce_evaluer(emp: str):
    reg = _reg()
    try:
        f = reg.get(emp)
        if not f:
            return HTMLResponse("Annonce introuvable.", status_code=404)
        try:
            bilan = evaluation.evaluer_lot(reg, [f])
            ok = bilan["evaluees"] == 1
            msg = "Annonce évaluée." if ok else "Évaluation impossible : %s" % "; ".join(bilan["erreurs"])
        except (evaluation.EvaluationImpossible, FileNotFoundError) as e:
            ok, msg = False, "Évaluation impossible : %s" % e
        return RedirectResponse("/annonce/%s?msg=%s&ok=%d#evaluation" % (emp, quote(msg), int(ok)), status_code=303)
    finally:
        reg.fermer()


@app.post("/courrier/{ident}")
def courrier_trancher(ident: str, decision: str = Form("ignore"), empreinte: str = Form(""), classe: str = Form("")):
    reg = _reg()
    try:
        courrier.clore(reg, ident, "applique" if decision == "applique" else "ignore", empreinte or None, classe or None)
        msg, ok = ("Courrier appliqué." if decision == "applique" else "Courrier ignoré."), True
    except actions.ActionRefusee as e:
        msg, ok = "Refusé : %s" % e, False
    finally:
        reg.fermer()
    return RedirectResponse("/?msg=%s&ok=%d#courrier" % (quote(msg), int(ok)), status_code=303)


def infos_dossier(reg, f):
    """Le dossier d'une annonce : produit par la plateforme (data/dossiers), sinon celui de
    l'ancienne chaîne dans le coffre (Candidatures/, lu sans jamais y écrire). None sinon."""
    d = dossier.etat(reg, f["empreinte"])
    if d:
        return dict(d, origine="plateforme")
    if f.get("dossier") and coffre_actif():
        from ..importer import CANDIDATURES
        chemin = coffre_actif() / CANDIDATURES / f["dossier"]
        if chemin.is_dir():
            return {"empreinte": f["empreinte"], "etat": "pret", "chemin": str(chemin), "couverture": None,
                    "controles": [], "erreur": None, "debut": None, "origine": "coffre"}
    return None


@app.get("/dossier/{emp}", response_class=HTMLResponse)
def dossier_page(request: Request, emp: str):
    reg = _reg()
    try:
        f = reg.get(emp)
        d = infos_dossier(reg, f) if f else None
        if not f or not d:
            return HTMLResponse("Dossier introuvable.", status_code=404)
        chemin = Path(d["chemin"])
        from ..lettre import date_cible
        if d.get("origine") == "plateforme":
            dossier.mettre_a_date(reg, emp)
        date_lettre = date_cible(f)
        pieces = []
        for nom in ("Analyse.md", "CV.md", "Lettre.md", "Envoi.md"):
            if (chemin / nom).exists():
                pieces.append((nom, (chemin / nom).read_text(encoding="utf-8")))
        fichiers = sorted(p.name for p in chemin.iterdir()
                          if p.suffix in (".docx", ".pdf") and not p.name.startswith(("~", "."))) if chemin.exists() else []
        # Document affiché : celui demandé, sinon le CV (PDF de préférence, plus fidèle).
        choix = request.query_params.get("doc")
        if choix not in fichiers:
            cvs = [n for n in fichiers if "-CV-" in n]
            choix = next((n for n in cvs if n.endswith(".pdf")), cvs[0] if cvs else (fichiers[0] if fichiers else None))
        motscles = None
        if (chemin / "motscles.json").exists():
            motscles = json.loads((chemin / "motscles.json").read_text(encoding="utf-8"))
        elif (chemin / "CV.md").exists() and (chemin / "Annonce.md").exists():
            from ..motscles import analyser         # dossier du coffre : mesuré à la volée, rien n'est écrit
            motscles = analyser((chemin / "CV.md").read_text(encoding="utf-8"),
                                (chemin / "Annonce.md").read_text(encoding="utf-8"), f.get("entreprise"))
        return page(request, "dossier.html", f=f, d=d, pieces=pieces, fichiers=fichiers, apercu=choix,
                    motscles=motscles, date_lettre=date_lettre, envoyee=f["statut"] in ENVOYEES,
                    pdf_lettre_coffre=d.get("origine") == "coffre" and any(
                        n.endswith(".pdf") and _est_lettre(n) for n in fichiers))
    finally:
        reg.fermer()


@app.get("/dossier/{emp}/fichier/{nom}")
def dossier_fichier(emp: str, nom: str):
    from fastapi.responses import FileResponse
    reg = _reg()
    try:
        f = reg.get(emp)
        d = infos_dossier(reg, f) if f else None
    finally:
        reg.fermer()
    if not d or "/" in nom or nom.startswith(".") or not nom.endswith((".docx", ".pdf", ".md")):
        return HTMLResponse("Fichier introuvable.", status_code=404)
    chemin = _document(f, d, nom)
    if not chemin:
        return HTMLResponse("Fichier introuvable.", status_code=404)
    return FileResponse(chemin, filename=nom)


def _est_lettre(nom):
    return any(m in nom for m in ("Cover-Letter", "Lettre", "Letter"))


def _document(f, d, nom):
    """Le fichier à servir. Une lettre est toujours à la bonne date : celle du jour, ou la
    date d'envoi une fois la candidature partie (copie datée pour un dossier du coffre)."""
    chemin = Path(d["chemin"]) / nom
    if not chemin.is_file():
        return None
    if _est_lettre(nom) and nom.endswith(".docx"):
        if d.get("origine") == "coffre":
            return dossier.lettre_coffre_datee(f, d["chemin"], nom)
        reg = _reg()
        try:
            dossier.mettre_a_date(reg, f["empreinte"])
        finally:
            reg.fermer()
    return chemin


@app.get("/dossier/{emp}/apercu/{nom}")
def dossier_apercu(emp: str, nom: str):
    """Aperçu d'un document : PDF tel quel, Word converti en page A4."""
    from fastapi.responses import FileResponse
    from .. import apercu
    reg = _reg()
    try:
        f = reg.get(emp)
        d = infos_dossier(reg, f) if f else None
    finally:
        reg.fermer()
    if not d or "/" in nom or nom.startswith(".") or not nom.endswith((".docx", ".pdf")):
        return HTMLResponse("Document introuvable.", status_code=404)
    chemin = _document(f, d, nom)
    if not chemin:
        return HTMLResponse("Document introuvable.", status_code=404)
    if nom.endswith(".pdf"):
        return FileResponse(chemin, media_type="application/pdf", filename=nom, content_disposition_type="inline")
    try:
        return HTMLResponse(apercu.docx_en_html(chemin, f.get("langue")))
    except Exception as e:
        return HTMLResponse("Aperçu impossible : %s" % type(e).__name__, status_code=500)


@app.post("/dossier/{emp}/mots-cles")
async def dossier_mots_cles(request: Request, emp: str):
    form = await request.form()
    termes = form.getlist("termes")
    reg = _reg()
    try:
        f = reg.get(emp)
        d = infos_dossier(reg, f) if f else None
    finally:
        reg.fermer()
    if not d or d.get("origine") != "plateforme":
        msg, ok = "Ce dossier vient de l'ancienne chaîne (coffre, lecture seule) : à réviser dans Obsidian.", False
    elif not termes:
        msg, ok = "Coche au moins un mot-clé.", False
    else:
        dossier.reviser_en_tache(emp, termes, form.get("precision") or "")
        msg, ok = "CV en révision avec %d mot(s)-clé(s) (une à deux minutes)." % len(termes), True
    return RedirectResponse("/dossier/%s?msg=%s&ok=%d#mots-cles" % (emp, quote(msg), int(ok)), status_code=303)


@app.post("/annonce/{emp}/preremplir")
def annonce_preremplir(emp: str, cible: str = Form("employeur"), retour: str = Form("/")):
    from .. import preremplissage
    if not retour.startswith("/") or retour.startswith("//"):
        retour = "/"
    reg = _reg()
    try:
        if cible == "jobroom":
            preremplissage.jobroom(reg, emp)          # vérifications avant d'ouvrir quoi que ce soit
        else:
            preremplissage.employeur(reg, emp)
        preremplissage.lancer_en_arriere_plan(emp, cible == "jobroom")
        msg, ok = ("Chrome s'ouvre sur %s : relis, puis %s toi-même." % (
            "Job-Room" if cible == "jobroom" else "le formulaire",
            "sauvegarde" if cible == "jobroom" else "envoie")), True
    except preremplissage.PreremplissageImpossible as e:
        msg, ok = "Pré-remplissage impossible : %s" % e, False
    finally:
        reg.fermer()
    base, _, ancre = retour.partition("#")
    return RedirectResponse("%s%smsg=%s&ok=%d%s" % (base, "&" if "?" in base else "?", quote(msg), int(ok),
                                                     "#" + ancre if ancre else ""), status_code=303)


@app.post("/annonce/{emp}/claude")
def annonce_claude(emp: str, type_: str = Form("formulaire", alias="type"), retour: str = Form("/")):
    from .. import agent_claude
    if not retour.startswith("/") or retour.startswith("//"):
        retour = "/"
    reg = _reg()
    try:
        agent_claude.lancer(reg, type_, emp if type_ == "formulaire" else None)
        msg, ok = ("Claude remplit le formulaire dans ton Chrome (rien n'est envoyé sans toi)." if type_ == "formulaire"
                   else "Claude saisit les preuves Job-Room dans ton Chrome."), True
    except agent_claude.LancementImpossible as e:
        msg, ok = "Claude n'a pas pu démarrer : %s" % e, False
    finally:
        reg.fermer()
    base, _, ancre = retour.partition("#")
    return RedirectResponse("%s%smsg=%s&ok=%d%s" % (base, "&" if "?" in base else "?", quote(msg), int(ok),
                                                     "#" + ancre if ancre else ""), status_code=303)


@app.get("/tache/{tache}/etat")
def tache_etat(tache: int):
    """Avancement d'une session Claude, lu par la page tant que le bouton est bloqué."""
    from .. import agent_claude
    reg = _reg()
    try:
        t = agent_claude.vue(agent_claude.lire(reg, tache))
    finally:
        reg.fermer()
    if not t:
        return JSONResponse({"etat": None}, status_code=404)
    return {"etat": t["etat"], "libelle": t["libelle"], "active": t["active"]}


@app.post("/dossier/{emp}/refaire")
def dossier_refaire(emp: str):
    try:
        dossier.produire_en_tache(emp)
        msg, ok = "Dossier relancé (quelques minutes, notification à la fin).", True
    except evaluation.EvaluationImpossible as e:
        msg, ok = "Dossier impossible : %s" % e, False
    return RedirectResponse("/dossier/%s?msg=%s&ok=%d" % (emp, quote(msg), int(ok)), status_code=303)


@app.get("/dossier/{emp}/etat")
def dossier_etat(emp: str):
    """Avancement du dossier, lu par la page toutes les quelques secondes pendant la préparation."""
    reg = _reg()
    try:
        d = dossier.etat(reg, emp)
    finally:
        reg.fermer()
    if not d:
        return JSONResponse({"etat": None}, status_code=404)
    return {"etat": d["etat"], "etape": d.get("etape"), "libelle": dossier.libelle(d)}


# --- Anciennes adresses (avant le rangement de la navigation) ---------------------
# Signets, notifications et liens déjà envoyés : la page suit, la recherche aussi.

def _deplacee(nouvelle):
    def rediriger(request: Request):
        q = request.url.query
        return RedirectResponse(nouvelle + ("?" + q if q else ""), status_code=301)
    return rediriger


app.get("/pipeline")(_deplacee("/candidatures"))
app.get("/registre")(_deplacee("/offres"))


@app.get("/candidatures", response_class=HTMLResponse)
def pipeline(request: Request):
    reg = _reg()
    try:
        en_cours = reg.liste(PIPELINE, ordre="echeance IS NULL, echeance, entreprise")
        dossiers = {f["empreinte"]: infos_dossier(reg, f) for f in en_cours}
        refus = reg.liste(["refus"], ordre="reponse_le DESC, entreprise")
        return page(request, "pipeline.html", en_cours=en_cours, refus=refus,
                    sans_reponse=followups.sans_reponse(reg), dossiers=dossiers,
                    a_classer=followups.relances(reg)["a_classer"])
    finally:
        reg.fermer()


@app.get("/offres", response_class=HTMLResponse)
def registre(request: Request, statut: str = "", q: str = "", source: str = ""):
    reg = _reg()
    try:
        fiches = reg.liste([statut] if statut in STATUTS else None)
        if q:
            from ..prefilter import plier
            n = plier(q)
            fiches = [f for f in fiches if n in plier(f.get("entreprise")) or n in plier(f.get("titre"))]
        if source:
            fiches = [f for f in fiches if source in (f.get("sources") or [])]
        return page(request, "registre.html", fiches=fiches[:400], total=len(fiches),
                    statut=statut, q=q, source=source, stats=reg.stats())
    finally:
        reg.fermer()


@app.get("/offres/ajouter", response_class=HTMLResponse)
def offres_ajouter(request: Request):
    return page(request, "ajout.html", saisi={})


@app.post("/offres/ajouter", response_class=HTMLResponse)
def offres_ajouter_enregistrer(request: Request, url: str = Form(""), texte: str = Form(""), entreprise: str = Form(""),
                               titre: str = Form(""), lieu: str = Form(""), evaluer: str = Form("")):
    """Une annonce trouvée ailleurs : ingérée sans doublon, notée en arrière-plan, à trancher."""
    saisi = {"url": url, "texte": texte, "entreprise": entreprise, "titre": titre, "lieu": lieu,
             "evaluer": evaluer or "0"}
    reg = _reg()
    try:
        r = ajout.ajouter(reg, url, texte, entreprise, titre, lieu)
        msg = r["message"]
        if evaluer and ajout.a_evaluer(reg, r["empreinte"]):
            ajout.evaluer_en_tache(r["empreinte"])
            msg += " Évaluation en cours (une à deux minutes)."
    except ajout.AjoutRefuse as e:
        return page(request, "ajout.html", saisi=saisi, msg="Refusé : %s." % e, ok=False)
    finally:
        reg.fermer()
    return RedirectResponse("/annonce/%s?msg=%s&ok=1#evaluation" % (r["empreinte"], quote(msg)), status_code=303)


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request, essai=None):
    reg = _reg()
    try:
        repertoire = repertoire_mod.lister(reg)
        etats = {r["id"]: dict(r) for r in reg.cx.execute("SELECT * FROM sources")}
        compte = {}
        for f in reg.toutes():
            for s in f.get("sources") or []:
                c = compte.setdefault(s, {"vues": 0, "ab": 0, "go": 0, "envoyees": 0,
                                          "entretiens": 0, "refus": 0})
                c["vues"] += 1
                st = f["statut"]
                c["ab"] += st in ("A", "B")
                c["go"] += st in ("retenue", "candidature", "entretien", "offre", "refus")
                c["envoyees"] += st in ENVOYEES
                c["entretiens"] += st in ("entretien", "offre")
                c["refus"] += st == "refus"
        lignes = []
        for sid in list(repertoire) + [s for s in compte if s not in repertoire]:
            lignes.append({"id": sid, **(repertoire.get(sid) or {"nom": sid, "acces": "non répertoriée"}),
                           "etat": etats.get(sid), "n": compte.get(sid, {})})
        return page(request, "sources.html", lignes=lignes, acces=repertoire_mod.ACCES, essai=essai,
                    a_verifier=doublons.detecter(reg)["a_verifier"], fusions=doublons.recentes(reg))
    finally:
        reg.fermer()


@app.post("/sources/essayer", response_class=HTMLResponse)
def sources_essayer(request: Request, url: str = Form(""), entreprise: str = Form("")):
    """Interroge la page collée, sans rien enregistrer : ce qu'elle donnerait chaque matin."""
    from ..sources.ajoutees import essayer
    from ..sources.base import Client
    url, entreprise = url.strip(), entreprise.strip()
    if not url.startswith(("http://", "https://")) or not entreprise:
        return _retour("/sources", "Il faut l'adresse de la page (https://…) et le nom de l'employeur.", False)
    return sources(request, essai=essayer(Client(pause=0.3), url, entreprise))


@app.post("/sources/brancher")
def sources_brancher(url: str = Form(""), entreprise: str = Form(""), rendu: str = Form("")):
    from ..sources import ajoutees
    from ..sources.logiciels import reconnaitre
    reg = _reg()
    try:
        if not url.strip().startswith(("http://", "https://")):
            raise ValueError("adresse illisible")
        conn = reconnaitre(url)
        if conn["type"] == "page-ia" and rendu == "1":
            conn["rendu"] = True                     # liste construite en JavaScript (constaté à l'essai)
        sid = ajoutees.brancher(reg, entreprise, url.strip(), conn)
        return _retour("/sources", "Page carrière de %s branchée (%s) : interrogée dès le prochain passage."
                       % (entreprise.strip(), sid), True)
    except (repertoire_mod.SourceRefusee, ValueError, KeyError) as e:
        return _retour("/sources", "Refusé : %s" % e, False)
    finally:
        reg.fermer()


@app.post("/sources/{sid}/{action}")
def sources_activer(sid: str, action: str):
    from ..sources import ajoutees
    if action not in ("activer", "desactiver"):
        return HTMLResponse("Action inconnue.", status_code=404)
    reg = _reg()
    try:
        ajoutees.activer(reg, sid, action == "activer")
        return _retour("/sources", "Source %s." % ("réactivée" if action == "activer" else "désactivée"), True)
    except repertoire_mod.SourceRefusee as e:
        return _retour("/sources", "Refusé : %s" % e, False)
    finally:
        reg.fermer()


@app.post("/doublons/{action}")
def doublons_action(action: str, a: str = Form(""), b: str = Form("")):
    reg = _reg()
    try:
        if action == "fusionner":
            p, s = doublons.ordonner(reg.get(a) or {"empreinte": a}, reg.get(b) or {"empreinte": b})
            doublons.fusionner(reg, p["empreinte"], s["empreinte"], "même offre (confirmée à la main)")
            msg = "Fusionnées : la fiche %s — %s reste, l'autre devient doublon." % (
                reg.get(p["empreinte"])["entreprise"], reg.get(p["empreinte"])["titre"])
        elif action == "differentes":
            doublons.differentes(reg, a, b)
            msg = "Deux offres distinctes : la paire ne sera plus proposée."
        elif action == "separer":
            msg = "Séparée : la fiche reprend le statut « %s »." % doublons.separer(reg, a)
        else:
            return _retour("/sources", "Action inconnue.", False)
        return RedirectResponse("/sources?msg=%s&ok=1#doublons" % quote(msg), status_code=303)
    except ValueError as e:
        return _retour("/sources", "Refusé : %s" % e, False)
    finally:
        reg.fermer()


def _retour(url, msg, ok):
    return RedirectResponse("%s?msg=%s&ok=%d" % (url, quote(msg), int(ok)), status_code=303)


@app.post("/sources/ajouter")
def sources_ajouter(nom: str = Form(""), url: str = Form(""), nature: str = Form(""),
                    acces: str = Form("en session"), remarques: str = Form("")):
    reg = _reg()
    try:
        repertoire_mod.ajouter(reg, nom, url, nature, acces, remarques)
        return _retour("/sources", "Source ajoutée : %s" % nom.strip(), True)
    except repertoire_mod.SourceRefusee as e:
        return _retour("/sources", "Refusé : %s" % e, False)
    finally:
        reg.fermer()


@app.post("/sources/retirer")
def sources_retirer(id: str = Form("")):
    reg = _reg()
    try:
        repertoire_mod.retirer(reg, id)
        return _retour("/sources", "Source retirée du répertoire.", True)
    except repertoire_mod.SourceRefusee as e:
        return _retour("/sources", "Refusé : %s" % e, False)
    finally:
        reg.fermer()


@app.get("/api/etat")
def api_etat():
    """Ce que l'application Mac montre dans la barre des menus : passage, Claude, machine virtuelle."""
    from .. import vm
    from ..travailleur import client as travailleur
    reg = _reg()
    try:
        p = etat_passage(reg)
        a_trancher = len(reg.liste(["A", "B", "nouvelle"]))
    finally:
        reg.fermer()
    distant = travailleur.distant()
    etat_vm = None
    if distant:
        try:
            e = vm.etat()
            etat_vm = {"existe": e["existe"], "statut": e["statut"]}
        except Exception as ex:
            etat_vm = {"existe": False, "statut": None, "erreur": str(ex)}
    c = etat_claude()
    return {"passage": {"niveau": p["niveau"], "texte": p["texte"]}, "a_trancher": a_trancher,
            "claude": {"connecte": bool(c.get("connecte")), "message": c.get("message", "")},
            "mode": "distant" if distant else "local", "vm": etat_vm, "donnees": str(DATA)}


@app.get("/connecteurs", response_class=HTMLResponse)
def connecteurs_page(request: Request):
    from .. import connecteurs
    from ..travailleur import client as travailleur
    return page(request, "connecteurs.html", connecteurs=connecteurs.liste(), roles=connecteurs.ROLES,
                moteur=etat_claude(), distant=travailleur.distant())


_ETAT_CLAUDE = {"t": 0.0, "v": None}


def ia_connectee():
    """Pour l'interface (fonctions débloquées) : l'état de Claude, relu au plus toutes les 15 s."""
    import time
    if _ETAT_CLAUDE["v"] is None or time.monotonic() - _ETAT_CLAUDE["t"] > 15:
        _ETAT_CLAUDE.update(t=time.monotonic(), v=bool(etat_claude().get("connecte")))
    return _ETAT_CLAUDE["v"]


def etat_claude():
    """La connexion de Claude Code là où il tourne : dans la machine virtuelle, ou sur ce Mac."""
    from ..travailleur import client as travailleur
    if not travailleur.distant():
        return moteur_mod.etat()
    try:
        e = dict(travailleur.Distant.depuis_reglages().etat()["moteur"])
        e["message"] += " (machine virtuelle)"
        if not e.get("connecte"):
            e["message"] = "Claude Code n'est pas connecté dans la machine virtuelle : lancer « jobsearch vm connexion »"
        return e
    except Exception as ex:
        return {"installe": False, "connecte": False, "abonnement": None, "message": str(ex)}


@app.get("/ia", response_class=HTMLResponse)
def ia_page(request: Request):
    """Le fournisseur d'IA (abonnement de la personne), à part des connecteurs."""
    from ..travailleur import client as travailleur
    moteur = etat_claude()
    fournisseurs = [dict(f, etat=moteur if f["id"] == "claude" else {}) for f in moteur_mod.FOURNISSEURS]
    return page(request, "ia.html", fournisseurs=fournisseurs, distant=travailleur.distant())


@app.post("/connecteurs/{nom}/{action}")
def connecteurs_action(nom: str, action: str):
    from .. import connecteurs
    if nom not in connecteurs.CATALOGUE or action not in ("activer", "desactiver", "tester"):
        return HTMLResponse("Connecteur ou action inconnus.", status_code=404)
    c = connecteurs.CATALOGUE[nom]
    if action == "tester":
        ok, msg = connecteurs.tester(nom)
        msg = "%s : %s" % (c["nom"], msg)
    else:
        connecteurs.activer(nom, action == "activer")
        ok, msg = True, "%s %s." % (c["nom"], "activé" if action == "activer" else "désactivé")
    return RedirectResponse("/connecteurs?msg=%s&ok=%d#%s" % (quote(msg), int(bool(ok)), nom), status_code=303)


@app.get("/sante", response_class=HTMLResponse)
def sante(request: Request):
    reg = _reg()
    try:
        runs = [dict(r) for r in reg.cx.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30")]
        for r in runs:
            r["etapes"] = [dict(e, compteurs=json.loads(e["compteurs"] or "{}")) for e in reg.cx.execute(
                "SELECT etape, statut, debut, fin, compteurs, erreur FROM run_etapes WHERE run_id=? ORDER BY id",
                (r["id"],))]
        veilles = [r[0] for r in reg.cx.execute("SELECT date FROM veilles ORDER BY date DESC LIMIT 45")]
        jours = []
        for i in range(30):
            d = (dt.date.today() - dt.timedelta(days=i)).isoformat()
            jours.append({"date": d, "fait": d in veilles})
        sauvegardes = sorted((DATA / "sauvegardes").glob("jobsearch-*.db"))
        return page(request, "sante.html", etat=etat_passage(reg), runs=runs, jours=jours,
                    derniere_sauvegarde=sauvegardes[-1].name if sauvegardes else None,
                    nb_sauvegardes=len(sauvegardes), import_le=reg.meta("import_coffre_le"),
                    depuis_le_mac=depuis_le_mac(request), cles=[cles.etat(n) for n in cles.SERVICES],
                    moteur=etat_claude())
    finally:
        reg.fermer()


@app.get("/confidentialite", response_class=HTMLResponse)
def confidentialite_page(request: Request):
    """Ce que l'IA reçoit : l'en-tête du CV tel qu'il part (balises), les lignes masquées du
    profil réel, et la table des balises (visible ici seulement, sur l'ordinateur)."""
    from .. import confidentialite
    try:
        texte = dossier.contexte_fixe()             # méthode et profil du coffre, comme pour un dossier
    except Exception:
        texte = ""
    m = confidentialite.construire(documents=texte)
    vu = m.copie()
    masque = vu.masquer(texte, contexte=True)
    i = profil_mod.identite()
    entete = " · ".join(x for x in (
        " ".join(y for y in (i.get("prenom"), i.get("nom")) if y),
        ", ".join(y for y in (i.get("adresse"), " ".join(z for z in (i.get("npa"), i.get("ville")) if z)) if y),
        i.get("telephone"), i.get("email"), i.get("site"), i.get("linkedin")) if x)
    return page(request, "confidentialite.html", entete=entete, entete_masque=m.masquer(entete),
                extrait=[l for l in masque.splitlines() if confidentialite.BALISE.search(l)][:16],
                balises=[(b, confidentialite.LIBELLES[b], m.valeurs[b]) for b in confidentialite.ORDRE if b in m.valeurs],
                tiers=sum(1 for b in vu.valeurs if "_" in b and b.rsplit("_", 1)[-1].isdigit()),
                profil_present=bool(texte), moteur=etat_claude())


# Pages de réglage : rangées sous « Réglages » dans la barre (elles gardent leur adresse).
REGLAGES = ("/reglages", "/profil", "/interface", "/sources", "/ia", "/connecteurs", "/sante")


@app.get("/reglages", response_class=HTMLResponse)
def reglages_page(request: Request):
    from .. import connecteurs
    reg = _reg()
    try:
        etat = etat_passage(reg)
        nb_sources = len(repertoire_mod.lister(reg))
    finally:
        reg.fermer()
    p = profil_mod.charger()
    cles_ = [cles.etat(n) for n in cles.SERVICES]
    actifs = [c["nom"] for c in connecteurs.liste() if c["actif"]]
    return page(request, "reglages.html", passage=etat, nb_sources=nb_sources, claude=ia_connectee(),
                connecteurs_actifs=actifs, nb_cles=sum(c["presente"] for c in cles_), total_cles=len(cles_),
                options=[(o["titre"], profil_mod.option(o["id"], p)) for o in profil_mod.OPTIONS])


@app.post("/cles/{nom}/{action}")
def cles_action(request: Request, nom: str, action: str, cle: str = Form("")):
    # Une clé ne se saisit que sur le Mac : depuis le téléphone, elle traverserait le
    # réseau local en clair (le tableau de bord n'est pas en HTTPS).
    if not depuis_le_mac(request):
        return RedirectResponse("/sante?msg=%s&ok=0#cles" % quote("Les clés se saisissent sur le Mac uniquement."),
                                status_code=303)
    try:
        if action == "enregistrer":
            msg = cles.enregistrer(nom, cle)
        elif action == "retirer":
            msg = cles.retirer(nom)
        else:
            raise cles.CleRefusee("action inconnue")
        ok = True
    except cles.CleRefusee as e:
        msg, ok = "Refusé : %s" % e, False
    return RedirectResponse("/sante?msg=%s&ok=%d#cles" % (quote(msg), int(ok)), status_code=303)


# --- Profil : l'assistant de configuration ----------------------------------------


def _en_texte(v):
    return "\n".join(map(str, v)) if isinstance(v, list) else "" if v is None else str(v)


def _champs(etapes, p, saisi=None):
    """Les champs d'une ou plusieurs étapes, prêts pour _champs_profil.html."""
    champs = []
    for e in etapes:
        for c in e["champs"]:
            champs.append(dict(c, valeur=saisi.get(c["cle"], "") if saisi is not None
                               else profil_mod.en_saisie(c, _en_texte(profil_mod.effectif(c["cle"], p))),
                               par_defaut=c["cle"] in profil_mod.SUPERPOSES and profil_mod.lire(p, c["cle"]) is None))
    return champs


def _ctx_champs():
    from .. import pays as pays_mod, telephone
    return {"indicatifs": telephone.INDICATIFS, "code_tel_defaut": pays_mod.code_telephone_defaut()}


def _page_profil(request, ident, saisi=None, **ctx):
    p = profil_mod.charger()
    e = profil_mod.etape(ident)
    champs = _champs([e] if e else [], p, saisi)
    ids = [x["id"] for x in profil_mod.etapes()] + ["fin"]
    i = ids.index(ident)
    resume = [dict(x, valeurs=[(c["libelle"], profil_mod.affiche(c, p)) for c in x["champs"]])
              for x in profil_mod.etapes()] if ident == "fin" else []
    coffre = saisi.get("coffre") if saisi is not None and ident == "coffre" else profil_mod.effectif("coffre", p)
    for k, v in _ctx_champs().items():
        ctx.setdefault(k, v)
    return page(request, "profil.html", etapes=profil_mod.etapes(), etape=e, ident=ident, champs=champs,
                precedente=ids[i - 1] if i else None, resume=resume, regles=profil_mod.regles_au_choix() if e else [],
                documents=profil_mod.documents_coffre(coffre) if ident in ("coffre", "fin") and not neutre() else [],
                cles=[cles.etat(n) for n in cles.SERVICES] if ident == "fin" else [],
                moteur=etat_claude() if ident == "fin" else None,
                manquants=profil_mod.manquants(p), **ctx)


# --- Accueil (spec Nexus « 03 Onboarding ») : IA, recherche, premières offres ----------

# La recherche d'abord, l'IA ensuite : on ne commence pas par confier ses informations à une IA.
ORDRE_ACCUEIL = ["recherche", "sources", "ia", "offres"]


def _recherche_faite(p=None):
    p = profil_mod.charger() if p is None else p
    return bool(profil_mod.lire(p, "recherche.requetes")) and bool(profil_mod.lire(p, "recherche.cantons"))


def _sources_faites(p=None):
    p = profil_mod.charger() if p is None else p
    return profil_mod.lire(p, "sources.socle") is not None


def _page_accueil(request, ecran, **ctx):
    """Les trois étapes, toujours montrées en entier ; une étape passée sans être faite reste à faire."""
    claude = ctx.pop("claude", None) or etat_claude()
    faites = {"recherche": _recherche_faite() and ecran != "recherche",
              "sources": _sources_faites() and ecran not in ("recherche", "sources"),
              "ia": bool(claude.get("connecte")), "offres": ctx.pop("offres_vues", False)}
    libelles = {"recherche": "Ta recherche", "sources": "Tes sources", "ia": "Ton IA", "offres": "Premières offres"}
    etapes = [{"label": libelles[e], "etat": "faite" if faites[e] else ("en_cours" if e == ecran else "a_faire")}
              for e in ORDRE_ACCUEIL]
    ctx.setdefault("heure_passage", (reglages().get("passage") or {}).get("heure", "6:30"))
    return page(request, "bienvenue.html", ecran=ecran, etapes_accueil=etapes, claude=claude, **ctx)


@app.get("/bienvenue")
def bienvenue():
    if not profil_mod.en_accueil():
        return RedirectResponse("/", status_code=303)
    suite = ("/bienvenue/recherche" if not _recherche_faite() else
             "/bienvenue/sources" if not _sources_faites() else "/bienvenue/ia")
    return RedirectResponse(suite, status_code=303)


@app.get("/bienvenue/ia", response_class=HTMLResponse)
def bienvenue_ia(request: Request):
    if not profil_mod.en_accueil():
        return RedirectResponse("/", status_code=303)
    if not _recherche_faite() or not _sources_faites():
        return RedirectResponse("/bienvenue", status_code=303)
    claude = etat_claude()
    if claude.get("connecte") and request.query_params.get("etape") != "ia":
        return RedirectResponse("/bienvenue/offres", status_code=303)          # étape déjà faite
    return _page_accueil(request, "ia", claude=claude, fournisseurs=moteur_mod.FOURNISSEURS)


@app.get("/bienvenue/recherche", response_class=HTMLResponse)
def bienvenue_recherche(request: Request):
    if not profil_mod.en_accueil():
        return RedirectResponse("/", status_code=303)
    from .. import pays
    p = profil_mod.charger()
    return _page_accueil(request, "recherche", objectifs=profil_mod.OBJECTIFS, objectif=profil_mod.objectif(p),
                         poste=(profil_mod.lire(p, "recherche.requetes") or [""])[0],
                         cantons_choisis=profil_mod.lire(p, "recherche.cantons") or [],
                         cantons=pays.CANTONS, romands=pays.CANTONS_ROMANDS)


@app.post("/bienvenue/recherche", response_class=HTMLResponse)
async def bienvenue_recherche_enregistrer(request: Request):
    from .. import pays, premiere
    form = await request.form()
    try:
        profil_mod.enregistrer_recherche(form.get("objectif"), form.get("poste"), form.getlist("cantons"))
    except profil_mod.ProfilInvalide as e:
        return _page_accueil(request, "recherche", objectifs=profil_mod.OBJECTIFS, objectif=form.get("objectif") or "fixe",
                             poste=form.get("poste") or "", cantons_choisis=form.getlist("cantons"),
                             cantons=pays.CANTONS, romands=pays.CANTONS_ROMANDS,
                             msg="À compléter : %s." % str(e).removeprefix("à choisir : "), ok=False)
    return RedirectResponse("/bienvenue/sources", status_code=303)


@app.get("/bienvenue/sources", response_class=HTMLResponse)
def bienvenue_sources(request: Request):
    from ..sources.catalogue import SOCLE, socle_choisi
    if not profil_mod.en_accueil():
        return RedirectResponse("/", status_code=303)
    if not _recherche_faite():
        return RedirectResponse("/bienvenue/recherche", status_code=303)
    return _page_accueil(request, "sources", socle=SOCLE, choisis=socle_choisi())


@app.post("/bienvenue/sources", response_class=HTMLResponse)
async def bienvenue_sources_enregistrer(request: Request):
    from .. import premiere
    from ..sources.catalogue import SOCLE
    form = await request.form()
    try:
        profil_mod.enregistrer_sources(form.getlist("sources"))
    except profil_mod.ProfilInvalide:
        return _page_accueil(request, "sources", socle=SOCLE, choisis=[], msg="Choisis au moins un site.", ok=False)
    premiere.lancer()                       # sans IA : elle tourne pendant l'étape suivante
    return RedirectResponse("/bienvenue/ia", status_code=303)


@app.get("/bienvenue/offres", response_class=HTMLResponse)
def bienvenue_offres(request: Request):
    from .. import premiere
    if not profil_mod.en_accueil():
        return RedirectResponse("/", status_code=303)
    from ..sources.catalogue import SOCLE, socle_choisi
    p = profil_mod.charger()
    if not _recherche_faite(p) or not _sources_faites(p):
        return RedirectResponse("/bienvenue", status_code=303)
    if premiere.etat()["etat"] == "aucune":
        premiere.lancer()
    e = premiere.etat()
    noms = [s["nom"] for s in SOCLE if s["id"] in socle_choisi()]
    return _page_accueil(request, "offres", premiere=e, offres_vues=e["etat"] == "finie",
                         sites=" et ".join([", ".join(noms[:-1]), noms[-1]]) if len(noms) > 1 else "".join(noms),
                         poste=profil_mod.lire(p, "recherche.requetes")[0],
                         lieux=", ".join(profil_mod.lire(p, "recherche.jobup_lieux") or profil_mod.lire(p, "recherche.cantons")))


@app.get("/bienvenue/offres/etat")
def bienvenue_offres_etat():
    from .. import premiere
    return {"etat": premiere.etat()["etat"]}


@app.post("/bienvenue/offres/relancer")
def bienvenue_offres_relancer():
    from .. import premiere
    premiere.lancer()
    return RedirectResponse("/bienvenue/offres", status_code=303)


@app.post("/bienvenue/terminer")
async def bienvenue_terminer(request: Request):
    form = await request.form()
    suite = form.get("suite") or "/"
    if not suite.startswith("/") or suite.startswith("//"):
        suite = "/"
    profil_mod.terminer_accueil()
    return RedirectResponse(suite, status_code=303)


# --- Connexion à l'IA dans une modale de la page --------------------------------------

@app.post("/ia/connexion/{fournisseur}/demarrer")
def ia_connexion_demarrer(fournisseur: str):
    from .. import connexion_ia
    if fournisseur not in {f["id"] for f in moteur_mod.FOURNISSEURS}:
        return JSONResponse({"etat": "echec", "message": "fournisseur inconnu"}, status_code=404)
    return connexion_ia.demarrer(fournisseur)


@app.get("/ia/connexion/etat")
def ia_connexion_etat():
    from .. import connexion_ia
    e = connexion_ia.etat()
    if e.get("etat") == "connecte":
        _ETAT_CLAUDE["v"] = None                  # l'interface se rouvre tout de suite
    return e


@app.post("/ia/connexion/code")
def ia_connexion_code(code: str = Form("")):
    from .. import connexion_ia
    return {"accepte": connexion_ia.envoyer_code(code), **connexion_ia.etat()}


@app.post("/ia/connexion/annuler")
def ia_connexion_annuler():
    from .. import connexion_ia
    connexion_ia.annuler()
    return {"etat": "annulee"}


@app.get("/interface", response_class=HTMLResponse)
def interface(request: Request):
    p = profil_mod.charger()
    ia = ia_connectee() if profil_mod.progressif(p) else True
    return page(request, "interface.html", options=profil_mod.OPTIONS, deblocages=profil_mod.DEBLOCAGES,
                taches={x[0]: x for x in profil_mod._taches_checklist(p, ia)},
                objectifs_rythme=profil_mod.objectifs_rythme())


@app.post("/objectifs")
async def objectifs_enregistrer(request: Request):
    """Objectifs de postulations par semaine et par mois : chacun facultatif, vide = aucun."""
    form = await request.form()
    try:
        profil_mod.enregistrer_objectifs(form.get("semaine", ""), form.get("mois", ""))
    except profil_mod.ProfilInvalide as e:
        return RedirectResponse("/interface?msg=%s" % quote(str(e)), status_code=303)
    return RedirectResponse("/interface?msg=%s&ok=1" % quote("Objectifs enregistrés."), status_code=303)


@app.post("/interface/{option}")
async def interface_option(request: Request, option: str):
    form = await request.form()
    try:
        profil_mod.poser_option(option, form.get("actif") == "1")
    except profil_mod.ProfilInvalide:
        return RedirectResponse("/interface", status_code=303)
    return RedirectResponse("/interface?msg=%s&ok=1" % quote("Enregistré."), status_code=303)


@app.post("/premiers-pas/masquer")
def premiers_pas_masquer():
    profil_mod.masquer_premiers_pas()
    return RedirectResponse("/", status_code=303)


def _page_tache(request, tache, saisi=None, **ctx):
    t = profil_mod.TACHES[tache]
    p = profil_mod.charger()
    etapes = [profil_mod.etape(e) for e in t["etapes"]]
    pas = profil_mod.premiers_pas(p, ia_connectee=bool(etat_claude().get("connecte"))) if p.get(profil_mod.PREMIERS_PAS) else []
    rang = next((i for i, x in enumerate(pas) if x[0] == tache), None)
    return page(request, "premiers_pas.html", tache=tache, t=t, champs=_champs(etapes, p, saisi),
                regles=profil_mod.regles_au_choix(), rang=rang, total=len(pas), **_ctx_champs(), **ctx)


@app.get("/premiers-pas/{tache}", response_class=HTMLResponse)
def premiers_pas_tache(request: Request, tache: str):
    if tache not in profil_mod.TACHES:
        return RedirectResponse("/", status_code=303)
    if profil_mod.TACHES[tache].get("entretien") and request.query_params.get("formulaire") != "1":
        return page_parcours(request) if tache == "dossier" else page_criteres(request)
    return _page_tache(request, tache)


# --- Ton parcours et ce qui compte : entretiens guidés par l'IA (parcours.py) ----------------


def _rang_tache(tache):
    p = profil_mod.charger()
    pas = profil_mod.premiers_pas(p, ia_connectee=ia_connectee()) if p.get(profil_mod.PREMIERS_PAS) else []
    return next((i for i, x in enumerate(pas) if x[0] == tache), None), len(pas)


def _refus_ia(e):
    return JSONResponse({"ok": False, "erreur": str(e)}, status_code=200)


@app.get("/parcours", response_class=HTMLResponse)
def page_parcours(request: Request):
    reg = parcours.ouvrir()
    try:
        rang, total = _rang_tache("dossier")
        return page(request, "parcours.html", arbre=parcours.arbre(reg), entretien=parcours.etat_entretien(reg),
                    nb_elements=len(parcours.elements(reg)),
                    vide=parcours.vide(reg), cv=parcours.documents(reg), genres=parcours.GENRES,
                    cadres=parcours.CADRES, ia=ia_connectee(), rang=rang, total=total,
                    t=profil_mod.TACHES["dossier"])
    finally:
        reg.cx.close()


@app.post("/parcours/importer")
async def parcours_importer(request: Request):
    form = await request.form()
    fichier, colle = form.get("fichier"), (form.get("texte") or "").strip()
    try:
        if fichier is not None and getattr(fichier, "filename", ""):
            texte, nom = parcours.texte_du_fichier(fichier.filename, await fichier.read()), fichier.filename
        elif colle:
            texte, nom = parcours.texte_du_fichier("", colle), "CV collé"
        else:
            raise parcours.ParcoursInvalide("choisis ton CV ou colle son texte")
    except parcours.ParcoursInvalide as e:
        return _refus_ia(e)
    from starlette.concurrency import run_in_threadpool

    def importer():
        reg = parcours.ouvrir()
        try:
            return parcours.importer_cv(reg, texte, nom)
        finally:
            reg.cx.close()
    try:
        bilan = await run_in_threadpool(importer)
    except (parcours.ParcoursInvalide, moteur_mod.MoteurImpossible) as e:
        return _refus_ia(e)
    return JSONResponse({"ok": True, **bilan})


@app.post("/parcours/repondre")
def parcours_repondre(reponse: str = Form("")):
    reg = parcours.ouvrir()
    try:
        return JSONResponse({"ok": True, **parcours.repondre(reg, reponse)})
    except (parcours.ParcoursInvalide, moteur_mod.MoteurImpossible) as e:
        return _refus_ia(e)
    finally:
        reg.cx.close()


def _sur_parcours(action, msg=None, ancre="#base"):
    """Une action sur le parcours, puis retour à la page : #base rouvre le volet de la base
    (là où l'on corrigeait), #entretien revient à l'entretien."""
    reg = parcours.ouvrir()
    try:
        action(reg)
    except parcours.ParcoursInvalide as e:
        return RedirectResponse("/parcours?msg=%s" % quote("À corriger : %s." % e), status_code=303)
    finally:
        reg.cx.close()
    return RedirectResponse("/parcours" + ("?msg=%s&ok=1" % quote(msg) if msg else "") + ancre, status_code=303)


@app.post("/parcours/passer")
def parcours_passer():
    return _sur_parcours(parcours.passer, ancre="#entretien")


@app.post("/parcours/ajouter")
async def parcours_ajouter(request: Request):
    f = await request.form()
    parent = int(f["parent"]) if (f.get("parent") or "").isdigit() else None
    champs = {k: f.get(k, "") for k in parcours.CHAMPS if k != "titre"}
    return _sur_parcours(lambda reg: parcours.ajouter(reg, f.get("genre", ""), f.get("titre", ""), parent=parent,
                                                       origine="saisie", citation=f.get("titre", ""), **champs),
                         "Ajouté.")


@app.post("/parcours/{ident}/modifier")
async def parcours_modifier(request: Request, ident: int):
    f = await request.form()
    return _sur_parcours(lambda reg: parcours.modifier(reg, ident, **{k: f[k] for k in parcours.CHAMPS if k in f}),
                         "Enregistré.")


@app.post("/parcours/{ident}/retirer")
def parcours_retirer(ident: int):
    return _sur_parcours(lambda reg: parcours.retirer(reg, ident), "Retiré.")


@app.post("/parcours/{ident}/reprendre")
def parcours_reprendre(ident: int):
    return _sur_parcours(lambda reg: parcours.reprendre(reg, ident), ancre="#entretien")


@app.post("/parcours/vider")
def parcours_vider(confirmer: str = Form("")):
    if confirmer != "oui":
        return RedirectResponse("/parcours", status_code=303)
    return _sur_parcours(parcours.vider, "Parcours vidé : tu peux importer un CV.", ancre="")


def page_criteres(request: Request, msg=None, ok=None):
    reg = parcours.ouvrir()
    try:
        etat = parcours.etat_criteres(reg)
        p = profil_mod.charger()
        prop = etat["proposition"]
        valeurs = {
            "criteres": "\n".join(prop.criteres if prop else profil_mod.lire(p, "evaluation.criteres") or []),
            "interet": prop.interet if prop else profil_mod.lire(p, "evaluation.interet") or "",
            "angles": "\n".join(prop.angles if prop else profil_mod.lire(p, "evaluation.angles") or []),
            "differenciateur": prop.differenciateur if prop else profil_mod.lire(p, "dossier.differenciateur") or "",
        }
        rang, total = _rang_tache("criteres")
        ctx = {k: v for k, v in (("msg", msg), ("ok", ok)) if v is not None}
        return page(request, "criteres_entretien.html", etat=etat, valeurs=valeurs, ia=ia_connectee(),
                    parcours_vide=parcours.vide(reg), rang=rang, total=total, t=profil_mod.TACHES["criteres"], **ctx)
    finally:
        reg.cx.close()


@app.post("/criteres/repondre")
def criteres_repondre(reponse: str = Form("")):
    reg = parcours.ouvrir()
    try:
        r = parcours.repondre_criteres(reg, reponse)
        return JSONResponse({"ok": True, "suivant": r["suivant"], "proposition": r["proposition"].model_dump()})
    except (parcours.ParcoursInvalide, moteur_mod.MoteurImpossible) as e:
        return _refus_ia(e)
    finally:
        reg.cx.close()


@app.post("/criteres/recommencer")
def criteres_recommencer():
    reg = parcours.ouvrir()
    try:
        parcours.recommencer_criteres(reg)
    finally:
        reg.cx.close()
    return RedirectResponse("/premiers-pas/criteres", status_code=303)


@app.post("/premiers-pas/{tache}", response_class=HTMLResponse)
async def premiers_pas_enregistrer(request: Request, tache: str):
    if tache not in profil_mod.TACHES:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    try:
        for e in profil_mod.TACHES[tache]["etapes"]:
            profil_mod.enregistrer(e, form)
        if "dossier.differenciateur" in form:            # proposé par l'entretien « Ce qui compte pour toi »
            profil_mod.poser_valeur("dossier.differenciateur", form.get("dossier.differenciateur"))
    except profil_mod.ProfilInvalide as e:
        if form.get("entretien") == "1":
            return page_criteres(request, msg="À corriger : %s." % e, ok=False)
        saisi = {k: "\n".join(form.getlist(k)) for k in form.keys()}
        return _page_tache(request, tache, saisi=saisi, msg="À corriger : %s." % e, ok=False)
    return RedirectResponse("/?msg=%s&ok=1#premiers-pas" % quote("%s : enregistré." % profil_mod.TACHES[tache]["titre"]),
                            status_code=303)




@app.get("/profil")
def profil_debut():
    manque = profil_mod.manquants()
    return RedirectResponse("/profil/%s" % (manque[0][0] if manque else "identite"), status_code=303)


@app.get("/profil/{ident}", response_class=HTMLResponse)
def profil_page(request: Request, ident: str):
    if ident != "fin" and profil_mod.etape(ident) is None:
        return RedirectResponse("/profil", status_code=303)
    return _page_profil(request, ident)


@app.post("/profil/terminer")
def profil_terminer(request: Request):
    try:
        profil_mod.terminer_accueil()
    except profil_mod.ProfilInvalide as e:
        return RedirectResponse("/profil/fin?msg=%s" % quote("À compléter avant de commencer : %s." % e), status_code=303)
    return RedirectResponse("/?msg=%s&ok=1" % quote("Profil enregistré. Bienvenue !"), status_code=303)


@app.post("/profil/{ident}", response_class=HTMLResponse)
async def profil_enregistrer(request: Request, ident: str):
    form = await request.form()
    try:
        profil_mod.enregistrer(ident, form)
    except profil_mod.ProfilInvalide as e:
        if profil_mod.etape(ident) is None:
            return RedirectResponse("/profil", status_code=303)
        saisi = {k: "\n".join(form.getlist(k)) for k in form.keys()}      # cases cochées : une par ligne
        return _page_profil(request, ident, saisi=saisi, msg="À corriger : %s." % e, ok=False)
    ids = [x["id"] for x in profil_mod.etapes()] + ["fin"]
    suivante = ids[ids.index(ident) + 1]
    return RedirectResponse("/profil/%s?msg=%s&ok=1" % (suivante, quote("Enregistré.")), status_code=303)


# --- Actions --------------------------------------------------------------------


@app.post("/action")
async def action(request: Request):
    form = await request.form()
    emp, type_ = form.get("empreinte", ""), form.get("type", "")
    retour = form.get("retour") or "/"
    if not retour.startswith("/") or retour.startswith("//"):
        retour = "/"
    donnees = {k: form.get(k) for k in ("note", "date", "canal", "motif", "prochaine") if form.get(k)}
    donnees["raisons"] = form.getlist("raisons")
    p = profil_mod.charger()
    if type_ in ("go", "candidature") and profil_mod.progressif(p) and \
            not profil_mod.fonctions(p, ia_connectee())["postuler"]:
        manque = profil_mod.fonctions(p, ia_connectee())["manque"]["postuler"]
        return RedirectResponse("%s?msg=%s" % (retour.split("#")[0], quote(
            "Pour postuler, il reste : %s." % ", ".join(l[:1].lower() + l[1:] for _, l, _ in manque))), status_code=303)
    reg = _reg()
    try:
        msg, ok = actions.appliquer(reg, emp, type_, donnees), True
        if type_ == "candidature":
            dossier.mettre_a_date(reg, emp)        # la lettre prend la date d'envoi, et la garde
            from .. import agent_claude
            if agent_claude.apres_envoi(reg, emp):
                msg += " · preuve Job-Room : Claude s'en occupe (connecte-toi si Chrome le demande)"
        if type_ == "go" and not (reg.get(emp) or {}).get("cv_genere"):
            try:
                dossier.produire_en_tache(emp, reg)     # le dossier démarre tout de suite
                msg += " · dossier en préparation (quelques minutes, notification à la fin)"
            except evaluation.EvaluationImpossible as e:
                msg += " · dossier impossible : %s" % e        # le Go, lui, est enregistré
    except actions.ActionRefusee as e:
        msg, ok = "Refusé : %s" % e, False
    finally:
        reg.fermer()
    base, _, ancre = retour.partition("#")
    sep = "&" if "?" in base else "?"
    return RedirectResponse("%s%smsg=%s&ok=%d%s" % (base, sep, quote(msg), int(ok),
                                                     "#" + ancre if ancre else ""), status_code=303)


# --- Connexion depuis le téléphone ---------------------------------------------


@app.get("/connexion", response_class=HTMLResponse)
def connexion(request: Request):
    return page(request, "connexion.html", erreur=False)


@app.post("/connexion")
def connexion_post(request: Request, jeton_saisi: str = Form("")):
    if jeton_saisi.strip() != jeton():
        return page(request, "connexion.html", erreur=True)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, jeton(), max_age=90 * 86400, httponly=True, samesite="strict")
    return r
