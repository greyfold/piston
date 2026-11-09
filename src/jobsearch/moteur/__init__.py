"""Moteur IA : le seul passage vers Claude.

Toutes les tâches passent par Claude Code (la commande `claude`), connecté au
compte Claude de la personne qui utilise la plateforme (décision du 06.11 :
chaque utilisateur se connecte à son propre abonnement, en version personnelle
comme en version distribuée ; aucune clé API). Il n'y a plus d'appel direct à
l'API Anthropic dans le code.

Une `Tache` est cadrée dans le code : consigne fixe, contexte, schéma de la
réponse, outils permis, modèle, effort et délai. `executer()` lance une session
`claude -p` sans tête et sans réglages de l'utilisateur (`--safe-mode` : ni
CLAUDE.md, ni plugins, ni crochets, ni serveurs MCP), sans aucun outil sauf
ceux que la tâche nomme, et rend le texte ou l'objet validé par le schéma.
Rien n'est écrit dans la base ici : c'est le rôle de l'appelant, après
validation.

Les coordonnées de la personne ne partent jamais : `ouvrir()` rend un moteur `Protege`,
qui les remplace par des balises avant l'envoi et les remet à la réception
(`confidentialite.py`), que la tâche tourne sur ce Mac ou dans la machine virtuelle.

La consigne et le contexte passent par un fichier temporaire lisible du seul
compte (`--system-prompt-file`), la demande par l'entrée standard : rien du
profil n'apparaît dans la liste des processus. Claude Code met le contexte en
cache d'une heure de lui-même : il reste identique d'une annonce à l'autre.
"""

import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from ..config import reglages

MODELE = "claude-opus-5-5"

# Les fournisseurs d'IA proposés à la connexion (accueil, checklist). Chacun se branche avec
# l'abonnement de la personne ; Claude d'abord, les autres viendront avec leur moteur.
FOURNISSEURS = [
    {"id": "claude", "nom": "Claude", "detail": "Avec ton abonnement Claude Pro ou Max",
     "commande": "jobsearch connexion"},
]


class MoteurImpossible(Exception):
    """La tâche n'a pas abouti. `bloquant` : inutile d'essayer la suivante
    (Claude Code absent ou déconnecté, abonnement épuisé)."""

    def __init__(self, message, bloquant=False):
        super().__init__(message)
        self.bloquant = bloquant


class LancementImpossible(MoteurImpossible):
    """Claude Code (ou ce dont une session a besoin) est introuvable."""

    def __init__(self, message):
        super().__init__(message, bloquant=True)


@dataclass
class Tache:
    nom: str                         # pour les journaux et les messages d'erreur
    consigne: str                    # règles fixes de la tâche
    contexte: str = ""               # profil, méthode : identique d'un appel à l'autre (cache)
    schema: type | None = None       # modèle pydantic de la réponse ; None : texte libre
    modele: str | None = None        # None : réglage moteur.modele
    effort: str = "high"
    outils: list = field(default_factory=list)        # outils intégrés permis ; vide : aucun
    autorises: list = field(default_factory=list)     # --allowedTools (outils MCP compris)
    mcp: dict | None = None          # configuration MCP propre à la tâche
    sans_reglages: bool = True       # --safe-mode : rien des réglages de l'utilisateur
    delai_s: int = 900
    schema_json: dict | None = None  # le schéma déjà converti (tâche reçue par le travailleur)
    masquer: bool = True             # coordonnées remplacées par des balises (Protege) ; reste sur le Mac

    def json_schema(self):
        if self.schema_json is not None:
            return self.schema_json
        return self.schema.model_json_schema() if self.schema is not None else None

    def vers_dict(self):
        """Ce qui traverse vers le travailleur : tout, le schéma en JSON Schema."""
        return {"nom": self.nom, "consigne": self.consigne, "contexte": self.contexte,
                "schema_json": self.json_schema(), "modele": self.modele, "effort": self.effort,
                "outils": list(self.outils), "autorises": list(self.autorises), "mcp": self.mcp,
                "sans_reglages": self.sans_reglages, "delai_s": self.delai_s}

    @classmethod
    def depuis_dict(cls, d):
        champs = {"nom", "consigne", "contexte", "schema_json", "modele", "effort", "outils", "autorises", "mcp",
                  "sans_reglages", "delai_s"}
        return cls(**{k: v for k, v in d.items() if k in champs})


@dataclass
class Resultat:
    texte: str
    objet: object = None
    brut: object = None              # la sortie structurée telle que Claude l'a rendue
    usage: dict = field(default_factory=dict)
    cout_usd: float | None = None
    session: str | None = None


def reglage(cle, defaut=None):
    return (reglages().get("moteur") or {}).get(cle, defaut)


def executable():
    """Chemin de `claude` : launchd ne connaît pas le PATH du terminal."""
    for chemin in (reglage("claude"), shutil.which("claude"), os.path.expanduser("~/.local/bin/claude"),
                   "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if chemin and os.path.exists(os.path.expanduser(chemin)):
            return os.path.expanduser(chemin)
    raise LancementImpossible("Claude Code introuvable (installer la commande « claude »)")


def environnement():
    """L'environnement d'une session : celui du processus, sans clé API. Une variable
    ANTHROPIC_API_KEY ferait passer Claude Code sur l'API au lieu de l'abonnement."""
    env = dict(os.environ)
    for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(v, None)
    return env


def connecte(executer=subprocess.run):
    """Vrai si Claude Code est connecté à un compte Claude (sinon les sessions s'arrêtent
    sur « Not logged in »)."""
    try:
        r = executer([executable(), "auth", "status"], capture_output=True, text=True, timeout=30,
                     env=environnement())
        return json.loads(r.stdout or "{}").get("loggedIn") is True
    except Exception:
        return False


def etat(executer=subprocess.run):
    """Ce que le tableau de bord montre de la connexion : installé, connecté, abonnement."""
    try:
        exe = executable()
    except LancementImpossible as e:
        return {"installe": False, "connecte": False, "abonnement": None, "message": str(e)}
    try:
        r = executer([exe, "auth", "status"], capture_output=True, text=True, timeout=30, env=environnement())
        d = json.loads(r.stdout or "{}")
    except Exception:
        d = {}
    ok = d.get("loggedIn") is True
    abonnement = d.get("subscriptionType") if d.get("authMethod") == "claude.ai" else None
    if not ok:
        message = "Claude Code n'est pas connecté : « Se connecter à Claude… » dans le menu de l'application"
    elif d.get("authMethod") != "claude.ai":
        message = "Claude Code est connecté autrement qu'avec un compte Claude (%s)" % (d.get("authMethod") or "?")
    else:
        message = "Connecté au compte Claude" + (" (abonnement %s)" % abonnement if abonnement else "")
    return {"installe": True, "connecte": ok and d.get("authMethod") == "claude.ai", "abonnement": abonnement,
            "message": message}


def commande(t, fichier_consigne):
    c = [executable(), "-p", "--output-format", "json", "--no-session-persistence",
         "--model", t.modele or reglage("modele", MODELE), "--effort", t.effort,
         "--system-prompt-file", fichier_consigne,
         "--tools", ",".join(t.outils), "--strict-mcp-config", "--permission-mode", "dontAsk"]
    if t.sans_reglages:
        c.append("--safe-mode")
    repli = reglage("repli")
    if repli:
        c += ["--fallback-model", repli]
    if t.json_schema() is not None:
        c += ["--json-schema", json.dumps(t.json_schema(), ensure_ascii=False)]
    if t.autorises:
        c += ["--allowedTools", ",".join(t.autorises)]
    if t.mcp:
        c += ["--mcp-config", json.dumps(t.mcp)]
    return c


EXECUTER = subprocess.run            # remplacé par un faux dans les tests


def _erreur(t, texte, code=None):
    bas = (texte or "").lower()
    if "not logged in" in bas or "/login" in bas or ("oauth" in bas and "expired" in bas):
        return MoteurImpossible("Claude Code n'est pas connecté : se connecter à son compte Claude", bloquant=True)
    if "usage limit" in bas or "limit reached" in bas or "rate limit" in bas:
        return MoteurImpossible("limite d'usage de l'abonnement Claude atteinte (%s)" % (texte or "").strip()[:120],
                                bloquant=True)
    return MoteurImpossible("%s : %s" % (t.nom, (texte or "sans message").strip()[:300]
                                         + (" (code %s)" % code if code not in (None, 0) else "")))


def executer(t, message, executer_=None):
    """Lance la tâche `t` sur `message` ; renvoie un Resultat ou lève MoteurImpossible."""
    executer_ = executer_ or EXECUTER
    with tempfile.TemporaryDirectory(prefix="jobsearch-") as dossier:
        fichier = os.path.join(dossier, "consigne.txt")
        fd = os.open(fichier, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(t.consigne + ("\n\n" + t.contexte if t.contexte else ""))
        try:
            r = executer_(commande(t, fichier), input=message, capture_output=True, text=True,
                          timeout=t.delai_s, cwd=dossier, env=environnement())
        except subprocess.TimeoutExpired as e:
            raise MoteurImpossible("%s : pas de réponse en %d s" % (t.nom, t.delai_s)) from e
        except OSError as e:
            raise LancementImpossible("Claude Code ne démarre pas : %s" % e) from e
    try:
        d = json.loads(r.stdout or "")
    except ValueError:
        raise _erreur(t, (r.stderr or r.stdout or "").strip(), r.returncode) from None
    if not isinstance(d, dict):
        raise _erreur(t, str(d), r.returncode)
    if d.get("is_error") or d.get("subtype") not in (None, "success"):
        raise _erreur(t, d.get("result") or d.get("subtype") or r.stderr, r.returncode)
    if d.get("stop_reason") == "refusal":
        raise MoteurImpossible("%s : refus du modèle" % t.nom)
    if d.get("stop_reason") == "max_tokens":
        raise MoteurImpossible("%s : réponse tronquée" % t.nom)
    brut = d.get("structured_output")
    if t.json_schema() is not None and brut is None:
        raise MoteurImpossible("%s : réponse illisible (pas de sortie structurée)" % t.nom)
    usage = {k: (d.get("usage") or {}).get(k) for k in
             ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    r = Resultat(texte=d.get("result") or "", brut=brut, usage=usage, cout_usd=d.get("total_cost_usd"),
                 session=d.get("session_id"))
    return valider(t, r)


def valider(t, r):
    """L'objet du schéma, à partir de la sortie structurée ; là où le schéma est connu."""
    if t.schema is not None:
        try:
            r.objet = t.schema.model_validate(r.brut)
        except Exception as e:
            raise MoteurImpossible("%s : réponse hors format (%s)" % (t.nom, str(e)[:200])) from e
    return r


class Moteur:
    """Ce que les modules reçoivent pour appeler Claude ; les tests le remplacent par FauxMoteur."""

    def executer(self, tache, message):
        return executer(tache, message)


class Protege:
    """Un moteur dont les tâches ne voient jamais les coordonnées de la personne : consigne,
    contexte et message partent masqués (balises), la réponse revient démasquée puis validée.
    Le masque se construit à chaque tâche, sur ce Mac : la machine virtuelle ne reçoit que
    des balises. Le reste (connecteurs, Gmail, sources du travailleur) passe tel quel."""

    def __init__(self, interne, masque=None):
        self.interne, self.masque = interne, masque

    def __getattr__(self, nom):
        if nom in ("interne", "masque"):
            raise AttributeError(nom)
        return getattr(self.interne, nom)

    def executer(self, tache, message):
        if not tache.masquer:
            return self.interne.executer(tache, message)
        from .. import confidentialite
        m = (self.masque or confidentialite.construire(documents=tache.contexte)).copie()
        consigne_, contexte_ = m.masquer(tache.consigne, contexte=True), m.masquer(tache.contexte, contexte=True)
        balises = m.balises_dans(consigne_ + "\n" + contexte_)
        if balises:                  # tiré du seul contexte fixe : la consigne reste la même d'un appel à l'autre (cache)
            consigne_ += "\n\n" + confidentialite.consigne(balises)
        masquee = dataclasses.replace(tache, consigne=consigne_, contexte=contexte_)
        r = self.interne.executer(masquee, m.masquer(message))
        brut = r.brut if r.brut is not None or not hasattr(r.objet, "model_dump") else r.objet.model_dump()
        r.texte, r.brut, r.objet = m.demasquer(r.texte), m.demasquer_objet(brut), None
        return valider(tache, r) if tache.schema is not None else r


def ouvrir(verifier=connecte):
    """Le moteur, après avoir vérifié que Claude Code est là et connecté : celui du
    travailleur (machine virtuelle) si les réglages le disent, sinon celui de ce Mac ;
    toujours derrière `Protege`, qui garde les coordonnées sur ce Mac."""
    from ..travailleur import client as travailleur
    if travailleur.distant():
        return Protege(travailleur.Distant.depuis_reglages().verifie())
    executable()
    if not verifier():
        raise MoteurImpossible("Claude Code n'est pas connecté : se connecter à son compte Claude", bloquant=True)
    return Protege(Moteur())


class FauxMoteur:
    """Pour les tests : rend les réponses données dans l'ordre (texte, objet ou exception),
    et garde chaque (tâche, message) reçu."""

    def __init__(self, reponses):
        self.reponses, self.appels = list(reponses), []

    def executer(self, tache, message):
        self.appels.append((tache, message))
        r = self.reponses.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, str):
            return Resultat(texte=r, usage={"input_tokens": 900, "output_tokens": 700})
        return Resultat(texte=r.model_dump_json(), objet=r, usage={"input_tokens": 900, "output_tokens": 700,
                                                                     "cache_read_input_tokens": 20000})
