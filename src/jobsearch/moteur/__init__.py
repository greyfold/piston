"""Moteur IA : le seul passage vers l'IA.

Toutes les tâches passent par la commande de code d'un fournisseur, connectée au compte de la
personne qui utilise la plateforme (décision du 06.11 : chaque utilisateur se connecte à son
propre abonnement ; aucune clé API) : Claude Code par défaut, ou Codex (OpenAI), Antigravity CLI
(Google), Grok Build (xAI), au choix de la personne (`fournisseurs.py`). Il n'y a aucun appel
direct à l'API d'un fournisseur dans le code.

Une `Tache` est cadrée dans le code : consigne fixe, contexte, schéma de la
réponse, outils permis, modèle, effort et délai. `executer()` lance une session
`claude -p` (ou son équivalent chez le moteur choisi) sans tête et sans réglages de l'utilisateur (`--safe-mode` : ni
CLAUDE.md, ni plugins, ni crochets, ni serveurs MCP), sans aucun outil sauf
ceux que la tâche nomme, et rend le texte ou l'objet validé par le schéma.
Rien n'est écrit dans la base ici : c'est le rôle de l'appelant, après
validation.

Les coordonnées de la personne ne partent jamais : `ouvrir()` rend un moteur `Protege`,
qui les remplace par des balises avant l'envoi et les remet à la réception
(`confidentialite.py`). Le moteur ne tourne que dans la machine virtuelle.

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
    niveau: str = "principal"        # principal : évaluation, dossiers ; leger : lecture d'une page
    fournisseur: str | None = None   # claude, codex, gemini, grok ; None : celui des réglages

    def json_schema(self):
        if self.schema_json is not None:
            return self.schema_json
        return self.schema.model_json_schema() if self.schema is not None else None

    def vers_dict(self):
        """Ce qui traverse vers le travailleur : tout, le schéma en JSON Schema."""
        return {"nom": self.nom, "consigne": self.consigne, "contexte": self.contexte,
                "schema_json": self.json_schema(), "modele": self.modele, "effort": self.effort,
                "outils": list(self.outils), "autorises": list(self.autorises), "mcp": self.mcp,
                "sans_reglages": self.sans_reglages, "delai_s": self.delai_s, "niveau": self.niveau,
                "fournisseur": self.fournisseur}

    @classmethod
    def depuis_dict(cls, d):
        champs = {"nom", "consigne", "contexte", "schema_json", "modele", "effort", "outils", "autorises", "mcp",
                  "sans_reglages", "delai_s", "niveau", "fournisseur"}
        return cls(**{k: v for k, v in d.items() if k in champs})


@dataclass
class Resultat:
    texte: str
    objet: object = None
    brut: object = None              # la sortie structurée telle que le moteur l'a rendue
    usage: dict = field(default_factory=dict)
    cout_usd: float | None = None
    session: str | None = None
    modele: str | None = None        # le modèle qui a répondu, quand le moteur le dit


def reglage(cle, defaut=None):
    return (reglages().get("moteur") or {}).get(cle, defaut)


def fournisseur_actif():
    """Le moteur choisi par la personne (profil : ia.fournisseur) ; Claude par défaut."""
    f = reglage("fournisseur") or "claude"
    return f if f in _TOUS else "claude"


def fournisseur(fid=None):
    return _TOUS[fid if fid in _TOUS else fournisseur_actif()]


def dans_la_vm():
    """Vrai dans la machine virtuelle du travailleur (vm.py y pose JOBSEARCH_VM=1) : seul endroit
    où `claude` se lance. Sur le Mac, il ne se lance jamais."""
    return bool(os.environ.get("JOBSEARCH_VM"))


def executable():
    """Chemin de `claude`, dans la machine virtuelle : l'environnement d'un service n'a pas le PATH
    du terminal. Hors de la VM : LancementImpossible, le Mac ne lance jamais `claude`."""
    if not dans_la_vm():
        raise LancementImpossible("Claude Code ne tourne que dans la machine virtuelle : "
                                  "démarrer la machine virtuelle (travailleur.mode: distant)")
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
    # Ni télémétrie ni rapports d'erreur de Claude Code : rien de la recherche ne part ailleurs
    # que vers le compte Claude de la personne.
    env.setdefault("DISABLE_TELEMETRY", "1")
    env.setdefault("DISABLE_ERROR_REPORTING", "1")
    return env


def environnement_connecteurs():
    """Pour une session qui se sert d'un connecteur claude.ai (Indeed, Gmail) : Claude Code
    attend que les connecteurs soient branchés avant le premier tour. Par défaut il les
    branche en arrière-plan, et un premier tour parti trop tôt ne voit pas leurs outils :
    Indeed paraissait « absent » alors qu'il se connectait une demi-seconde plus tard
    (machine virtuelle, 09.11)."""
    return dict(environnement(), MCP_CONNECTION_NONBLOCKING="false")


def connecte(executer=subprocess.run):
    """Vrai si Claude Code est connecté à un compte Claude (sinon les sessions s'arrêtent
    sur « Not logged in »)."""
    try:
        r = executer([executable(), "auth", "status"], capture_output=True, text=True, timeout=30,
                     env=environnement())
        return json.loads(r.stdout or "{}").get("loggedIn") is True
    except Exception:
        return False


def etat(executer=subprocess.run, fournisseur_=None):
    """Ce que le tableau de bord montre de la connexion d'un moteur : installé, connecté, abonnement."""
    return fournisseur(fournisseur_).etat(executer)


def etats(executer=subprocess.run):
    """{fournisseur: état} de tous les moteurs (le travailleur, pour le Mac). Chaque état lance une
    commande du moteur (une demi-seconde à plus d'une seconde) : elles tournent en même temps, et le Mac
    attend la plus lente au lieu de leur somme."""
    from concurrent.futures import ThreadPoolExecutor

    def un(f):
        try:
            return f.etat(executer)
        except Exception as e:                      # un moteur en panne ne cache pas les autres
            return {"installe": False, "connecte": False, "abonnement": None, "message": str(e)[:200]}
    with ThreadPoolExecutor(max_workers=len(_TOUS) or 1) as fils:
        return dict(zip(_TOUS, fils.map(un, _TOUS.values())))


def etat_claude(executer=subprocess.run):
    """Claude Code : installé, connecté, abonnement."""
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


def modele_claude(t):
    if t.modele:
        return t.modele
    if t.niveau == "leger":
        return reglage("modele_lecture", "claude-sonnet-5-5")
    return reglage("modele", MODELE)


def commande(t, fichier_consigne):
    c = [executable(), "-p", "--output-format", "json", "--no-session-persistence",
         "--model", modele_claude(t), "--effort", t.effort,
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
    """Lance la tâche `t` sur `message`, avec le moteur qu'elle nomme (ou celui des réglages) ;
    renvoie un Resultat ou lève MoteurImpossible."""
    return fournisseur(t.fournisseur).executer(t, message, executer_)


def executer_claude(t, message, executer_=None):
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
                 session=d.get("session_id"), modele=modele_claude(t))
    return valider(t, r)


def valider(t, r):
    """L'objet du schéma, à partir de la sortie structurée ; là où le schéma est connu."""
    if t.schema is not None:
        try:
            r.objet = t.schema.model_validate(r.brut)
        except Exception as e:
            # Un moteur au schéma strict (Codex) rend null pour un champ facultatif : sans ces
            # null, le champ prend sa valeur par défaut.
            sans = _sans_nuls(r.brut)
            try:
                if sans == r.brut:
                    raise
                r.objet = t.schema.model_validate(sans)
            except Exception:
                raise MoteurImpossible("%s : réponse hors format (%s)" % (t.nom, str(e)[:200])) from e
    return r


def _sans_nuls(v):
    if isinstance(v, dict):
        return {k: _sans_nuls(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_sans_nuls(x) for x in v]
    return v


class Moteur:
    """Ce que les modules reçoivent pour appeler l'IA ; les tests le remplacent par FauxMoteur."""

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
        consigne_, contexte_ = m.masquer(tache.consigne, contexte=True), m.masquer(tache.contexte, contexte=True, neutraliser=True)
        balises = m.balises_dans(consigne_ + "\n" + contexte_)
        if balises:                  # tiré du seul contexte fixe : la consigne reste la même d'un appel à l'autre (cache)
            consigne_ += "\n\n" + confidentialite.consigne(balises)
        masquee = dataclasses.replace(tache, consigne=consigne_, contexte=contexte_)
        r = self.interne.executer(masquee, m.masquer(message, neutraliser=True))
        brut = r.brut if r.brut is not None or not hasattr(r.objet, "model_dump") else r.objet.model_dump()
        r.texte, r.brut, r.objet = m.demasquer(r.texte), m.demasquer_objet(brut), None
        return valider(tache, r) if tache.schema is not None else r


def ouvrir(verifier=None):
    """Le moteur choisi, après avoir vérifié qu'il est là et connecté : celui du travailleur
    (machine virtuelle), derrière `Protege`, qui garde les coordonnées sur le Mac. Le Mac ne lance
    jamais de moteur : hors de la VM et sans travailleur distant, c'est une erreur ; dans la VM,
    le moteur est local."""
    from ..travailleur import client as travailleur
    f = fournisseur()
    if travailleur.distant():
        return Protege(travailleur.Distant.depuis_reglages().verifie(f.id))
    f.executable()                   # LancementImpossible hors de la machine virtuelle
    ok = verifier() if verifier is not None else (connecte() if f.id == "claude" else f.etat().get("connecte"))
    if not ok:
        raise MoteurImpossible("%s n'est pas connecté : te connecter à ton compte %s" % (
            "Claude Code" if f.id == "claude" else f.nom, f.nom if f.id == "claude" else f.editeur), bloquant=True)
    return Protege(Moteur())


from .fournisseurs import TOUS as _TOUS  # noqa: E402  (après ce que les moteurs reprennent d'ici)

# Les fournisseurs d'IA proposés à la connexion (page IA, accueil, checklist), chacun avec
# l'abonnement de la personne.
FOURNISSEURS = [f.vue for f in _TOUS.values()]


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
