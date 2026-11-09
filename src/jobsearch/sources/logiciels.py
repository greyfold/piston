"""Pages carrière tenues par un logiciel de recrutement connu : branchées sans code.

`reconnaitre(url)` lit l'adresse collée par l'utilisateur et dit quel logiciel la
sert (Workday, Greenhouse, Lever, SmartRecruiters, Recruitee, Personio). Chacun a
une route publique de ses offres, qu'on lit comme celles de jobup : liste complète
à chaque passage, le pré-tri sur l'intitulé fait le reste. Les offres hors de
Suisse sont écartées dès la lecture (ces logiciels servent souvent des groupes
mondiaux) ; une offre sans lieu lisible est gardée.

Une adresse qu'aucun logiciel ne sert passe à la lecture par l'IA (page_ia.py).
"""

import re
from urllib.parse import urlparse

from .base import Annonce, RouteCassee, code_canton, html_vers_texte
from .employeurs import ETIQUETTE, Workday

SUISSE = re.compile(r"(?i)\b(switzerland|suisse|schweiz|svizzera|swiss|CH)\b")
VILLES = {"lausanne": "VD", "vevey": "VD", "montreux": "VD", "nyon": "VD", "morges": "VD", "renens": "VD",
          "yverdon": "VD", "ecublens": "VD", "geneve": "GE", "geneva": "GE", "genf": "GE", "fribourg": "FR",
          "neuchatel": "NE", "sion": "VS", "martigny": "VS", "bienne": "BE", "biel": "BE", "berne": "BE",
          "bern": "BE", "zurich": "ZH", "basel": "BS", "bale": "BS", "lugano": "TI", "zug": "ZG", "lucerne": "LU",
          "luzern": "LU", "delemont": "JU", "monthey": "VS", "gland": "VD", "rolle": "VD", "epalinges": "VD"}
CODES = {"AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE", "NW", "OW", "SG", "SH", "SO",
         "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH"}
PAYS_SUISSE_WORKDAY = "187134fccb084a0ea9b4b95f23890dbe"        # référence pays de Workday (globale)


def _plier(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", t or "") if not unicodedata.combining(c)).lower()


def lieu_suisse(texte):
    """(lieu lisible, canton, en_suisse) d'un texte de lieu libre."""
    t = (texte or "").strip()
    if not t:
        return "", None, True
    for mot in re.split(r"[,;/()\-–|]+|\s+", _plier(t)):
        if mot in VILLES:
            return t, VILLES[mot], True
    canton = next((c for c in (code_canton(x.strip()) for x in re.split(r"[,;]", t)) if c in CODES), None)
    return t, canton, bool(canton) or bool(SUISSE.search(t))


def reconnaitre(url):
    """{type, …} pour une adresse de page carrière, ou {"type": "page-ia", "url": …}."""
    u = urlparse(url.strip())
    hote, chemin = (u.hostname or "").lower(), [p for p in u.path.split("/") if p]
    m = re.match(r"([\w-]+)\.wd\d+\.myworkdayjobs\.com$", hote)
    if m and chemin:
        site = chemin[1] if re.fullmatch(r"[a-z]{2}-[A-Z]{2}", chemin[0]) and len(chemin) > 1 else chemin[0]
        return {"type": "workday", "hote": hote, "tenant": m.group(1), "site": site}
    if hote in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io") and chemin:
        return {"type": "greenhouse", "org": chemin[0]}
    if hote in ("jobs.lever.co", "jobs.eu.lever.co") and chemin:
        return {"type": "lever", "org": chemin[0], "eu": hote.startswith("jobs.eu")}
    if hote in ("careers.smartrecruiters.com", "jobs.smartrecruiters.com") and chemin:
        return {"type": "smartrecruiters", "org": chemin[0]}
    m = re.match(r"([\w-]+)\.recruitee\.com$", hote)
    if m:
        return {"type": "recruitee", "org": m.group(1)}
    m = re.match(r"([\w-]+)\.jobs\.personio\.(de|com)$", hote)
    if m:
        return {"type": "personio", "org": m.group(1), "tld": m.group(2)}
    return {"type": "page-ia", "url": url.strip()}


LIBELLES = {"workday": "Workday", "greenhouse": "Greenhouse", "lever": "Lever", "smartrecruiters": "SmartRecruiters",
            "recruitee": "Recruitee", "personio": "Personio", "page-ia": "lecture de la page par l'IA"}


class _Logiciel:
    page_carriere = True

    def __init__(self, client, nom, entreprise, **params):
        self.c, self.nom, self.entreprise, self.p = client, nom, entreprise, params

    def _annonce(self, ident, titre, url, lieu_texte="", publiee="", texte=""):
        lieu, canton, suisse = lieu_suisse(lieu_texte)
        if not suisse or not titre:
            return None
        return Annonce(source=self.nom, id_source=str(ident), entreprise=self.entreprise, titre=titre.strip(),
                       lieu=lieu, canton=canton, url=url, url_postuler=url, publiee_le=(publiee or "")[:10],
                       texte=texte, requetes=[ETIQUETTE], detail_charge=bool(texte))

    def detail(self, a):
        if not a.texte:
            a.texte = html_vers_texte(self.c.page(a.url))[:20000]
        a.detail_charge = True
        return a

    def tester(self):
        return len(self.offres())


class Greenhouse(_Logiciel):
    def offres(self, aujourdhui=None):
        res = self.c.requete("GET", "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % self.p["org"])
        if not isinstance(res, dict) or "jobs" not in res:
            raise RouteCassee("%s : la liste Greenhouse a changé" % self.nom)
        import html
        return [a for a in (self._annonce(j["id"], j.get("title"), j.get("absolute_url") or "",
                                          (j.get("location") or {}).get("name", ""), j.get("updated_at") or "",
                                          html_vers_texte(html.unescape(j.get("content") or "")))
                            for j in res["jobs"]) if a]


class Lever(_Logiciel):
    def offres(self, aujourdhui=None):
        base = "https://api.eu.lever.co" if self.p.get("eu") else "https://api.lever.co"
        res = self.c.requete("GET", "%s/v0/postings/%s?mode=json" % (base, self.p["org"]))
        if not isinstance(res, list):
            raise RouteCassee("%s : la liste Lever a changé" % self.nom)
        import datetime as dt
        out = []
        for j in res:
            pub = dt.date.fromtimestamp(j["createdAt"] / 1000).isoformat() if j.get("createdAt") else ""
            out.append(self._annonce(j.get("id"), j.get("text"), j.get("hostedUrl") or "",
                                     (j.get("categories") or {}).get("location", ""), pub,
                                     j.get("descriptionPlain") or ""))
        return [a for a in out if a]


class SmartRecruiters(_Logiciel):
    def offres(self, aujourdhui=None):
        out, debut = [], 0
        for _ in range(10):
            res = self.c.requete("GET", "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100&offset=%d"
                                 % (self.p["org"], debut))
            if not isinstance(res, dict) or "content" not in res:
                raise RouteCassee("%s : la liste SmartRecruiters a changé" % self.nom)
            for j in res["content"]:
                loc = j.get("location") or {}
                lieu = ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
                if (loc.get("country") or "").lower() not in ("ch", "switzerland", ""):
                    continue
                url = "https://jobs.smartrecruiters.com/%s/%s" % (self.p["org"], j.get("id"))
                out.append(self._annonce(j.get("id"), j.get("name"), url, lieu + ", Switzerland", j.get("releasedDate") or ""))
            debut += 100
            if debut >= (res.get("totalFound") or 0):
                break
        return [a for a in out if a]


class Recruitee(_Logiciel):
    def offres(self, aujourdhui=None):
        res = self.c.requete("GET", "https://%s.recruitee.com/api/offers/" % self.p["org"])
        if not isinstance(res, dict) or "offers" not in res:
            raise RouteCassee("%s : la liste Recruitee a changé" % self.nom)
        return [a for a in (self._annonce(j.get("id"), j.get("title"), j.get("careers_url") or "",
                                          ", ".join(x for x in (j.get("city"), j.get("country")) if x),
                                          j.get("published_at") or "", html_vers_texte(j.get("description") or ""))
                            for j in res["offers"]) if a]


class Personio(_Logiciel):
    def offres(self, aujourdhui=None):
        org, tld = self.p["org"], self.p.get("tld", "de")
        xml = self.c.page("https://%s.jobs.personio.%s/xml" % (org, tld))
        if "<position" not in xml and "<workzag-jobs" not in xml:
            raise RouteCassee("%s : le flux Personio a changé" % self.nom)
        import html
        out = []
        for bloc in re.findall(r"(?s)<position>(.*?)</position>", xml):
            champ = lambda n: html.unescape((re.search(r"(?s)<%s>(.*?)</%s>" % (n, n), bloc) or [None, ""])[1]).strip()
            ident = champ("id")
            out.append(self._annonce(ident, champ("name"), "https://%s.jobs.personio.%s/job/%s" % (org, tld, ident),
                                     champ("office"), champ("createdAt"),
                                     html_vers_texte(" ".join(re.findall(r"(?s)<value>(.*?)</value>", bloc)))))
        return [a for a in out if a]


class WorkdayAjoutee(Workday):
    """Workday d'un employeur ajouté par l'utilisateur : offres en Suisse, canton tiré de la ville."""

    def __init__(self, client, nom, entreprise, hote, tenant, site):
        super().__init__(client, nom, entreprise, hote, tenant, site, PAYS_SUISSE_WORKDAY)

    def _lieu(self, texte):
        lieu, canton, _ = lieu_suisse((texte or "").split(",")[0])
        return lieu, canton


CLASSES = {"greenhouse": Greenhouse, "lever": Lever, "smartrecruiters": SmartRecruiters, "recruitee": Recruitee,
           "personio": Personio}


def construire(client, nom, entreprise, conn):
    """La source d'une page ajoutée, d'après ce que `reconnaitre` en a dit."""
    t = conn.get("type")
    if t == "workday":
        return WorkdayAjoutee(client, nom, entreprise, conn["hote"], conn["tenant"], conn["site"])
    if t in CLASSES:
        return CLASSES[t](client, nom, entreprise, **{k: v for k, v in conn.items() if k not in ("type", "entreprise")})
    if t == "page-ia":
        from .page_ia import PageIA
        return PageIA(client, nom, entreprise, conn["url"], rendu=bool(conn.get("rendu")))
    raise RouteCassee("%s : type de page inconnu (%s)" % (nom, t))
