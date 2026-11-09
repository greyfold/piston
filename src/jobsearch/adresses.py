"""Adresse postale de l'employeur, pour la preuve Job-Room (NPA / Lieu obligatoires).

Cherchée une fois, à la production du dossier, et rangée avec l'annonce
(`extra.adresse_employeur`) ; la preuve Job-Room la reprend. Jamais devinée :
1. l'adresse que l'employeur a donnée à Job-Room, pour une annonce venue de là ;
2. le registre du commerce (Zefix, la recherche publique du site
   zefix.admin.ch) : adresse officielle du siège, quand le nom de l'annonce
   désigne une seule entreprise inscrite (à défaut, celle dont le siège est
   dans la ville de l'annonce) ;
3. une adresse complète écrite dans le texte de l'annonce (rue, numéro, NPA,
   localité) — organisations internationales, employeurs étrangers ;
4. une adresse complète affichée sur la page de l'annonce ou de postulation,
   lue telle que le navigateur l'affiche (Chrome sans fenêtre, profil vierge :
   ni le Chrome ni les comptes de la personne) — agences de placement, dont
   l'encadré « agence » donne l'adresse ;
5. à défaut, le lieu de travail de l'annonce, sans rue : NPA et localité
   (le NPA de Job-Room, sinon le plus petit NPA de la localité dans la liste
   officielle, via openplzapi.org). Agences de placement, noms commerciaux.
Sans rien de tout cela, l'annonce le note et la preuve Job-Room s'arrête sur
ce champ, comme avant.
"""

import datetime as dt
import re
import unicodedata

import httpx

ZEFIX = "https://www.zefix.admin.ch/ZefixREST/api/v1/firm"
JOBROOM = "https://www.job-room.ch/jobadservice/api/jobAdvertisements/%s"
LOCALITES = "https://openplzapi.org/ch/Localities"
_ID_JOBROOM = re.compile(r"job-room\.ch/job-detail/([0-9a-f-]{36})")
_SUCCURSALE = re.compile(r"\b(succursale|zweigniederlassung|filiale|branch)\b", re.I)
_VOIES = (r"rue|avenue|av\.|chemin|ch\.|route|rte|place|pl\.|boulevard|bd|quai|all[ée]e|impasse|sentier|"
          r"passage|promenade|esplanade|via|viale|piazza|corso")
_ADRESSES = [
    # « Avenue Général Guisan 70a, 1009 Pully », « Bahnhofstrasse 12, CH-8001 Zürich »
    re.compile(r"(?P<rue>(?:\b(?:%s)\s+[^\n,;:\d]{2,60}?|[A-ZÄÖÜ][\wäöüß\-]{2,40}(?:strasse|straße|gasse|weg|platz|allee|ring)))"
               r"\s+(?P<no>\d{1,4}\s?[a-zA-Z]?)\s*,?\s*(?:CH\s?-\s?)?(?P<npa>[1-9]\d{3}),?\s+(?P<ville>[A-ZÀ-ÝÄÖÜ][\w'\-]+(?:[ \-][\w'\-]+){0,3})"
               % _VOIES, re.I),
    # « 34, chemin des Colombettes, 1211 Genève 20 »
    re.compile(r"\b(?P<no>\d{1,4}\s?[a-zA-Z]?),?\s+(?P<rue>(?:%s)\s+[^\n,;:\d]{2,60}?)\s*,\s*(?:CH\s?-\s?)?(?P<npa>[1-9]\d{3})\s+"
               r"(?P<ville>[A-ZÀ-ÝÄÖÜ][\w'\-]+(?:[ \-][\w'\-]+){0,2})" % _VOIES, re.I),
]


def _plier(t):
    t = unicodedata.normalize("NFKD", t or "")
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if not unicodedata.combining(c)).lower()).strip()


def _client():
    return httpx.Client(timeout=15, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})


def _ville_annonce(f):
    lieu = re.sub(r"\b[A-Z]{2}$", "", (f.get("lieu") or "").strip()).strip()
    return _plier(re.sub(r"^\d{4}\s+", "", lieu))


def zefix(nom, ville="", client=None):
    """{rue, numero, npa, ville, origine} du siège inscrit, ou None si le nom est ambigu."""
    nom = re.sub(r"\((?:via|par)\b[^)]*\)", " ", nom or "", flags=re.I).strip()
    if len(nom) < 3:
        return None
    http = client or _client()
    try:
        res = http.post(ZEFIX + "/search.json", json={"name": nom, "languageKey": "fr", "maxEntries": 30})
        if res.status_code != 200:
            return None
        cible = _plier(nom)
        exactes = [x for x in (res.json() or {}).get("list") or []
                   if _plier(x.get("name")) == cible and x.get("status") in ("EXISTIEREND", "ACTIVE", None)
                   and not _SUCCURSALE.search(x.get("name") or "")]
        if len(exactes) > 1 and ville:
            exactes = [x for x in exactes if _plier(x.get("legalSeat")) == ville] or exactes
        if len(exactes) != 1:
            return None
        fiche = http.get("%s/%d.json" % (ZEFIX, exactes[0]["ehraid"]))
        a = (fiche.json() or {}).get("address") or {} if fiche.status_code == 200 else {}
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    finally:
        if client is None:
            http.close()
    if not (a.get("swissZipCode") and a.get("town")):
        return None
    return {"rue": (a.get("street") or "").strip(), "numero": (a.get("houseNumber") or "").replace(" ", ""),
            "npa": str(a["swissZipCode"]), "ville": a["town"].strip(),
            "origine": "registre du commerce (Zefix, %s)" % exactes[0].get("uidFormatted", exactes[0].get("uid", ""))}


def dans_le_texte(texte, origine="texte de l'annonce"):
    """La première adresse complète du texte ; écrite en minuscules de préférence à la même en capitales
    (« Route Industrielle 2 » plutôt que « ROUTE INDUSTRIELLE 2 »)."""
    for motif in _ADRESSES:
        trouvees = list(motif.finditer(texte or ""))
        if trouvees:
            m = next((x for x in trouvees if not x.group(0).isupper()), trouvees[0])
            return {"rue": re.sub(r"\s+", " ", m.group("rue")).strip(), "numero": m.group("no").replace(" ", ""),
                    "npa": m.group("npa"), "ville": m.group("ville").strip(), "origine": origine}
    return None


def _texte_rendu(url):
    """Le texte d'une page tel que le navigateur l'affiche (pages construites en JavaScript),
    rendue par le Chrome de la machine virtuelle dans un contexte isolé. Vide si ça échoue."""
    from .travailleur import client as travailleur
    try:
        return travailleur.Distant.depuis_reglages().rendre(url, attente_ms=4000)["texte"]
    except Exception:
        return ""


def sur_les_pages(f):
    """Une adresse affichée sur la page de postulation ou de l'annonce. Ni LinkedIn (aucun compte
    n'est utilisé), ni Job-Room (son adresse vient déjà de son API)."""
    for url in dict.fromkeys(u for u in (f.get("url_postuler"), f.get("url")) if u):
        if re.search(r"linkedin\.com|job-room\.ch", url):
            continue
        a = dans_le_texte(_texte_rendu(url), "page de l'annonce (%s)" % re.sub(r"^https?://(www\.)?", "", url).split("/")[0])
        if a:
            return a
    return None


def job_room(f, http):
    """(adresse donnée par l'employeur, lieu de travail {npa, ville}) d'une annonce Job-Room."""
    m = _ID_JOBROOM.search(f.get("url") or "")
    if not m:
        return None, None
    try:
        r = http.get(JOBROOM % m.group(1))
        jc = (r.json() or {}).get("jobContent") or {} if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None, None
    c, loc = jc.get("company") or {}, jc.get("location") or {}
    adresse = None
    if c.get("postalCode") and c.get("city") and (c.get("countryIsoCode") or "CH") == "CH":
        adresse = {"rue": (c.get("street") or "").strip(), "numero": (c.get("houseNumber") or "").replace(" ", ""),
                   "npa": str(c["postalCode"]), "ville": c["city"].strip(), "origine": "adresse donnée à Job-Room par l'employeur"}
    lieu = ({"npa": str(loc["postalCode"]), "ville": loc["city"].strip()}
            if loc.get("postalCode") and loc.get("city") and (loc.get("countryIsoCode") or "CH") == "CH" else None)
    return adresse, lieu


# Noms anglais des annonces (« Geneva GE ») → nom officiel de la liste des localités.
EXONYMES = {"geneva": "Genève", "geneve": "Genève", "zurich": "Zürich", "berne": "Bern", "basle": "Basel",
            "lucerne": "Luzern", "neuchatel": "Neuchâtel", "fribourg": "Fribourg", "bienne": "Biel/Bienne",
            "biel": "Biel/Bienne", "sion": "Sion", "st gallen": "St. Gallen", "saint gall": "St. Gallen"}


def npa_de(ville, canton="", http=None):
    """Le plus petit NPA de la localité (celui de la commune elle-même : 1201 Genève, 1003 Lausanne)."""
    ville = EXONYMES.get(_plier(ville), ville)
    try:
        r = http.get(LOCALITES, params={"name": ville})
        liste = r.json() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return None
    cible = _plier(ville)
    npas = [x.get("postalCode") for x in liste or []
            if _plier(x.get("name")) == cible and (not canton or (x.get("canton") or {}).get("shortName") == canton)]
    return min((n for n in npas if n), default=None)


def lieu_de_travail(f, lieu=None, http=None):
    """NPA et localité du lieu de travail, sans rue : le dernier recours de la preuve Job-Room."""
    if lieu:
        return {"rue": "", "numero": "", **lieu, "origine": "lieu de travail de l'annonce (Job-Room, sans adresse d'employeur)"}
    brut = (f.get("lieu") or "").split(",")[0].strip()
    m = re.match(r"^(?:(?P<npa>[1-9]\d{3})\s+)?(?P<ville>.+?)(?:\s+(?P<canton>[A-Z]{2}))?$", brut)
    if not m:
        return None
    npa = m.group("npa") or npa_de(m.group("ville"), m.group("canton") or f.get("canton") or "", http)
    if not npa:
        return None
    return {"rue": "", "numero": "", "npa": npa, "ville": EXONYMES.get(_plier(m.group("ville")), m.group("ville")),
            "origine": "lieu de travail de l'annonce (NPA de la localité, sans adresse d'employeur)"}


def trouver(f, client=None):
    http = client or _client()
    try:
        publiee, lieu = job_room(f, http)
        return (publiee or zefix(f.get("entreprise"), _ville_annonce(f), http) or dans_le_texte(f.get("texte"))
                or sur_les_pages(f) or lieu_de_travail(f, lieu, http))
    finally:
        if client is None:
            http.close()


def en_ligne(a):
    """« Avenue Général Guisan 70a, 1009 Pully »."""
    rue = " ".join(x for x in (a.get("rue"), a.get("numero")) if x)
    return ", ".join(x for x in (rue, "%s %s" % (a.get("npa", ""), a.get("ville", ""))) if x.strip())


def renseigner(reg, emp, client=None):
    """Cherche l'adresse si l'annonce ne l'a pas encore, et la range. Ne lève jamais."""
    f = reg.get(emp)
    if not f:
        return None
    extra = dict(f.get("extra") or {})
    if extra.get("adresse_employeur"):
        return extra["adresse_employeur"]
    try:
        a = trouver(f, client)
    except Exception:            # une adresse manquante ne doit jamais arrêter un dossier
        a = None
    deja_cherchee = extra.get("adresse_cherchee_le")
    extra["adresse_cherchee_le"] = dt.date.today().isoformat()
    if a:
        extra["adresse_employeur"] = a
        reg.noter(emp, "adresse de l'employeur : %s (%s)" % (en_ligne(a), a["origine"]))
    elif not deja_cherchee:
        reg.noter(emp, "adresse de l'employeur introuvable (Job-Room, registre du commerce, annonce, lieu de travail) : "
                       "NPA et lieu à saisir à la main dans Job-Room")
    reg.maj(emp, extra=extra)
    reg.commit()
    return a
