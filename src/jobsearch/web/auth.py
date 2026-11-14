"""Accès au tableau de bord : jeton exigé pour toute requête, même locale.

Le jeton est créé au démarrage du serveur dans `jeton` du dossier de données (lisible par la
personne seule), avant que le serveur n'écoute. Toute requête (hors /static et /connexion) le
porte, soit dans le cookie `jobsearch_jeton`, soit dans l'en-tête `X-Jobsearch-Jeton` ; la
comparaison se fait en temps constant.

- L'application Mac lit le fichier et pose le jeton sur sa vue web.
- Sur le téléphone, ou avec `uv run jobsearch web` en développement, on le colle une fois sur la
  page /connexion ; un cookie le garde 90 jours (`uv run jobsearch jeton` l'affiche).

Aucune dispense pour une connexion locale : la machine virtuelle atteint le 127.0.0.1 du Mac
(host.lima.internal) et peut forger l'en-tête Host, et une page web malveillante peut faire
pointer son nom de domaine sur 127.0.0.1 (« DNS rebinding »). Le jeton, que ni l'une ni l'autre
ne connaît, est la seule preuve d'accès.

`depuis_le_mac` ne donne aucun accès : c'est une restriction de plus, après le jeton, pour ce qui
ne doit pas traverser le réseau (saisie des clés, écran de la VM). Elle ne distingue pas la VM du
Mac ; la VM, elle, n'a pas le jeton.

Toute requête POST d'une autre origine est refusée, même avec le jeton.
"""

import hmac
import os
import secrets
from urllib.parse import urlparse

from .. import config

LOCAL = {"127.0.0.1", "::1", "localhost"}
HOTES_LOCAUX = {"127.0.0.1", "::1", "localhost"}
COOKIE = "jobsearch_jeton"
ENTETE = "x-jobsearch-jeton"


def chemin_jeton():
    return config.DATA / "jeton"         # lu à l'appel : les tests remplacent le dossier de données


def jeton():
    f = chemin_jeton()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:          # créé entre-temps par un autre appel
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as sortie:
                sortie.write(secrets.token_urlsafe(24))
    return f.read_text(encoding="utf-8").strip()


def jeton_valide(propose):
    """Comparaison en temps constant ; un jeton vide ou absent ne vaut jamais."""
    attendu = jeton()
    if not propose or not attendu:
        return False
    return hmac.compare_digest(propose.strip().encode("utf-8"), attendu.encode("utf-8"))


def hote_demande(request):
    """Le nom d'hôte de l'en-tête Host, sans le port ni les crochets d'IPv6."""
    h = (request.headers.get("host") or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def depuis_le_mac(request):
    """Connexion locale ET adresse demandée locale. Ne remplace jamais le jeton (voir plus haut)."""
    return (request.client.host if request.client else "") in LOCAL and hote_demande(request) in HOTES_LOCAUX


def autorise(request):
    return jeton_valide(request.cookies.get(COOKIE, "")) or jeton_valide(request.headers.get(ENTETE, ""))


def meme_origine(request):
    """Un POST d'une autre origine est refusé. Sans Origin ni Referer (l'application Mac, un client
    HTTP), il passe : il n'arrive ici qu'après `autorise`, donc avec le jeton."""
    origine = request.headers.get("origin") or request.headers.get("referer")
    if not origine:
        return True
    return urlparse(origine).netloc == request.headers.get("host")
