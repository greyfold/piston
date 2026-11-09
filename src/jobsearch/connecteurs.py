"""Les connecteurs MCP : catalogue, activation, test.

Chaque connecteur a un rôle (courrier, source, navigateur) et un lieu :
- « mac » : il ne vit que sur le Mac (Apple Mail pilote l'application Mail,
  Claude in Chrome le Chrome de l'utilisateur) ;
- « compte » : un connecteur du compte Claude de l'utilisateur (claude.ai), joint
  par une session Claude Code ; il suit la connexion jusque dans la machine
  virtuelle.

Une tâche ne reçoit que les outils de lecture du connecteur qu'elle utilise :
`outils(nom)` les donne, préfixés comme Claude Code les nomme. Rien n'est jamais
envoyé, déplacé ni effacé par un connecteur.

L'activation vit dans le profil (`connecteurs.actifs`) ; sans profil, l'état par
défaut reprend les réglages d'avant (Apple Mail et Chrome actifs, Indeed selon
`indeed.actif`, Gmail inactif).
"""

from . import profil
from .config import reglages

CATALOGUE = {
    "apple-mail": {
        "nom": "Apple Mail", "role": "courrier", "lieu": "mac",
        "usage": "Réponses des employeurs (refus, entretiens), lues dans les comptes de Mail.",
        "activer": "Autoriser l'application à piloter Mail (Réglages Système › Confidentialité › Automatisation).",
    },
    "gmail": {
        "nom": "Gmail", "role": "courrier", "lieu": "compte", "prefixe": "mcp__claude_ai_Gmail",
        "outils": ["search_threads", "get_thread"],
        "usage": "Les mêmes réponses, lues dans Gmail par le connecteur de ton compte Claude.",
        "activer": "Sur claude.ai › Réglages › Connecteurs : activer Gmail avec ton compte Google.",
    },
    "indeed": {
        "nom": "Indeed", "role": "source", "lieu": "compte", "prefixe": "mcp__claude_ai_Indeed",
        "outils": ["search_jobs", "get_job_details"],
        "usage": "Recherches Indeed du passage du matin (le site refuse les scripts).",
        "activer": "Sur claude.ai › Réglages › Connecteurs : activer Indeed.",
    },
    "chrome": {
        "nom": "Claude in Chrome", "role": "navigateur", "lieu": "mac",
        "usage": "Formulaires des employeurs préremplis sans envoi, preuves Job-Room.",
        "activer": "Installer l'extension Claude in Chrome ; tu te connectes toi-même aux sites.",
    },
}

ROLES = {"courrier": "Courrier", "source": "Source d'annonces", "navigateur": "Navigateur"}
LIEUX = {"mac": "sur ce Mac", "compte": "compte Claude"}


def _defauts():
    out = {"apple-mail", "chrome"}
    if (reglages().get("indeed") or {}).get("actif"):
        out.add("indeed")
    return out


def actifs(p=None):
    p = profil.charger() if p is None else p
    liste = profil.lire(p, "connecteurs.actifs")
    return set(liste) if isinstance(liste, list) else _defauts()


def actif(nom, p=None):
    return nom in actifs(p)


def activer(nom, oui=True):
    if nom not in CATALOGUE:
        raise KeyError("connecteur inconnu : %s" % nom)
    p = dict(profil.charger())
    a = actifs(p)
    (a.add if oui else a.discard)(nom)
    p.setdefault("connecteurs", {})
    p["connecteurs"] = dict(p["connecteurs"], actifs=sorted(a))
    profil.ecrire(p)
    return a


def outils(nom):
    """Les outils de lecture du connecteur, tels que Claude Code les nomme."""
    c = CATALOGUE[nom]
    return ["%s__%s" % (c["prefixe"], o) for o in c.get("outils", [])]


def liste(p=None):
    a = actifs(p)
    return [dict(c, id=n, actif=n in a, role_libelle=ROLES[c["role"]], lieu_libelle=LIEUX[c["lieu"]])
            for n, c in CATALOGUE.items()]


# --- Test --------------------------------------------------------------------------------


def tester(nom):
    """(ok, message). Un connecteur du compte se teste en ouvrant une session Claude Code
    qui ne fait rien : son état se lit au démarrage (connecté, à réautoriser, absent)."""
    c = CATALOGUE[nom]
    if nom == "apple-mail":
        from .courrier import CourrierImpossible, commande_serveur
        try:
            commande_serveur()
        except CourrierImpossible as e:
            return False, str(e)
        return True, "serveur MCP Apple Mail présent ; Mail doit être ouvert au moment du passage"
    if nom == "chrome":
        return None, "à vérifier dans Chrome : l'extension Claude in Chrome doit être installée et connectée"
    from .travailleur import client as travailleur
    if travailleur.distant():
        try:
            return travailleur.Distant.depuis_reglages().connecteur(nom)
        except Exception as e:
            return False, str(e)
    return etat_connecteur(c)


def etat_connecteur(c, ouvrir=None):
    """État d'un connecteur claude.ai, lu dans le message d'initialisation d'une session."""
    from .moteur import LancementImpossible
    try:
        s = (ouvrir or _session_vide)(c)
    except LancementImpossible as e:
        return False, str(e)
    try:
        s.tour("Réponds seulement « ok ».", 120)
    except Exception as e:
        return False, "session Claude en échec : %s" % e
    finally:
        s.fermer()
    etat = next((v for k, v in (s.serveurs or {}).items() if c["nom"].lower() in (k or "").lower()), None)
    if etat == "connected":
        return True, "%s connecté au compte Claude" % c["nom"]
    if etat == "needs-auth":
        return False, "%s est à réautoriser sur claude.ai (Réglages, Connecteurs)" % c["nom"]
    if etat:
        return False, "%s : %s" % (c["nom"], etat)
    return False, "%s absent du compte Claude : %s" % (c["nom"], c["activer"])


def _session_vide(c):
    from .moteur import executable
    from .moteur.session import SessionClaude
    return SessionClaude([executable(), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                          "--verbose", "--no-session-persistence", "--tools", "", "--model", "haiku",
                          "--permission-mode", "dontAsk"])
