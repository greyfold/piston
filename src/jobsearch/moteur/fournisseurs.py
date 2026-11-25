"""Les moteurs d'IA proposés : Claude Code, Codex (OpenAI), Antigravity CLI (Google), Grok Build (xAI).

Chacun est la commande de code du fournisseur, connectée à l'abonnement de la personne (aucune
clé API), et ne tourne que dans la machine virtuelle. L'application lui demande la même chose
qu'à Claude Code : une tâche cadrée (consigne, contexte, schéma de la réponse), sans aucun
outil sauf ceux que la tâche nomme (recherche web pour l'examen d'une source), ou une session
dans le Chrome de la VM, avec les deux seuls serveurs MCP de l'application (mcp_vm.py).

Ce que chaque classe sait de son moteur :
- `infos` : nom, éditeur, abonnement, mode de connexion, ce que la page Confidentialité en dit ;
- `executable()`, `environnement()` : sans clé API, sans télémétrie ; Codex, Antigravity et Grok
  ont chacun leur propre dossier personnel dans la VM (`~/moteurs/<id>`), à part de Claude
  Code et de ses réglages ;
- `executer(t, message, executer_)` : la commande sans tête, et la lecture de sa réponse ;
- `etat()` : installé, connecté ;
- `connexion()` / `deconnexion()` : le script lancé dans la VM ;
- `session(consigne, tache, donnees, dossier)` : la session Chrome (commande, environnement, dossier
  de travail, ce qui part sur son entrée standard).

Rien de la personne n'y passe autrement que par la tâche : consigne et contexte dans un fichier
du seul compte, demande sur l'entrée standard (rien dans la liste des processus).
"""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PREFIXE_OUTILS = "~/outils-ia"         # Codex (npm), Antigravity CLI, Grok Build, dans la VM
DOSSIER_MOTEURS = "~/moteurs"          # un dossier personnel par moteur, dans la VM

# Versions figées, relues avant chaque changement (comme le serveur MCP de Chrome).
CODEX = "@openai/codex@0.171.0"
GROK = "1.0.92"
# Antigravity CLI : le binaire de Google pour Linux arm64 (la VM), avec l'empreinte de son manifeste.
ANTIGRAVITY = {"version": "1.5.16",
               "url": "https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.16-5250855658717184/"
                      "linux-arm/cli_linux_arm64.tar.gz",
               "sha512": "23c193151499a4986fd5fa0d8132665cc8ffbad6cc0b3ff4fd53a2cb68abb76b149a992b836d53063443cd89"
                         "add335f14d8134fb02abdcecbf6e1eaef6c53d33"}

OUTILS_WEB = ("WebSearch", "WebFetch")


def _moteur():
    from .. import moteur                # au moment de l'appel : les tests remplacent ses attributs
    return moteur


def _sans(env, *cles):
    for c in cles:
        env.pop(c, None)
    return env


def _consigne(t):
    return t.consigne + ("\n\n" + t.contexte if t.contexte else "")


def _ecrire(chemin, texte):
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(texte)
    return str(chemin)


def serveur_web():
    """Le serveur MCP `web` de l'application (`lire_page`), pour un moteur qui ne lit pas les pages."""
    return ("web", sys.executable, ["-m", "jobsearch.mcp_vm", "web"])


def serveurs_session(tache, donnees):
    """Les deux serveurs MCP d'une session Chrome : (nom, commande, arguments)."""
    py = sys.executable
    return [("chrome", py, ["-m", "jobsearch.mcp_vm", "chrome"]),
            ("session", py, ["-m", "jobsearch.mcp_vm", "session", "--tache", str(tache), "--donnees", str(donnees)])]


def json_dans(texte):
    """L'objet JSON d'une réponse en texte : tel quel, dans un bloc ``` ou entre accolades."""
    texte = (texte or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", texte, re.S)
    for essai in ([m.group(1)] if m else []) + [texte, texte[texte.find("{"):texte.rfind("}") + 1]]:
        try:
            return json.loads(essai)
        except ValueError:
            continue
    return None


def schema_strict(s):
    """Le schéma au format strict qu'exige Codex : chaque objet fermé, chaque propriété requise
    (une propriété facultative accepte null), sans valeurs par défaut."""
    s = copy.deepcopy(s)

    def visiter(n):
        if isinstance(n, list):
            for v in n:
                visiter(v)
            return
        if not isinstance(n, dict):
            return
        n.pop("default", None)
        props = n.get("properties")
        if isinstance(props, dict):
            requis = set(n.get("required") or [])
            for k in list(props):
                if k not in requis:
                    props[k] = {"anyOf": [props[k], {"type": "null"}]}
            n["required"] = list(props)
            n["additionalProperties"] = False
        for v in n.values():
            visiter(v)
    visiter(s)
    return s


class Fournisseur:
    id = nom = editeur = ""
    infos = {}
    connexion_mode = "code"              # code : collé dans l'application ; appareil : tapé chez le fournisseur
    invites = []                         # (texte affiché, réponse) que la connexion envoie seule
    pty = False                          # connexion dans un pseudo-terminal (Entrée = « \r »)
    # Réglages propres au moteur, établis par le banc d'essai (moteur/essais.py) :
    lit_les_pages = True                 # a son propre outil pour lire une page ; sinon `lire_page` (mcp_vm web)

    def __init__(self):
        self.vue = dict(self.infos, id=self.id, nom=self.nom, editeur=self.editeur, connexion=self.connexion_mode,
                        commande="jobsearch connexion --fournisseur %s" % self.id)

    # --- dans la VM ---
    def maison(self):
        return Path(os.path.expanduser(DOSSIER_MOTEURS)) / self.id

    def chemins(self):
        return []

    def executable(self):
        m = _moteur()
        if not m.dans_la_vm():
            raise m.LancementImpossible("%s ne tourne que dans la machine virtuelle : démarrer la machine "
                                        "virtuelle (travailleur.mode: distant)" % self.nom)
        for chemin in [m.reglage(self.id)] + self.chemins():
            if chemin and os.path.exists(os.path.expanduser(chemin)):
                return os.path.expanduser(chemin)
        raise m.LancementImpossible("%s introuvable dans la machine virtuelle (redémarre-la pour l'installer)" % self.nom)

    def environnement(self):
        env = dict(os.environ)
        maison = self.maison()
        maison.mkdir(parents=True, exist_ok=True)
        env.update(HOME=str(maison), DISABLE_TELEMETRY="1", DISABLE_ERROR_REPORTING="1", DO_NOT_TRACK="1",
                   NO_COLOR="1")
        return _sans(env, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
                     "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE_VERTEXAI",
                     "GOOGLE_CLOUD_PROJECT", "XAI_API_KEY", "GROK_API_KEY")

    def modele(self, t):
        """Le modèle choisi dans les réglages (moteur.modeles.<id>.principal|leger) ; None : celui
        que le moteur prend par défaut."""
        m = _moteur()
        choix = ((m.reglage("modeles") or {}).get(self.id) or {})
        return choix.get(t.niveau) or choix.get("principal")

    def web(self, t):
        return any(o in OUTILS_WEB for o in t.outils)

    def lancer(self, t, commande, message, dossier, env, executer_):
        m = _moteur()
        try:
            return (executer_ or m.EXECUTER)(commande, input=message, capture_output=True, text=True,
                                             timeout=t.delai_s, cwd=dossier, env=env)
        except subprocess.TimeoutExpired as e:
            raise m.MoteurImpossible("%s : pas de réponse en %d s" % (t.nom, t.delai_s)) from e
        except OSError as e:
            raise m.LancementImpossible("%s ne démarre pas : %s" % (self.nom, e)) from e

    def erreur(self, t, texte, code=None):
        m = _moteur()
        bas = (texte or "").lower()
        if any(x in bas for x in ("not logged in", "not signed in", "/login", "login required", "unauthorized",
                                   "401", "please log in", "authenticat", "authorization is required", "sign in")):
            return m.MoteurImpossible("%s n'est pas connecté : te connecter à ton compte %s" % (self.nom, self.editeur),
                                      bloquant=True)
        if any(x in bas for x in ("usage limit", "limit reached", "rate limit", "quota", "429", "resource_exhausted")):
            return m.MoteurImpossible("limite d'usage de l'abonnement %s atteinte (%s)" % (self.editeur, (texte or "").strip()[:120]),
                                      bloquant=True)
        return m.MoteurImpossible("%s : %s" % (t.nom, (texte or "sans message").strip()[:300]
                                               + (" (code %s)" % code if code not in (None, 0) else "")))

    def refuser_mcp(self, t):
        if t.mcp:
            m = _moteur()
            raise m.MoteurImpossible("%s : cette tâche demande un connecteur du compte Claude" % t.nom, bloquant=True)

    def resultat(self, t, texte, brut=None, usage=None, session=None, modele=None):
        m = _moteur()
        if t.json_schema() is not None:
            brut = brut if brut is not None else json_dans(texte)
            if brut is None:
                raise m.MoteurImpossible("%s : réponse illisible (pas de JSON)" % t.nom)
        return m.valider(t, m.Resultat(texte=texte or "", brut=brut, usage=usage or {}, session=session, modele=modele))

    # --- connexion, dans la VM (vm.py lance ces scripts par `limactl shell`) ---
    def script_env(self):
        maison = "%s/%s" % (DOSSIER_MOTEURS.replace("~", "$HOME"), self.id)
        return ('export PATH="$HOME/.local/bin:%s/bin:/opt/node/bin:$PATH" HOME="%s" DISABLE_TELEMETRY=1 '
                'DISABLE_ERROR_REPORTING=1 NO_COLOR=1; mkdir -p "$HOME"; '
                'unset OPENAI_API_KEY CODEX_API_KEY GEMINI_API_KEY GOOGLE_API_KEY XAI_API_KEY GROK_API_KEY; '
                % (PREFIXE_OUTILS.replace("~", "$HOME"), maison))


class Claude(Fournisseur):
    id, nom, editeur = "claude", "Claude", "Anthropic"
    infos = {"detail": "Avec ton abonnement Claude Pro ou Max", "abonnement": "Claude Pro ou Max",
             "commande_vm": "claude", "site": "claude.ai",
             "entrainement": "Dans les réglages de confidentialité de ton compte Claude, l'option « Aider à "
                             "améliorer Claude » décide si tes conversations servent à entraîner les modèles.",
             "lien_entrainement": "https://claude.ai/settings/data-privacy-controls",
             "lien_conditions": "https://www.anthropic.com/legal/privacy"}

    def executable(self):
        return _moteur().executable()

    def environnement(self):
        return _moteur().environnement()

    def executer(self, t, message, executer_=None):
        m = _moteur()
        return m.executer_claude(t, message, executer_)

    def etat(self, executer=subprocess.run):
        return _moteur().etat_claude(executer)

    def connexion(self):
        return ('export PATH="$HOME/.local/bin:$PATH"; pkill -f "^claude.auth.login" || true; '
                'exec claude auth login --claudeai')

    def deconnexion(self):
        return 'export PATH="$HOME/.local/bin:$PATH"; pkill -f "^claude.auth.login" || true; exec claude auth logout'

    def session(self, consigne, tache, donnees, dossier):
        m = _moteur()
        mcp = {"mcpServers": {nom: {"command": c, "args": a} for nom, c, a in serveurs_session(tache, donnees)}}
        modele = m.reglage("modele")
        commande = [m.executable(), "-p", consigne, *(["--model", modele] if modele else []),
                    "--mcp-config", json.dumps(mcp), "--strict-mcp-config", "--tools", "",
                    "--allowedTools", "mcp__chrome,mcp__session",
                    "--permission-mode", "dontAsk", "--output-format", "stream-json", "--verbose"]
        return commande, m.environnement(), None, None


class Codex(Fournisseur):
    id, nom, editeur = "codex", "Codex", "OpenAI"
    connexion_mode = "appareil"
    infos = {"detail": "Avec ton abonnement ChatGPT Plus, Pro ou Business", "abonnement": "ChatGPT Plus, Pro ou Business",
             "commande_vm": "codex", "site": "chatgpt.com",
             "entrainement": "Dans les réglages de ChatGPT (Contrôle des données), l'option « Améliorer le modèle "
                             "pour tous » décide si ce que tu envoies sert à entraîner les modèles d'OpenAI.",
             "lien_entrainement": "https://chatgpt.com/#settings/DataControls",
             "lien_conditions": "https://openai.com/policies/privacy-policy/"}

    # Fonctions de Codex coupées : rien qui lance une commande, navigue, agit sur l'écran ou garde
    # une mémoire. Une tâche n'a que la recherche web, si elle la demande.
    FONCTIONS_COUPEES = ["shell_tool", "browser_use", "computer_use", "image_generation", "apps", "plugins", "hooks",
                         "multi_agent", "goals", "tool_suggest", "skill_search", "code_mode_host", "memories",
                         "sleep_tool", "in_app_browser"]

    def chemins(self):
        return ["%s/bin/codex" % PREFIXE_OUTILS]

    def environnement(self):
        env = super().environnement()
        env["CODEX_HOME"] = str(self.maison() / ".codex")
        return env

    def options(self, consigne_fichier, effort="high", web=False):
        c = ["--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--color", "never", "--json",
             "-s", "read-only",
             "-c", 'model_reasoning_effort="%s"' % effort,
             "-c", 'web_search="%s"' % ("live" if web else "disabled"),
             "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
             "-c", "check_for_update_on_startup=false", "-c", "project_doc_max_bytes=0",
             "-c", 'approval_policy="never"']
        if consigne_fichier:
            c += ["-c", "model_instructions_file=%s" % json.dumps(consigne_fichier)]
        for f in self.FONCTIONS_COUPEES:
            c += ["--disable", f]
        return c

    def commande(self, t, dossier, consigne_fichier, schema_fichier):
        c = [self.executable(), "exec", "--ephemeral", "-C", dossier] + self.options(
            consigne_fichier, {"max": "high"}.get(t.effort, t.effort), self.web(t))
        if self.modele(t):
            c += ["-m", self.modele(t)]
        if schema_fichier:
            c += ["--output-schema", schema_fichier]
        return c + ["-"]

    @staticmethod
    def lire(sortie):
        """(texte, usage, erreur, fini) des événements JSON de `codex exec --json`."""
        texte, usage, erreur, fini = "", {}, None, False
        for ligne in (sortie or "").splitlines():
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            genre = e.get("type")
            if genre == "item.completed" and (e.get("item") or {}).get("type") == "agent_message":
                texte = e["item"].get("text") or ""
            elif genre == "turn.completed":
                fini, u = True, e.get("usage") or {}
                usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                         "cache_read_input_tokens": u.get("cached_input_tokens")}
            elif genre == "turn.failed":
                erreur = ((e.get("error") or {}).get("message")) or "échec du tour"
            elif genre == "error":
                erreur = e.get("message") or erreur
        return texte, usage, erreur, fini

    def executer(self, t, message, executer_=None):
        self.refuser_mcp(t)
        with tempfile.TemporaryDirectory(prefix="jobsearch-") as dossier:
            consigne = _ecrire(os.path.join(dossier, "consigne.txt"), _consigne(t))
            schema = None
            if t.json_schema() is not None:
                schema = _ecrire(os.path.join(dossier, "schema.json"), json.dumps(schema_strict(t.json_schema())))
            r = self.lancer(t, self.commande(t, dossier, consigne, schema), message, dossier,
                            self.environnement(), executer_)
        texte, usage, erreur, fini = self.lire(r.stdout)
        if not fini:
            raise self.erreur(t, erreur or r.stderr or r.stdout, r.returncode)
        return self.resultat(t, texte, usage=usage, modele=self.modele(t))

    def etat(self, executer=subprocess.run):
        try:
            exe = self.executable()
        except _moteur().LancementImpossible as e:
            return {"installe": False, "connecte": False, "abonnement": None, "message": str(e)}
        try:
            r = executer([exe, "login", "status"], capture_output=True, text=True, timeout=30, env=self.environnement())
            sortie = (r.stdout or "") + (r.stderr or "")
            ok = r.returncode == 0 and "chatgpt" in sortie.lower()
        except Exception:
            ok, sortie = False, ""
        if ok:
            message = "Connecté au compte ChatGPT"
        elif "api key" in sortie.lower():
            message = "Codex est connecté avec une clé API, pas avec un compte ChatGPT"
        else:
            message = "Codex n'est pas connecté : « Se connecter » sur la page IA"
        return {"installe": True, "connecte": ok, "abonnement": None, "message": message}

    def connexion(self):
        return self.script_env() + 'export CODEX_HOME="$HOME/.codex"; mkdir -p "$CODEX_HOME"; exec codex login --device-auth'

    def deconnexion(self):
        return self.script_env() + 'export CODEX_HOME="$HOME/.codex"; exec codex logout'

    def session(self, consigne, tache, donnees, dossier):
        c = [self.executable(), "exec", "--ephemeral", "-C", str(dossier)] + self.options(None)
        for nom, commande, args in serveurs_session(tache, donnees):
            c += ["-c", "mcp_servers.%s={command=%s, args=%s}" % (nom, json.dumps(commande), json.dumps(args))]
        modele = ((_moteur().reglage("modeles") or {}).get(self.id) or {}).get("principal")
        return c + (["-m", modele] if modele else []) + [consigne], self.environnement(), None, None


class Antigravity(Fournisseur):
    """Antigravity CLI (`agy`), l'agent de Google qui remplace Gemini CLI pour les abonnements Google.

    Il n'a pas d'option pour remplacer sa consigne : elle va dans `AGENTS.md` du dossier de travail
    (une « règle », 24 Ko au plus), le reste (contexte trop long, demande) part sur l'entrée standard,
    au format stream-json. Ses outils se coupent par les règles d'interdiction de ses réglages,
    posés pour la seule exécution (`--settings`) ; une action qu'il faudrait approuver est refusée
    d'office en mode sans tête. Ses identifiants restent dans son dossier personnel à part."""
    id, nom, editeur = "antigravity", "Antigravity", "Google"
    infos = {"detail": "Avec ton abonnement Google AI Pro ou Ultra", "abonnement": "Google AI Pro ou Ultra",
             "commande_vm": "agy", "site": "antigravity.google",
             "entrainement": "Antigravity garde avec ton compte Google ton choix de partager tes échanges pour "
                             "améliorer les modèles (« Enable Telemetry » dans ses réglages) : l'application ne peut "
                             "pas le régler pour toi, vérifie-le dans Antigravity.",
             "lien_entrainement": "https://antigravity.google/docs/settings?tab=cli",
             "lien_conditions": "https://policies.google.com/privacy"}

    # Toutes les actions de l'agent sont interdites, sauf celles que la tâche demande.
    ACTIONS = ["command(*)", "write_file(*)", "read_file(*)", "read_url(*)", "execute_url(*)", "download_url(*)"]
    REGLE_MAX = 20_000            # octets d'AGENTS.md (Antigravity tronque une règle au-delà de 24 000)
    # Banc d'essai du 25.11.2026 (agy 1.5.16) : ses seuls outils web sont `search_web` ; aucun ne lit une
    # page, même avec read_url permis. La lecture passe par `lire_page`, le serveur MCP de l'application.
    lit_les_pages = False

    def chemins(self):
        return ["%s/antigravity/agy" % PREFIXE_OUTILS]

    def environnement(self):
        env = super().environnement()
        env["ANTIGRAVITY_SENTRY_SAMPLE_RATE"] = "0"         # pas de rapports d'erreur
        return env

    def reglages(self, web=False, serveurs=()):
        """Les réglages d'une seule exécution (jamais enregistrés par Antigravity)."""
        refus = [a for a in self.ACTIONS if not (web and a == "read_url(*)")]
        permis = (["read_url(*)"] if web else []) + ["mcp(%s/*)" % nom for nom, _, _ in serveurs]
        if not serveurs:
            refus.append("mcp(*)")
        return {"toolPermission": "request-review", "disableSlashCommands": True, "allowNonWorkspaceAccess": False,
                "permissions": {"allow": permis, "deny": refus}}

    def preparer(self, dossier, web=False, serveurs=()):
        """Le dossier de travail : ses réglages, ses serveurs MCP (`.agents/mcp_config.json`) ; et
        les réglages de l'utilisateur à part : aucun greffon (essaim d'agents) actif."""
        config = self.maison() / ".gemini" / "config"
        config.mkdir(parents=True, exist_ok=True)
        _ecrire(config / "config.json", json.dumps({"plugins": {"swarm": {"enabled": False}}}))
        if serveurs:
            (Path(dossier) / ".agents").mkdir(exist_ok=True)
            _ecrire(Path(dossier) / ".agents" / "mcp_config.json", json.dumps(
                {"mcpServers": {nom: {"command": c, "args": a} for nom, c, a in serveurs}}))
        return _ecrire(Path(dossier) / "reglages.json", json.dumps(self.reglages(web, serveurs)))

    @staticmethod
    def entree(texte):
        return json.dumps({"event": "user", "message": {"content": texte}}, ensure_ascii=False) + "\n"

    def commande(self, dossier, reglages, effort=None, modele=None, schema=None):
        c = [self.executable(), "--input-format", "stream-json", "--output-format", "stream-json",
             "--settings", reglages, "--disable-slash-commands", "--print-timeout", "0s"]
        if effort:
            c += ["--effort", effort]
        if modele:
            c += ["--model", modele]
        if schema:
            c += ["--json-schema", schema]
        return c + ["-p="]           # -p sans texte : la demande arrive sur l'entrée standard

    @staticmethod
    def lire(sortie):
        """Le dernier événement « result » de la sortie stream-json."""
        dernier = None
        for ligne in (sortie or "").splitlines():
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("event") == "result":
                dernier = e.get("result") or {}
        return dernier

    def executer(self, t, message, executer_=None):
        self.refuser_mcp(t)
        regle = t.consigne + ("\n\n" + t.contexte if t.contexte else "")
        demande = message
        if len(regle.encode("utf-8")) > self.REGLE_MAX:          # trop long pour une règle : le contexte part avec la demande
            regle, demande = t.consigne, t.contexte + "\n\n---\n\n" + message
        with tempfile.TemporaryDirectory(prefix="jobsearch-") as dossier:
            _ecrire(os.path.join(dossier, "AGENTS.md"), regle)
            schema = None
            if t.json_schema() is not None:
                schema = _ecrire(os.path.join(dossier, "schema.json"), json.dumps(t.json_schema()))
            serveurs = [serveur_web()] if self.web(t) and not self.lit_les_pages else []
            reglages = self.preparer(dossier, self.web(t), serveurs)
            r = self.lancer(t, self.commande(dossier, reglages, t.effort, self.modele(t), schema), self.entree(demande),
                            dossier, self.environnement(), executer_)
        d = self.lire(r.stdout)
        if d is None:
            raise self.erreur(t, r.stderr or r.stdout, r.returncode)
        if d.get("status") != "SUCCESS":
            raise self.erreur(t, d.get("error") or d.get("status") or r.stderr, r.returncode)
        u = d.get("usage") or {}
        usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                 "cache_read_input_tokens": u.get("cache_read_tokens")}
        return self.resultat(t, d.get("response") or "", brut=d.get("structured_output"), usage=usage,
                             session=d.get("conversation_id") or None, modele=self.modele(t))

    def etat(self, executer=subprocess.run):
        """`agy models` répond tout de suite, en erreur tant que personne n'est connecté."""
        try:
            exe = self.executable()
        except _moteur().LancementImpossible as e:
            return {"installe": False, "connecte": False, "abonnement": None, "message": str(e)}
        try:
            r = executer([exe, "models"], capture_output=True, text=True, timeout=30, env=self.environnement(),
                         cwd=str(self.maison()), stdin=subprocess.DEVNULL)
            ok = r.returncode == 0
        except Exception:
            ok = False
        return {"installe": True, "connecte": ok, "abonnement": None,
                "message": "Connecté au compte Google" if ok else
                           "Antigravity n'est pas connecté : « Se connecter » sur la page IA"}

    # Ce que la connexion doit répondre seule : choisir « Google OAuth » dans son premier menu.
    invites = [("Select login method", "\r")]
    pty = True                   # le code collé arrive dans un pseudo-terminal : Entrée y est « \r »

    def connexion(self):
        """Antigravity ne se connecte que dans son interface : elle tourne dans un pseudo-terminal
        (`script`), et s'arrête dès que `agy models` répond (la connexion est faite)."""
        return ('A="%s/antigravity/agy"; ' % PREFIXE_OUTILS.replace("~", "$HOME")) + self.script_env() + (
            'export ANTIGRAVITY_SENTRY_SAMPLE_RATE=0 AGY_CLI_HIDE_LOGO=1\n'
            'cd "$HOME"; exec 3<&0\n'
            'script -qfec "stty cols 2000 rows 50; exec \\"$A\\"" /dev/null <&3 &\n'
            'P=$!\n'
            'while kill -0 $P 2>/dev/null; do\n'
            '  if "$A" models </dev/null >/dev/null 2>&1; then sleep 1; pkill -P $P; kill $P 2>/dev/null; break; fi\n'
            '  sleep 2\n'
            'done\n'
            'wait $P 2>/dev/null\n'
            '"$A" models </dev/null >/dev/null 2>&1 && echo "Connecté."\n')

    def deconnexion(self):
        """Pas de commande de déconnexion hors de son interface : son dossier personnel, où sont ses
        identifiants et ses conversations, est effacé."""
        return self.script_env() + ('cd /; case "$HOME" in */moteurs/antigravity) rm -rf -- "$HOME" ;; esac; '
                                    'echo "Déconnecté."')

    def session(self, consigne, tache, donnees, dossier):
        modele = ((_moteur().reglage("modeles") or {}).get(self.id) or {}).get("principal")
        reglages = self.preparer(dossier, False, serveurs_session(tache, donnees))
        return (self.commande(dossier, reglages, None, modele), self.environnement(), str(dossier),
                self.entree(consigne))


class Grok(Fournisseur):
    id, nom, editeur = "grok", "Grok", "xAI"
    connexion_mode = "appareil"
    infos = {"detail": "Avec ton abonnement SuperGrok ou X Premium+", "abonnement": "SuperGrok ou X Premium+",
             "commande_vm": "grok", "site": "grok.com",
             "entrainement": "Dans les réglages de Grok (Contrôles des données), l'option « Améliorer le modèle » "
                             "décide si tes échanges servent à entraîner les modèles de xAI.",
             "lien_entrainement": "https://grok.com/?_s=data",
             "lien_conditions": "https://x.ai/legal/privacy-policy"}

    OUTILS_COUPES = ["run_terminal_cmd", "read_file", "grep", "list_dir", "write", "search_replace", "task", "Agent"]

    def chemins(self):
        return ["%s/grok/bin/grok" % PREFIXE_OUTILS]

    def environnement(self):
        env = super().environnement()
        env["GROK_HOME"] = str(self.maison() / ".grok")
        return env

    def options(self, web=False):
        coupes = self.OUTILS_COUPES + ([] if web else ["web_search", "web_fetch"])
        c = ["--permission-mode", "dontAsk", "--disallowed-tools", ",".join(coupes),
             "--no-subagents", "--no-memory", "--no-plan", "--output-format", "streaming-messages-json"]
        return c + ([] if web else ["--disable-web-search"])

    def commande(self, t, dossier, consigne_fichier):
        web = self.web(t)
        c = [self.executable(), "-p", "--system-prompt-file", consigne_fichier, "--cwd", dossier,
             "--sandbox", "read-only" if web else "strict",
             "--tools", "web_search,web_fetch" if web else "", "--effort", t.effort] + self.options(web)
        if self.modele(t):
            c += ["-m", self.modele(t)]
        if t.json_schema() is not None:
            c += ["--json-schema", json.dumps(t.json_schema(), ensure_ascii=False)]
        return c

    @staticmethod
    def lire(sortie):
        """La dernière ligne « result » de la sortie (même forme que celle de Claude Code)."""
        dernier = None
        for ligne in (sortie or "").splitlines():
            try:
                e = json.loads(ligne)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == "result":
                dernier = e
        return dernier

    def executer(self, t, message, executer_=None):
        self.refuser_mcp(t)
        with tempfile.TemporaryDirectory(prefix="jobsearch-") as dossier:
            consigne = _ecrire(os.path.join(dossier, "consigne.txt"), _consigne(t))
            r = self.lancer(t, self.commande(t, dossier, consigne), message, dossier, self.environnement(), executer_)
        d = self.lire(r.stdout)
        if d is None:
            raise self.erreur(t, r.stderr or r.stdout, r.returncode)
        if d.get("is_error") or d.get("subtype") not in (None, "success"):
            raise self.erreur(t, " ".join(d.get("errors") or []) or d.get("result") or d.get("subtype"), r.returncode)
        u = d.get("usage") or {}
        usage = {k: u.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        return self.resultat(t, d.get("result") or "", brut=d.get("structured_output"), usage=usage,
                             session=d.get("session_id") or None, modele=d.get("model") or self.modele(t))

    def identifiants(self):
        return self.maison() / ".grok" / "auth.json"

    def etat(self, executer=subprocess.run):
        try:
            self.executable()
        except _moteur().LancementImpossible as e:
            return {"installe": False, "connecte": False, "abonnement": None, "message": str(e)}
        ok = self.identifiants().exists()
        return {"installe": True, "connecte": ok, "abonnement": None,
                "message": "Connecté au compte xAI" if ok else "Grok n'est pas connecté : « Se connecter » sur la page IA"}

    def connexion(self):
        return self.script_env() + 'export GROK_HOME="$HOME/.grok"; exec grok login --device-code'

    def deconnexion(self):
        return self.script_env() + 'export GROK_HOME="$HOME/.grok"; grok logout || rm -f "$GROK_HOME/auth.json"'

    def session(self, consigne, tache, donnees, dossier):
        """Grok lit ses serveurs MCP dans `.grok/config.toml` du dossier de travail : un dossier de
        la VM propre à la session, hors du dossier partagé."""
        travail = self.maison() / "sessions" / str(tache)
        (travail / ".grok").mkdir(parents=True, exist_ok=True)
        toml = "".join('[mcp_servers.%s]\ncommand = %s\nargs = %s\n\n' % (nom, json.dumps(c), json.dumps(a))
                       for nom, c, a in serveurs_session(tache, donnees))
        _ecrire(travail / ".grok" / "config.toml", toml)
        c = [self.executable(), "-p", consigne, "--cwd", str(travail), "--sandbox", "workspace", "--tools", "",
             "--allow", "MCPTool"] + self.options(False)
        modele = ((_moteur().reglage("modeles") or {}).get(self.id) or {}).get("principal")
        return c + (["-m", modele] if modele else []), self.environnement(), str(travail), None


TOUS = {f.id: f for f in (Claude(), Codex(), Antigravity(), Grok())}


def get(fid):
    f = TOUS.get(fid or "claude")
    if f is None:
        raise ValueError("fournisseur inconnu : %s" % fid)
    return f


def installer_vm():
    """Le script (dans la VM, compte de l'utilisateur) qui installe Codex, Antigravity CLI et Grok
    Build aux versions figées, s'ils manquent ou ne sont pas à la bonne version."""
    prefixe = PREFIXE_OUTILS.replace("~", "$HOME")
    return """set -e
export PATH="/opt/node/bin:$PATH"
P="%(p)s"
mkdir -p "$P" "$HOME/moteurs"
command -v bwrap >/dev/null || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bubblewrap
# --version peut écrire dans le dossier personnel : celui du moteur, jamais le vrai.
v() { mkdir -p "$HOME/moteurs/$1"; HOME="$HOME/moteurs/$1" "$P/bin/$1" --version 2>/dev/null | grep -q "$2"; }
v codex %(vcodex)s || npm install -g --no-fund --no-audit --prefix "$P" %(codex)s
# Antigravity : l'archive de Google, empreinte vérifiée ; son dossier en lecture seule, pour qu'il ne
# se mette pas à jour tout seul (il le fait en arrière-plan, sans réglage pour l'en empêcher).
mkdir -p "$HOME/moteurs/antigravity"
if ! HOME="$HOME/moteurs/antigravity" "$P/antigravity/agy" --version 2>/dev/null | grep -qx %(vagy)s; then
  chmod -R u+w "$P/antigravity" 2>/dev/null || true
  rm -rf "$P/antigravity" && mkdir -p "$P/antigravity"
  curl -fsSL -o "$P/antigravity/agy.tar.gz" "%(uagy)s"
  echo "%(sagy)s  $P/antigravity/agy.tar.gz" | sha512sum -c -
  tar -xzf "$P/antigravity/agy.tar.gz" -C "$P/antigravity" antigravity
  mv "$P/antigravity/antigravity" "$P/antigravity/agy" && rm -f "$P/antigravity/agy.tar.gz"
fi
chmod 555 "$P/antigravity/agy" "$P/antigravity"
# Gemini CLI, remplacé par Antigravity : retiré s'il reste d'une version précédente.
if [ -e "$P/lib/node_modules/@google/gemini-cli" ]; then npm uninstall -g --prefix "$P" @google/gemini-cli >/dev/null; fi
if ! HOME="$HOME/moteurs/grok" "$P/grok/bin/grok" --version 2>/dev/null | grep -q %(grok)s; then
  # L'installeur de xAI, avec un dossier personnel à part : il n'écrit ni dans ~/.bashrc ni dans ~/.local.
  mkdir -p "$HOME/moteurs/grok"
  curl -fsSL https://x.ai/cli/install.sh -o "$P/grok-install.sh"
  HOME="$HOME/moteurs/grok" GROK_BIN_DIR="$P/grok/bin" bash "$P/grok-install.sh" %(grok)s
  rm -f "$P/grok-install.sh"
fi
""" % {"p": prefixe, "codex": CODEX, "grok": GROK, "vcodex": CODEX.rsplit("@", 1)[1],
       "vagy": ANTIGRAVITY["version"], "uagy": ANTIGRAVITY["url"], "sagy": ANTIGRAVITY["sha512"]}
