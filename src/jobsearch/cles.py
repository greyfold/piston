"""Clés d'accès des services payants, dans le coffre à clés du système.

Claude n'a pas de clé : le moteur IA passe par Claude Code, connecté au compte
Claude de l'utilisateur (moteur/__init__.py). Reste la clé Apify (LinkedIn).

Une clé ne vit que dans le coffre à clés de l'ordinateur (`hote.coffre()` : trousseau de macOS,
gestionnaire d'identification de Windows, Secret Service sous Linux) : jamais dans un fichier, un
journal, une adresse ni un argument de commande. Sa forme est vérifiée avant de l'écrire, ce qui
empêche aussi toute injection dans ce que le coffre reçoit.

Le tableau de bord ne montre jamais une clé : seulement si elle est là, ses
quatre derniers caractères, et le résultat d'un essai auprès du service.
"""

import re

import httpx

from . import hote

COMPTE = "jobsearch"
SERVICES = {
    "apify": {
        "nom": "Apify (LinkedIn)", "trousseau": "jobsearch-apify", "prefixe": "apify_api_",
        "usage": "Offres LinkedIn publiques (acteur crawlworks/linkedin-jobs-scraper).",
        "ou": "console.apify.com › Settings › API & Integrations",
    },
}
_ALPHABET = re.compile(r"[A-Za-z0-9_\-]{20,300}")


class CleRefusee(Exception):
    pass


def _coffre(coffre):
    """Le coffre donné (tests), sinon celui du système (que conftest remplace : jamais le vrai)."""
    return coffre if coffre is not None else hote.coffre()


def _service(nom):
    if nom not in SERVICES:
        raise CleRefusee("service inconnu : %s" % nom)
    return SERVICES[nom]


def lire(nom, coffre=None):
    """La clé, ou None si elle n'est pas dans le coffre."""
    return _coffre(coffre).lire(_service(nom)["trousseau"])


def etat(nom, coffre=None):
    cle = lire(nom, coffre)
    return dict(_service(nom), id=nom, presente=bool(cle), fin=cle[-4:] if cle else "")


def verifier(nom, cle, client=None):
    """(True, …) acceptée, (False, …) refusée par le service, (None, …) injoignable."""
    http = client or httpx.Client(timeout=15)
    try:
        r = http.get("https://api.apify.com/v2/users/me", headers={"Authorization": "Bearer %s" % cle})
    except httpx.HTTPError as e:
        return None, "service injoignable (%s)" % type(e).__name__
    finally:
        if client is None:
            http.close()
    if r.status_code in (401, 403):
        return False, "clé refusée par le service (HTTP %d)" % r.status_code
    if r.status_code >= 400:
        return None, "réponse inattendue du service (HTTP %d)" % r.status_code
    return True, "clé acceptée par le service"


def enregistrer(nom, cle, coffre=None, client=None):
    """Vérifie la forme, essaie la clé auprès du service, puis la range. Renvoie le message."""
    s = _service(nom)
    c = _coffre(coffre)
    cle = (cle or "").strip()
    if not cle.startswith(s["prefixe"]):
        raise CleRefusee("une clé %s commence par « %s »" % (s["nom"], s["prefixe"]))
    if not _ALPHABET.fullmatch(cle):
        raise CleRefusee("caractères inattendus dans la clé (copie incomplète ?)")
    ok, msg = verifier(nom, cle, client)
    if ok is False:
        raise CleRefusee(msg)
    c.retirer(s["trousseau"])
    if not c.ecrire(s["trousseau"], COMPTE, cle) or c.lire(s["trousseau"]) != cle:
        raise CleRefusee("le %s a refusé l'enregistrement" % hote.libelle("coffre"))
    return "Clé %s enregistrée (…%s) : %s." % (s["nom"], cle[-4:], msg)


def retirer(nom, coffre=None):
    _coffre(coffre).retirer(_service(nom)["trousseau"])
    return "Clé %s retirée du %s." % (_service(nom)["nom"], hote.libelle("coffre"))
