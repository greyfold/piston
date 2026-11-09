"""Routes des pages carrière d'employeurs : toutes leurs offres ouvertes.

Contrairement à Job-Room et jobup, on ne cherche pas par mots-clés : chaque
passage lit la liste complète des offres de l'employeur (quelques dizaines au
plus), et c'est le pré-tri sur l'intitulé qui écarte les métiers exclus. Le
texte complet n'est lu que pour les annonces nouvelles qui passent ce pré-tri.

Aucune page carrière n'est branchée dans le code : la personne ajoute les siennes depuis
la page Sources (`sources/ajoutees.py`, `sources/logiciels.py`). Deux logiciels lus ici :

- Workday : POST /wday/cxs/<tenant>/<site>/jobs, corps
  {appliedFacets, limit ≤ 20, offset, searchText} ; filtre pays par
  `locationCountry`. La liste ne donne qu'une date relative (« il y a 5 jours »),
  le détail GET /wday/cxs/<tenant>/<site><externalPath> donne `startDate`.
- Johdi Suite : la page carrière porte la clé de l'entreprise
  (`data-company-hash-key`, chiffrée, elle peut changer : on la relit à chaque
  passage) ; GET ats.johdisuite.ch/api/company/<clé>/publicationFlows/web/offers/fr
  rend la liste, …/offer/<id>/fr le détail.
"""

import datetime as dt
import html
import re

from . import jobup
from .base import Annonce, Client, RouteCassee, code_canton, html_vers_texte

ETIQUETTE = "page carrière"


def _relative(texte, aujourdhui=None):
    """« Offre publiée il y a 5 jours » / « Posted 5 Days Ago » → date ISO ;
    « 30 jours ou plus » reste inconnue (le détail la donne)."""
    t = (texte or "").lower()
    j = aujourdhui or dt.date.today()
    if "aujourd" in t or "today" in t:
        n = 0
    elif "hier" in t or "yesterday" in t:
        n = 1
    else:
        m = re.search(r"(\d+)\s*(?:jours?|days?)", t)
        if not m or "plus" in t or "+" in t:
            return ""
        n = int(m.group(1))
    return (j - dt.timedelta(days=n)).isoformat()


class _PageCarriere:
    page_carriere = True

    def __init__(self, client: Client, nom, entreprise):
        self.c, self.nom, self.entreprise = client, nom, entreprise


class Workday(_PageCarriere):
    LIGNES = 20
    PAGES_MAX = 10

    def __init__(self, client, nom, entreprise, hote, tenant, site, pays, cantons=None):
        super().__init__(client, nom, entreprise)
        self.api = "https://%s/wday/cxs/%s/%s" % (hote, tenant, site)
        self.public = "https://%s/%s" % (hote, site)
        self.pays, self.cantons = pays, cantons or {}

    def _liste(self, offset, limite):
        res = self.c.requete("POST", self.api + "/jobs", json={
            "appliedFacets": {"locationCountry": [self.pays]}, "limit": limite,
            "offset": offset, "searchText": ""})
        if not isinstance(res, dict) or "jobPostings" not in res:
            raise RouteCassee("%s : format de réponse inattendu" % self.nom)
        return res

    def _lieu(self, texte):
        ville = (texte or "").split(",")[0].strip()
        if not ville or re.search(r"\d+\s*(sites|locations)", texte or ""):
            return "", None
        canton = self.cantons.get(ville)
        return " ".join(x for x in (ville, canton) if x), canton

    def offres(self, aujourdhui=None):
        out, offset = [], 0
        for _ in range(self.PAGES_MAX):
            res = self._liste(offset, self.LIGNES)
            for j in res["jobPostings"]:
                if not j.get("title") or not j.get("externalPath"):
                    continue
                lieu, canton = self._lieu(j.get("locationsText"))
                ident = (j.get("bulletFields") or [None])[0] or j["externalPath"].rsplit("_", 1)[-1]
                out.append(Annonce(
                    source=self.nom, id_source=str(ident), entreprise=self.entreprise,
                    titre=j["title"].strip(), lieu=lieu, canton=canton,
                    url=self.public + j["externalPath"], url_postuler=self.public + j["externalPath"],
                    publiee_le=_relative(j.get("postedOn"), aujourdhui), requetes=[ETIQUETTE]))
            offset += self.LIGNES
            if len(res["jobPostings"]) < self.LIGNES or offset >= (res.get("total") or 0):
                break
        return out

    def detail(self, a: Annonce):
        if not a.url.startswith(self.public):
            raise RouteCassee("%s : adresse d'annonce inattendue %s" % (self.nom, a.url))
        chemin = a.url[len(self.public):]
        info = (self.c.requete("GET", self.api + chemin) or {}).get("jobPostingInfo") or {}
        texte = html_vers_texte(info.get("jobDescription") or "")
        if len(texte) > len(a.texte):
            a.texte = texte
        a.publiee_le = (info.get("startDate") or "")[:10] or a.publiee_le
        ville = ((info.get("jobRequisitionLocation") or {}).get("descriptor") or "").strip()
        if ville and not a.lieu:
            a.canton = self.cantons.get(ville)
            a.lieu = " ".join(x for x in (ville, a.canton) if x)
        a.url = info.get("externalUrl") or a.url
        a.url_postuler = a.url
        a.detail_charge = True
        return a

    def tester(self):
        return len(self._liste(0, 1)["jobPostings"])


class Johdi(_PageCarriere):
    API = "https://ats.johdisuite.ch/api/company/%s/publicationFlows/web"

    def __init__(self, client, nom, entreprise, page):
        super().__init__(client, nom, entreprise)
        self.page = page
        self._cle = None

    def cle(self):
        if not self._cle:
            m = re.search(r'data-company-hash-key="([^"]+)"', self.c.page(self.page))
            if not m:
                raise RouteCassee("%s : clé Johdi Suite absente de la page carrière" % self.nom)
            self._cle = html.unescape(m.group(1))
        return self._cle

    def _convertir(self, o, requete=None):
        titre = (o.get("title") or "").strip()
        if not titre:
            return None
        canton = code_canton(o.get("canton"))
        ville = (o.get("city") or o.get("work_place") or "").strip()
        texte = "\n\n".join(t for t in (html_vers_texte(o.get("introduction") or ""),
                                        html_vers_texte(o.get("description") or "")) if t)
        return Annonce(
            source=self.nom, id_source=str(o["id"]), entreprise=self.entreprise, titre=titre,
            lieu=" ".join(x for x in (ville, canton) if x), canton=canton,
            url="%s#/offer/%s/%s" % (self.page, o["id"], o.get("slug") or ""),
            url_postuler=o.get("apply_link") or o.get("postulation_url") or "",
            publiee_le=(o.get("publication_date") or "")[:10], texte=texte,
            taux_min=o.get("activity_from"), taux_max=o.get("activity_to"),
            permanent={"CDI": True, "CDD": False}.get(o.get("contract_type")),
            fin_publication=(o.get("expiration_date") or "")[:10],
            requetes=[requete] if requete else [])

    def _liste(self):
        res = self.c.requete("GET", (self.API % self.cle()) + "/offers/fr")
        if not isinstance(res, list):
            raise RouteCassee("%s : la liste Johdi Suite n'est plus une liste" % self.nom)
        return res

    def offres(self, aujourdhui=None):
        return [a for a in (self._convertir(o, ETIQUETTE) for o in self._liste()) if a]

    def detail(self, a: Annonce):
        complet = self._convertir(self.c.requete("GET", (self.API % self.cle()) + "/offer/%s/fr" % a.id_source))
        if complet:
            if len(complet.texte) > len(a.texte):
                a.texte = complet.texte
            a.url_postuler = complet.url_postuler or a.url_postuler
            a.fin_publication = complet.fin_publication or a.fin_publication
        a.detail_charge = True
        return a

    def tester(self):
        self._cle = None
        return len(self._liste())


def connecteurs(client):
    """Les pages carrière branchées dans le code : aucune. Celles de la personne s'ajoutent
    depuis la page Sources, dans son dossier de données."""
    return {}


BRANCHEES = set(connecteurs(None))
