"""Ce qui est commun aux sources : le format d'une annonce brute et le client HTTP.

Les routes de Job-Room et de jobup sont celles des sites, sans contrat de
service (constat F-20). On les interroge au rythme d'un humain, et chaque
passage commence par un test de route : une route cassée produit une alerte,
pas un passage vide.
"""

import html
import re
import time
import unicodedata
from dataclasses import dataclass, field

import httpx

AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) " \
        "Chrome/131.0 Safari/537.36"


@dataclass
class Annonce:
    source: str
    id_source: str
    entreprise: str
    titre: str
    lieu: str = ""
    canton: str | None = None
    url: str = ""
    url_postuler: str = ""
    publiee_le: str = ""
    texte: str = ""
    taux_min: int | None = None
    taux_max: int | None = None
    permanent: bool | None = None
    fin_publication: str = ""
    requetes: list = field(default_factory=list)
    detail_charge: bool = False


class RouteCassee(Exception):
    pass


class Client:
    def __init__(self, pause=0.8, timeout=20.0, transport=None):
        self.pause = pause
        self.http = httpx.Client(timeout=timeout, follow_redirects=True, transport=transport,
                                 headers={"User-Agent": AGENT, "Accept": "application/json",
                                          "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.8"})
        self._dernier = 0.0
        self.requetes = 0

    def _attendre(self):
        reste = self.pause - (time.monotonic() - self._dernier)
        if reste > 0:
            time.sleep(reste)
        self._dernier = time.monotonic()

    def requete(self, methode, url, **kw):
        self._attendre()
        self.requetes += 1
        r = self.http.request(methode, url, **kw)
        if r.status_code >= 400:
            raise RouteCassee("%s %s → HTTP %d" % (methode, url.split("?")[0], r.status_code))
        try:
            return r.json()
        except ValueError as e:
            raise RouteCassee("%s %s → réponse non JSON" % (methode, url.split("?")[0])) from e

    def page(self, url):
        """Une page HTML (page carrière d'un employeur), en texte brut."""
        self._attendre()
        self.requetes += 1
        r = self.http.get(url, headers={"Accept": "text/html"})
        if r.status_code >= 400:
            raise RouteCassee("GET %s → HTTP %d" % (url.split("?")[0], r.status_code))
        return r.text

    def fermer(self):
        self.http.close()


def html_vers_texte(h):
    """HTML d'une annonce → texte qui garde la structure : intertitres en
    « ### », gras en « ** », puces en « - » (relu par formatage.py)."""
    if not h:
        return ""
    h = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", h)
    h = re.sub(r"(?is)<h\d[^>]*>(.*?)</h\d>", lambda m: "\n\n### %s\n\n" % re.sub(r"<[^>]+>", " ", m.group(1)).strip(), h)
    h = re.sub(r"(?is)<(strong|b)\b[^>]*>\s*(.*?)\s*</\1>", lambda m: "**%s**" % m.group(2) if m.group(2).strip() else " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</li>|</div>", "\n", h)
    h = re.sub(r"(?i)</p>", "\n\n", h)
    h = re.sub(r"(?i)<li[^>]*>", "\n- ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h).replace("\xa0", " ")
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n\s*\n+", "\n\n", "\n".join(l.strip() for l in h.split("\n"))).strip()


CANTONS = {"vaud": "VD", "geneve": "GE", "fribourg": "FR", "valais": "VS", "neuchatel": "NE",
           "jura": "JU", "berne": "BE", "bern": "BE", "zurich": "ZH", "bale": "BS", "basel": "BS"}


def code_canton(nom):
    """« Vaud » → « VD » ; un code déjà en deux lettres passe tel quel."""
    if not nom:
        return None
    if re.fullmatch(r"[A-Z]{2}", nom.strip()):
        return nom.strip()
    t = "".join(c for c in unicodedata.normalize("NFKD", nom) if not unicodedata.combining(c))
    return CANTONS.get(t.strip().lower())


def sans_surlignage(t):
    return re.sub(r"</?em>", "", t or "")
