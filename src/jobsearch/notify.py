"""Prévenir la personne : notification macOS, et sur le téléphone par ntfy si activé."""

import subprocess

import httpx

from .config import reglages


def _osa(texte):
    return texte.replace("\\", "\\\\").replace('"', '\\"')


APPLICATION = "app.rechercheemploi.mac"     # CFBundleIdentifier de l'application (scripts/construire-app.sh)


def alerte_action(titre, texte, bouton="Ouvrir l'application"):
    """Fenêtre avec un bouton qui ouvre l'application (une notification macOS envoyée par
    osascript ne fait rien quand on clique dessus) : le tableau de bord, et « Navigateur » pour
    le Chrome de la VM. Le Mac ne lance jamais Chrome. Lancée à part : la commande qui l'appelle
    ne l'attend pas."""
    script = ('display alert "%s" message "%s" buttons {"Plus tard", "%s"} default button 2 giving up after 900\n'
              'if button returned of result is "%s" then tell application id "%s" to activate'
              % (_osa(titre), _osa(texte[:400]), _osa(bouton), _osa(bouton), APPLICATION))
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except OSError:
        return False


def _adresse(lien, hote="localhost"):
    w = reglages().get("web") or {}
    return "http://%s:%s/%s" % (hote, w.get("port", 8765), (lien or "").lstrip("/"))


def _hote_telephone():
    """Le nom du Mac sur le réseau local (<nom>.local), d'où le téléphone le joint."""
    try:
        r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() + ".local" if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        return None


def notifier(titre, texte, urgent=False, lien="/", telephone=True):
    """Notification sur le Mac, par ce que macOS fournit (osascript) et, si un sujet ntfy est
    réglé et `telephone`, sur le téléphone (un clic ouvre `lien` du tableau de bord, sur le Wi-Fi
    de la maison). Sur le Mac, un clic sur la notification n'ouvre rien d'utile : ce qui demande
    une action passe aussi par `alerte_action`, dont le bouton ouvre l'application."""
    r = reglages().get("notifications") or {}
    envoyes = []
    if r.get("macos", True):
        try:
            subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s"%s'
                            % (_osa(texte[:900]), _osa(titre), ' sound name "Basso"' if urgent else "")],
                           check=False, timeout=10, capture_output=True)
            envoyes.append("macos")
        except (OSError, subprocess.SubprocessError):
            pass
    sujet = (r.get("ntfy_sujet") or "").strip()
    if sujet and telephone:
        message = {"topic": sujet, "title": titre, "message": texte[:3500],
                   "priority": 4 if urgent else 3, "tags": ["briefcase"]}
        hote = _hote_telephone()
        if hote:
            message["click"] = _adresse(lien, hote)
        try:
            # En JSON : les en-têtes HTTP ne portent pas les accents des titres.
            httpx.post(r.get("ntfy_serveur") or "https://ntfy.sh", json=message, timeout=10).raise_for_status()
            envoyes.append("ntfy")
        except httpx.HTTPError:
            pass
    return envoyes
