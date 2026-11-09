"""Clés d'accès des services payants, dans le trousseau macOS.

Claude n'a pas de clé : le moteur IA passe par Claude Code, connecté au compte
Claude de l'utilisateur (moteur/__init__.py). Reste la clé Apify (LinkedIn).

Une clé ne vit que dans le trousseau : jamais dans un fichier, un journal, une
adresse ni un argument de commande. On l'écrit en passant par l'entrée
standard de `security -i` (une ligne de commande visible dans `ps` le
temps d'un instant la montrerait à tout processus du compte), après avoir
vérifié son alphabet, ce qui empêche aussi toute injection dans cette ligne.

Le tableau de bord ne montre jamais une clé : seulement si elle est là, ses
quatre derniers caractères, et le résultat d'un essai auprès du service.
"""

import re
import subprocess

import httpx


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


def _run(*a, **k):
    """Seul passage vers `security` (les tests le remplacent : jamais le vrai trousseau)."""
    return subprocess.run(*a, **k)


def _service(nom):
    if nom not in SERVICES:
        raise CleRefusee("service inconnu : %s" % nom)
    return SERVICES[nom]


def lire(nom, executer=None):
    """La clé, ou None si elle n'est pas dans le trousseau."""
    r = (executer or _run)(["security", "find-generic-password", "-s", _service(nom)["trousseau"], "-w"],
                 capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def etat(nom, executer=None):
    cle = lire(nom, executer)
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


def _retirer_toutes(trousseau, executer):
    # Une entrée par passage : on retire jusqu'à ce qu'il n'y en ait plus (anciennes
    # entrées créées à la main avec un autre nom de compte comprises).
    for _ in range(10):
        if (executer or _run)(["security", "delete-generic-password", "-s", trousseau],
                    capture_output=True, text=True).returncode != 0:
            return


def enregistrer(nom, cle, executer=None, client=None):
    """Vérifie la forme, essaie la clé auprès du service, puis la range. Renvoie le message."""
    s = _service(nom)
    cle = (cle or "").strip()
    if not cle.startswith(s["prefixe"]):
        raise CleRefusee("une clé %s commence par « %s »" % (s["nom"], s["prefixe"]))
    if not _ALPHABET.fullmatch(cle):
        raise CleRefusee("caractères inattendus dans la clé (copie incomplète ?)")
    ok, msg = verifier(nom, cle, client)
    if ok is False:
        raise CleRefusee(msg)
    _retirer_toutes(s["trousseau"], executer)
    r = (executer or _run)(["security", "-i"], input='add-generic-password -U -a %s -s %s -w "%s"\n' % (COMPTE, s["trousseau"], cle),
                 capture_output=True, text=True)
    if r.returncode != 0 or lire(nom, executer) != cle:
        raise CleRefusee("le trousseau a refusé l'enregistrement")
    return "Clé %s enregistrée (…%s) : %s." % (s["nom"], cle[-4:], msg)


def retirer(nom, executer=None):
    _retirer_toutes(_service(nom)["trousseau"], executer)
    return "Clé %s retirée du trousseau." % _service(nom)["nom"]
