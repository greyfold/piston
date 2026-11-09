"""Accès au tableau de bord : libre depuis le Mac, jeton exigé depuis le réseau.

Le jeton est créé au premier lancement dans `data/jeton` (lisible par la personne
seul). Sur le téléphone, on le saisit une fois sur la page de connexion ; un
cookie le garde 90 jours. Toute requête POST d'une autre origine est refusée.

« Depuis le Mac » veut dire : connexion locale ET adresse demandée locale (localhost,
127.0.0.1, [::1]). Une page web malveillante peut faire pointer son propre nom de domaine sur
127.0.0.1 (« DNS rebinding ») : la connexion est alors locale, mais l'en-tête Host porte ce
nom ; sans le jeton, elle ne lit rien et ne fait rien.
"""

import hmac
import os
import secrets
from urllib.parse import urlparse

from ..config import DATA

LOCAL = {"127.0.0.1", "::1", "localhost", "testclient"}
HOTES_LOCAUX = {"127.0.0.1", "::1", "localhost", "testserver"}
COOKIE = "jobsearch_jeton"


def jeton():
    f = DATA / "jeton"
    if not f.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(24), encoding="utf-8")
        os.chmod(f, 0o600)
    return f.read_text(encoding="utf-8").strip()


def hote_demande(request):
    """Le nom d'hôte de l'en-tête Host, sans le port ni les crochets d'IPv6."""
    h = (request.headers.get("host") or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def depuis_le_mac(request):
    return (request.client.host if request.client else "") in LOCAL and hote_demande(request) in HOTES_LOCAUX


def autorise(request):
    if depuis_le_mac(request):
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), jeton())


def meme_origine(request):
    origine = request.headers.get("origin") or request.headers.get("referer")
    if not origine:
        return True
    return urlparse(origine).netloc == request.headers.get("host")
