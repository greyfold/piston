"""LinkedIn par Apify (acteur crawlworks/linkedin-jobs-scraper), décision du 26.10.

Aucun compte LinkedIn personnel n'est utilisé : l'acteur lit les offres
publiques depuis l'infrastructure d'Apify : requêtes du profil (syntaxe LinkedIn,
« OR »), un lieu, les champs de l'acteur, un plafond de dépense par run. Le dédoublonnage et la sortie
JSON/CSV du script sont ceux du passage (ingestion, `data/runs/…/sourcing.json`).

Toutes les recherches sont lancées d'un coup, puis attendues ensemble (une
recherche prend une à trois minutes) ; un run qui dépasse `attente_max_s` est
arrêté, pour ne pas payer un run que personne ne lira. La période de
publication suit la fenêtre du passage, arrondie aux valeurs que l'acteur
accepte (1, 3, 7 ou 30 jours).

Le jeton Apify vit dans le trousseau macOS (service « jobsearch-apify »),
jamais dans un fichier ni dans une adresse.
"""

import re
import time

from .. import cles
from .base import Annonce, RouteCassee, code_canton, html_vers_texte
from .employeurs import _relative

API = "https://api.apify.com/v2"
SERVICE_TROUSSEAU = "jobsearch-apify"
PERIODES = {1: "86400", 3: "259200", 7: "604800", 30: "2592000"}
FINS = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")


def jeton_trousseau():
    jeton = cles.lire("apify")
    if not jeton:
        raise RouteCassee("linkedin : jeton Apify absent du trousseau (service %s) : à saisir sur la page Santé"
                          % SERVICE_TROUSSEAU)
    return jeton


def periode(jours):
    """Plus petite période de l'acteur qui couvre la fenêtre."""
    return PERIODES[next((p for p in sorted(PERIODES) if p >= int(jours or 1)), 30)]


def _premier(d, *cles):
    """Les noms de champs de l'acteur varient : la première clé renseignée."""
    for c in cles:
        v = d
        for morceau in c.split("."):
            v = v.get(morceau) if isinstance(v, dict) else None
        if v not in (None, "", []):
            return v
    return ""


def convertir(item, requete=None):
    titre = str(_premier(item, "title", "jobTitle", "position")).strip()
    entreprise = str(_premier(item, "companyName", "company.name", "company")).strip()
    url = str(_premier(item, "jobUrl", "url", "link"))
    ident = str(_premier(item, "jobId", "id", "job_id") or "")
    if not ident:
        m = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)", url)
        ident = m.group(1) if m else url
    if not titre or not entreprise or not ident:
        return None
    lieu = str(_premier(item, "location", "jobLocation", "place"))
    morceaux = [m.strip() for m in lieu.split(",") if m.strip()]
    canton = next((code_canton(m) for m in morceaux[1:] if code_canton(m)), None)
    ville = morceaux[0] if morceaux and not code_canton(morceaux[0]) else ""
    pub = str(_premier(item, "postedAt", "publishedAt", "postedDate", "listedAt"))
    texte = str(_premier(item, "description", "descriptionText", "jobDescription"))
    if "<" in texte:
        texte = html_vers_texte(texte)
    return Annonce(
        source="linkedin", id_source=ident, entreprise=entreprise, titre=titre,
        lieu=" ".join(x for x in (ville, canton) if x), canton=canton,
        url="https://www.linkedin.com/jobs/view/%s/" % ident if ident.isdigit() else url,
        url_postuler=str(_premier(item, "applyUrl", "applyLink")),
        publiee_le=pub[:10] if re.match(r"\d{4}-\d{2}-\d{2}", pub) else _relative(pub),
        texte=texte.strip(), requetes=[requete] if requete else [], detail_charge=bool(texte.strip()))


class LinkedIn:
    nom = "linkedin"
    selon_fenetre = True

    def __init__(self, client, reglages, requetes, lieu, jeton=None, dormir=time.sleep):
        self.c, self.r = client, reglages or {}
        self.requetes, self.lieu = list(requetes or []), lieu
        self.acteur = self.r.get("acteur") or "crawlworks~linkedin-jobs-scraper"
        self._jeton, self.dormir = jeton, dormir

    def _appel(self, methode, chemin, **kw):
        if not self._jeton:
            self._jeton = jeton_trousseau()
        return self.c.requete(methode, API + chemin, headers={"Authorization": "Bearer %s" % self._jeton}, **kw)

    def _lancer(self, requete, jours):
        entree = {
            "query": requete, "location": self.lieu, "jobsToFetch": int(self.r.get("annonces_par_requete", 25)),
            "timePostedRange": periode(jours), "enrichCompanyDetails": False,
            "easyApply": False, "under10Applicants": False,
            # Filtres de contrat, de niveau et de mode de travail inopérants côté LinkedIn
            # depuis août 2026 (constat du script) : tous à False.
            **{k: False for k in ("fullTime", "partTime", "contract", "temporary", "internship", "volunteer",
                                  "onSite", "hybrid", "remote", "internshipLevel", "entryLevel", "associate",
                                  "midSeniorLevel", "director", "executive")},
        }
        res = self._appel("POST", "/acts/%s/runs" % self.acteur, json=entree,
                          params={"maxTotalChargeUsd": self.r.get("plafond_usd_par_run", 1.0)})
        return (res or {}).get("data") or {}

    def offres(self, jours=3):
        if not self.requetes:
            return []
        runs = {q: self._lancer(q, jours) for q in self.requetes}
        debut, attente = time.monotonic(), float(self.r.get("attente_max_s", 480))
        while any(r.get("status") not in FINS for r in runs.values()):
            if time.monotonic() - debut > attente:
                for r in runs.values():
                    if r.get("id") and r.get("status") not in FINS:
                        self._appel("POST", "/actor-runs/%s/abort" % r["id"])
                        r["status"] = "ABORTED"
                break
            self.dormir(10)
            for q, r in runs.items():
                if r.get("id") and r.get("status") not in FINS:
                    runs[q] = (self._appel("GET", "/actor-runs/%s" % r["id"]) or {}).get("data") or r
        out, ratees = [], []
        for q, r in runs.items():
            if r.get("status") != "SUCCEEDED":
                ratees.append("%s : %s" % (q, r.get("status") or "non lancé"))
                continue
            items = self._appel("GET", "/datasets/%s/items" % r["defaultDatasetId"],
                                params={"clean": "true", "format": "json"})
            for it in items if isinstance(items, list) else []:
                a = convertir(it, q)
                if a:
                    out.append(a)
        if ratees and not out:
            raise RouteCassee("linkedin : aucun run Apify réussi (%s)" % " ; ".join(ratees)[:250])
        return out

    def detail(self, a: Annonce):
        """L'acteur rend déjà le texte complet."""
        a.detail_charge = True
        return a

    def tester(self):
        """Jeton et acteur joignables, sans lancer de run (gratuit)."""
        res = self._appel("GET", "/acts/%s" % self.acteur)
        if not isinstance(res, dict) or "data" not in res:
            raise RouteCassee("linkedin : l'acteur Apify %s ne répond plus" % self.acteur)
        return 1


# --- Le compte Apify de la personne : de quoi régler LinkedIn sur sa page --------------------------


def compte(client, acteur="crawlworks~linkedin-jobs-scraper", jeton=None):
    """Ce que le compte Apify dit de lui (lecture seule, gratuit) : l'offre, le crédit du mois et
    le coût des dernières recherches LinkedIn faites avec ce jeton.

    `users/me` (offre, crédit inclus), `users/me/limits` (cycle, limite, dépense du mois) et les
    derniers runs réussis de l'acteur (`usageTotalUsd`). Lève RouteCassee si Apify ne répond pas."""
    jeton = jeton or jeton_trousseau()
    lire = lambda chemin, **kw: (client.requete("GET", API + chemin, headers={"Authorization": "Bearer %s" % jeton},
                                                **kw) or {}).get("data") or {}
    moi, limites = lire("/users/me"), lire("/users/me/limits")
    runs = lire("/acts/%s/runs" % acteur, params={"desc": "1", "limit": "20", "status": "SUCCEEDED"}).get("items") or []
    couts = [float(r["usageTotalUsd"]) for r in runs if isinstance(r.get("usageTotalUsd"), (int, float))]
    plan = moi.get("plan") or {}
    nombre = lambda v: float(v) if isinstance(v, (int, float)) else None
    return {"offre": plan.get("description") or plan.get("id") or "",
            "credit_inclus": nombre(plan.get("monthlyUsageCreditsUsd")),
            "limite": nombre((limites.get("limits") or {}).get("maxMonthlyUsageUsd")),
            "utilise": nombre((limites.get("current") or {}).get("monthlyUsageUsd")) or 0.0,
            "fin_cycle": str((limites.get("monthlyUsageCycle") or {}).get("endAt") or "")[:10],
            "cout_moyen": sum(couts) / len(couts) if couts else None, "runs": len(couts)}


def conseil(c, n_requetes, plafond_actuel, aujourdhui=None):
    """Ce que le compte permet d'ici la fin de son cycle, à un passage par jour et une recherche
    Apify par poste visé : la dépense prévue, si le crédit suffit, et le plafond par recherche
    (`maxTotalChargeUsd`) à proposer : de quoi couvrir une recherche habituelle (coût moyen et une
    marge), sans dépasser la part du crédit restant qui revient à chaque recherche."""
    import datetime as dt
    import math
    aujourdhui = aujourdhui or dt.date.today()
    try:
        jours = max(1, (dt.date.fromisoformat(c["fin_cycle"]) - aujourdhui).days)
    except (TypeError, ValueError):
        jours = 30
    budget = min(x for x in (c.get("limite"), c.get("credit_inclus")) if x is not None) \
        if c.get("limite") is not None or c.get("credit_inclus") is not None else None
    reste = max(0.0, budget - c.get("utilise", 0.0)) if budget is not None else None
    n = max(0, int(n_requetes or 0))
    part = reste / (n * jours) if reste is not None and n else None
    prevu = c["cout_moyen"] * n * jours if c.get("cout_moyen") is not None else None
    utile = max(0.10, c["cout_moyen"] * 1.5) if c.get("cout_moyen") is not None else float(plafond_actuel or 1.0)
    plafond = min(utile, part) if part is not None else utile
    plafond = max(0.05, math.floor(plafond * 20) / 20)          # par pas de 5 centimes, vers le bas
    return {"jours": jours, "budget": budget, "reste": reste, "requetes": n, "prevu": prevu,
            "suffisant": None if prevu is None or reste is None else prevu <= reste,
            "part": part, "plafond": round(plafond, 2), "serre": part is not None and part < utile,
            "a_changer": abs(round(plafond, 2) - float(plafond_actuel or 0)) >= 0.01}
