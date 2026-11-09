"""Job-Room (portail officiel) : la route que lit le site lui-même.

Recherche : POST /jobadservice/api/jobAdvertisements/_search, `page` et `size`
dans l'adresse (le corps les refuse depuis le 24.10), corps JSON
{keywords, cantonCodes, onlineSince}. Le total arrive dans `x-total-count`,
on s'arrête à la première page incomplète.
Détail : GET /jobadservice/api/jobAdvertisements/<id> (texte complet).
"""

from .base import Annonce, Client, RouteCassee, sans_surlignage

BASE = "https://www.job-room.ch"
RECHERCHE = BASE + "/jobadservice/api/jobAdvertisements/_search"
DETAIL = BASE + "/jobadservice/api/jobAdvertisements/%s"
FICHE = BASE + "/job-detail/%s"
TAILLE = 50
PAGES_MAX = 6

# Annonces reprises de jobup : l'employeur affiché est la plateforme. Elles
# arrivent de toute façon par la source jobup, avec le vrai employeur.
_RELAIS = {"jobup", "jobs.ch", "jobup.ch", "jobcloud"}


def _texte(jc):
    """Titre et texte dans la langue d'origine de l'annonce.

    Une annonce en plusieurs langues porte plusieurs descriptions : on garde la
    première, celle que l'employeur a saisie, jamais une traduction. Les mots-clés
    du CV et de la lettre se prennent sur ce texte-là. La langue déclarée par
    Job-Room n'est pas fiable (« fr » sur un texte anglais), on ne s'en sert pas.
    """
    desc = [d for d in jc.get("jobDescriptions") or [] if (d.get("description") or "").strip()]
    d = desc[0] if desc else {}
    return sans_surlignage(d.get("title") or ""), sans_surlignage(d.get("description") or "")


def _entier(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def convertir(brut, requete=None):
    ja = brut.get("jobAdvertisement", brut)
    jc = ja.get("jobContent") or {}
    titre, texte = _texte(jc)
    entreprise = ((jc.get("company") or {}).get("name") or "").strip()
    if not titre or entreprise.lower() in _RELAIS:
        return None
    loc = jc.get("location") or {}
    emp = jc.get("employment") or {}
    lieu = " ".join(x for x in (loc.get("city"), loc.get("cantonCode")) if x)
    apply = jc.get("applyChannel") or {}
    return Annonce(
        source="job-room", id_source=ja["id"], entreprise=entreprise, titre=titre.strip(),
        lieu=lieu, canton=loc.get("cantonCode"), url=FICHE % ja["id"],
        url_postuler=jc.get("externalUrl") or apply.get("formUrl") or "",
        publiee_le=(ja.get("createdTime") or "")[:10], texte=texte.strip(),
        taux_min=_entier(emp.get("workloadPercentageMin")), taux_max=_entier(emp.get("workloadPercentageMax")),
        permanent=emp.get("permanent"), requetes=[requete] if requete else [],
        fin_publication=(ja.get("publication") or {}).get("endDate") or "")


class JobRoom:
    nom = "job-room"

    def __init__(self, client: Client):
        self.c = client

    def chercher(self, mots, cantons, depuis_jours, requete=None):
        out = []
        for page in range(PAGES_MAX):
            res = self.c.requete("POST", "%s?page=%d&size=%d" % (RECHERCHE, page, TAILLE),
                                 json={"keywords": [mots], "cantonCodes": list(cantons),
                                       "onlineSince": int(depuis_jours)})
            if not isinstance(res, list):
                raise RouteCassee("Job-Room : format de réponse inattendu")
            for brut in res:
                a = convertir(brut, requete or mots)
                if a:
                    out.append(a)
            if len(res) < TAILLE:
                break
        return out

    def detail(self, a: Annonce):
        """Complète le texte (la recherche n'en sert qu'un extrait)."""
        brut = self.c.requete("GET", DETAIL % a.id_source)
        complet = convertir({"jobAdvertisement": brut})
        if complet and len(complet.texte) > len(a.texte):
            a.texte = complet.texte
        a.detail_charge = True
        return a

    def tester(self):
        """Test de route : une recherche minimale doit rendre une liste."""
        res = self.c.requete("POST", "%s?page=0&size=1" % RECHERCHE,
                             json={"keywords": ["manager"], "cantonCodes": ["VD"], "onlineSince": 30})
        if not isinstance(res, list):
            raise RouteCassee("Job-Room : la recherche ne rend plus une liste")
        return len(res)
