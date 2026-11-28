"""Prévenir la personne : notification de l'ordinateur, et sur le téléphone par ntfy si activé."""

import httpx

from . import hote
from .config import reglages


def alerte_action(titre, texte, bouton="Ouvrir l'application"):
    """Une alerte dont le bouton ouvre l'application (le tableau de bord, et « Navigateur » pour le
    Chrome de la VM) : une notification simple ne fait rien d'utile quand on clique dessus sur Mac.
    L'ordinateur ne lance jamais Chrome. Lancée à part : la commande qui l'appelle ne l'attend pas."""
    return hote.alerte_action(titre, texte, bouton)


def _adresse(lien, nom="localhost"):
    w = reglages().get("web") or {}
    return "http://%s:%s/%s" % (nom, w.get("port", 8765), (lien or "").lstrip("/"))


def _hote_telephone():
    """Le nom de l'ordinateur sur le réseau local (<nom>.local), d'où le téléphone le joint."""
    return hote.nom_reseau()


def notifier(titre, texte, urgent=False, lien="/", telephone=True):
    """Notification sur l'ordinateur, par ce que le système fournit (`hote.notifier`) et, si un sujet
    ntfy est réglé et `telephone`, sur le téléphone (un clic ouvre `lien` du tableau de bord, sur le
    Wi-Fi de la maison). Ce qui demande une action passe aussi par `alerte_action`, dont le bouton
    ouvre l'application. Le réglage `notifications.macos` vaut pour tout système (son nom est resté)."""
    r = reglages().get("notifications") or {}
    envoyes = []
    if r.get("macos", True) and hote.notifier(titre, texte, urgent):
        envoyes.append(hote.SYSTEME)
    sujet = (r.get("ntfy_sujet") or "").strip()
    if sujet and telephone:
        message = {"topic": sujet, "title": titre, "message": texte[:3500],
                   "priority": 4 if urgent else 3, "tags": ["briefcase"]}
        nom = _hote_telephone()
        if nom:
            message["click"] = _adresse(lien, nom)
        try:
            # En JSON : les en-têtes HTTP ne portent pas les accents des titres.
            httpx.post(r.get("ntfy_serveur") or "https://ntfy.sh", json=message, timeout=10).raise_for_status()
            envoyes.append("ntfy")
        except httpx.HTTPError:
            pass
    return envoyes
