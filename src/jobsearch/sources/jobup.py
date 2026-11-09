"""jobup.ch et jobs.ch (même base, même route publique de recherche).

Un connecteur par site (`site=`), sources « jobup » et « jobs-ch » : les deux sites
publient en grande partie les mêmes offres, l'ingestion les réunit par
l'empreinte employeur + intitulé.

Recherche : GET /api/v1/public/search?query=…&location=…&rows=20&page=N.
`rows` plafonne à 20 (au-delà, HTTP 422) ; des guillemets dans `query` cassent
la requête sans erreur, on les retire. Les pages commencent à 1.
`location` filtre sur une ville et ses environs, `radius` est ignoré (constaté
le 24.10 : « Lausanne » rend 10 résultats, « Vaud » 16, sans filtre 335) : on
interroge donc canton par canton, par son nom. Les résultats ne sont pas triés
par date, la fenêtre se filtre ici.
Détail : GET /api/v1/public/search/job/<id> (texte complet, canton, lien de
postulation).
"""

import datetime as dt
from urllib.parse import urlencode

from .base import Annonce, Client, RouteCassee, html_vers_texte

BASE = "https://www.jobup.ch"
RECHERCHE = BASE + "/api/v1/public/search"
DETAIL = BASE + "/api/v1/public/search/job/%s"
LIGNES = 20
PAGES_MAX = 5


def _date(v):
    return (v or "")[:10]


def convertir(doc, requete=None, source="jobup", base=BASE):
    titre = (doc.get("title") or "").strip()
    if not titre:
        return None
    grades = [g for g in doc.get("employment_grades") or [] if isinstance(g, int)]
    lien = ((doc.get("_links") or {}).get("detail_fr") or {}).get("href") or \
        "%s/fr/emplois/detail/%s/" % (base, doc["job_id"])
    locs = doc.get("locations") or []
    canton = locs[0].get("cantonCode") if locs else None
    texte = html_vers_texte(doc.get("template_text") or "") or (doc.get("preview") or "")
    return Annonce(
        source=source, id_source=doc["job_id"], entreprise=(doc.get("company_name") or "?").strip(),
        titre=titre, lieu=" ".join(x for x in (doc.get("place"), canton) if x), canton=canton,
        url=lien, url_postuler=doc.get("application_url") or doc.get("external_url") or "",
        publiee_le=_date(doc.get("initial_publication_date") or doc.get("publication_date")),
        texte=texte.strip(), taux_min=min(grades) if grades else None,
        taux_max=max(grades) if grades else None, requetes=[requete] if requete else [],
        fin_publication=doc.get("publication_end_date") or "")


class Jobup:
    nom = "jobup"

    def __init__(self, client: Client, site="jobup.ch"):
        self.c, self.site = client, site
        self.base = "https://www.%s" % site
        self.nom = "jobup" if site == "jobup.ch" else site.replace(".", "-")
        self.recherche, self.route_detail = self.base + "/api/v1/public/search", self.base + "/api/v1/public/search/job/%s"

    def _convertir(self, doc, requete=None):
        return convertir(doc, requete, self.nom, self.base)

    def chercher(self, mots, lieu, depuis_jours, requete=None, aujourdhui=None):
        limite = ((aujourdhui or dt.date.today()) - dt.timedelta(days=int(depuis_jours))).isoformat()
        out = []
        for page in range(1, PAGES_MAX + 1):
            q = urlencode({"query": mots.replace('"', "").replace("'", " "), "location": lieu,
                           "rows": LIGNES, "page": page})
            res = self.c.requete("GET", "%s?%s" % (self.recherche, q))
            docs = res.get("documents") if isinstance(res, dict) else None
            if docs is None:
                raise RouteCassee("%s : format de réponse inattendu" % self.nom)
            for d in docs:
                a = self._convertir(d, requete or mots)
                if a and not (a.publiee_le and a.publiee_le < limite):
                    out.append(a)
            if len(docs) < LIGNES or page >= (res.get("num_pages") or 1):
                break
        return out

    def detail(self, a: Annonce):
        doc = self.c.requete("GET", self.route_detail % a.id_source)
        complet = self._convertir(doc)
        if complet:
            if len(complet.texte) > len(a.texte):
                a.texte = complet.texte
            a.canton = complet.canton or a.canton
            a.lieu = complet.lieu or a.lieu
            a.url_postuler = complet.url_postuler or a.url_postuler
        a.detail_charge = True
        return a

    def tester(self):
        res = self.c.requete("GET", "%s?%s" % (self.recherche, urlencode(
            {"query": "manager", "location": "Vaud", "rows": 1, "page": 1})))
        if not isinstance(res, dict) or "documents" not in res:
            raise RouteCassee("%s : la recherche ne rend plus de documents" % self.nom)
        return len(res["documents"])
