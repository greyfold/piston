"""Le superviseur : ce que fait l'enveloppe native, écrit une seule fois pour tous les systèmes.

L'enveloppe (SwiftUI sur Mac, Tauri sous Windows et Linux) ne garde que le natif : la fenêtre, l'icône
de la barre des menus (ou de la zone de notification), la dictée, les mises à jour, l'ouverture à la
connexion. Tout le reste est ici, lancé par elle (`jobsearch superviseur --port 8775`) :

- un seul tableau de bord sur le port : celui de cette installation est adopté, un ancien (autre code,
  application mise à jour ou déplacée) est arrêté puis remplacé, celui d'une autre installation ou un
  autre programme est refusé avec un message ; un tableau de bord tombé est relancé, au plus trois fois
  de suite, de plus en plus espacées ;
- l'état (`/api/etat`) relu toutes les 30 s, pour le menu ;
- `jobsearch echeances` toutes les 5 min (passage du matin, alerte, surveillance), un seul à la fois ;
- au lancement, `jobsearch vm demarrer` et ses étapes (l'écran /demarrage), puis les échéances ;
- les commandes du menu (passage, démarrer ou arrêter la VM), une à la fois, chacune sous un délai de garde ;
- à la fermeture, l'arrêt de ce qui tourne, puis de la machine virtuelle, puis du tableau de bord.

Le dialogue avec l'enveloppe : une ligne JSON par message.
- Sortie (événements) : {"evenement": "tableau", "pret": true, "adresse", "entree"} · {"evenement": "tableau",
  "pret": false, "erreur"} · {"evenement": "etat", …/api/etat} · {"evenement": "occupe", "titre"} ·
  {"evenement": "etape", "texte"} · {"evenement": "page", "page"} · {"evenement": "recharger_accueil"} ·
  {"evenement": "message", "texte"} · {"evenement": "entree", "adresse", "id"} ·
  {"evenement": "arret", "passage_interrompu"} · {"evenement": "fin"}.
- Entrée (ordres) : {"ordre": "passage" | "vm_demarrer" | "vm_arreter" | "rafraichir" | "reessayer" |
  "entree" (avec "suivant", "id") | "quitter"}. Fin de l'entrée (l'enveloppe est morte) : tout ce que le
  superviseur a lancé s'arrête, la VM reste (elle sera adoptée au prochain lancement).

`entree` : une adresse à usage unique (/entree?code=…) qui pose le cookie du jeton dans la vue web ;
l'enveloppe n'a ainsi besoin d'aucune API de cookies.

Le superviseur ne touche jamais la base : il lit l'état par le tableau de bord et lance des commandes.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from . import hote

DELAI_PASSAGE = 6 * 3600        # une douzaine d'étapes bornées à 900 s, plus un appel à l'IA : au pire ~6 h
DELAI_COMMANDE = 3600            # la VM se crée (image à télécharger) en moins d'une heure
DELAI_ARRET_VM = 240             # arrêt propre (120 s) puis forcé (60 s)
INTERVALLE_ETAT = 30
INTERVALLE_ECHEANCES = 300
ENTETE = "X-Jobsearch-Jeton"


def _racine_paquet():
    import jobsearch
    return Path(jobsearch.__file__).resolve().parent


class Superviseur:
    """`lancer(args, **kw)` démarre `python -m jobsearch <args>` (les tests en donnent un faux) ;
    `http` : le client du tableau de bord ; `ecrire(ligne)` : la sortie vers l'enveloppe."""

    def __init__(self, port=8775, hote_web="127.0.0.1", donnees=None, ecrire=None, lancer=None, http=None,
                 dormir=time.sleep):
        from .config import DATA
        self.port = port
        self.hote_web = hote_web
        self.donnees = Path(donnees or DATA)
        self.base = "http://localhost:%d/" % port
        self._ecrire = ecrire or self._vers_stdout
        self._lancer = lancer or self._python
        self.http = http or httpx.Client(timeout=3)
        self.dormir = dormir
        self._verrou = threading.RLock()
        self._sortie = threading.Lock()
        self.web = None                  # le tableau de bord lancé ici (None : adopté, ou aucun)
        self.echeances = None
        self.commande = None             # (processus, titre) de la commande du menu en cours
        self.tableau_pret = False
        self.en_demarrage = False
        self.relances = 0
        self.lance_le = 0.0
        self.refus = 0
        self.mode = None                 # « distant » (VM) ou « local », d'après /api/etat
        self.quitte = False
        self.arret = threading.Event()   # posé quand tout est fini

    # --- Sortie et journal ------------------------------------------------------------

    def _vers_stdout(self, ligne):
        sys.stdout.write(ligne + "\n")
        sys.stdout.flush()

    def emettre(self, evenement, **champs):
        ligne = json.dumps(dict(champs, evenement=evenement), ensure_ascii=False)
        with self._sortie:
            try:
                self._ecrire(ligne)
            except (OSError, ValueError):
                pass                     # l'enveloppe n'écoute plus : la fin de l'entrée suivra

    def journal(self, texte):
        try:
            (self.donnees / "logs").mkdir(parents=True, exist_ok=True)
            with open(self.donnees / "logs" / "superviseur.log", "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), texte))
        except OSError:
            pass

    # --- Processus --------------------------------------------------------------------

    def _python(self, args, **kw):
        kw.setdefault("cwd", str(self.donnees))
        kw.setdefault("stdin", subprocess.DEVNULL)
        for cle, valeur in hote.options_sans_console().items():   # Windows : pas de console qui s'ouvre
            kw.setdefault(cle, valeur)
        return subprocess.Popen([sys.executable, "-m", "jobsearch", *args], **kw)

    def garde(self, proc, secondes):
        """Passé `secondes`, le processus est arrêté (puis tué 30 s plus tard s'il tient encore)."""
        def arreter():
            if proc.poll() is None:
                self.journal("délai de garde dépassé (%d s) : arrêt du processus %s" % (secondes, proc.pid))
                proc.terminate()
                t = threading.Timer(30, lambda: proc.poll() is None and proc.kill())
                t.daemon = True
                t.start()
        t = threading.Timer(secondes, arreter)
        t.daemon = True
        t.start()
        return t

    @staticmethod
    def _sortie_de(proc):
        """Lit toute la sortie d'un processus (le tube ne se remplit jamais) ; n'en garde que la fin."""
        if proc.stdout is None:
            proc.wait()
            return ""
        morceaux = []
        for ligne in proc.stdout:
            morceaux.append(ligne)
            if len(morceaux) > 400:
                del morceaux[:200]
        proc.wait()
        return "".join(morceaux)

    # --- Le tableau de bord -------------------------------------------------------------

    def jeton(self):
        try:
            return (self.donnees / "jeton").read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _entetes(self):
        j = self.jeton()
        return {ENTETE: j} if j else {}

    def sonder(self):
        """Ce qui répond sur le port : ("rien"), ("tableau", état), ("refus") ou ("etranger")."""
        try:
            r = self.http.get(self.base + "api/etat", headers=self._entetes(), timeout=3)
        except httpx.ConnectError:
            return ("rien", None)
        except httpx.HTTPError:
            return ("etranger", None)
        if r.status_code == 200:
            try:
                return ("tableau", r.json())
            except ValueError:
                return ("etranger", None)
        if r.status_code in (401, 403):
            return ("refus", None)
        return ("etranger", None)

    def _meme_dossier(self, autre):
        try:
            return Path(autre).resolve() == self.donnees.resolve()
        except OSError:
            return False

    def assurer_tableau(self):
        """Le tableau de bord de cette installation : adopté, remplacé ou lancé. Faux s'il n'est pas prêt
        (l'erreur est émise)."""
        with self._verrou:
            if self.en_demarrage or self.quitte:
                return self.tableau_pret
            self.en_demarrage = True
        try:
            ok = self._assurer_tableau()
        finally:
            with self._verrou:
                self.en_demarrage = False
        return ok

    def _erreur_tableau(self, texte):
        self.journal("tableau de bord : " + texte)
        self.emettre("tableau", pret=False, erreur=texte)
        return False

    def _pret(self):
        self.tableau_pret = True
        self.refus = 0
        self.emettre("tableau", pret=True, adresse=self.base, entree=self.entree("/?ouverture=1"))
        return True

    def _assurer_tableau(self):
        genre, e = self.sonder()
        port = self.port
        if genre == "tableau":
            if not self._meme_dossier((e or {}).get("donnees") or ""):
                return self._erreur_tableau("Le port %d est pris par le tableau de bord d'une autre installation (%s)."
                                            % (port, (e or {}).get("donnees") or "?"))
            racine = (e or {}).get("racine")
            if racine and Path(racine).resolve() == _racine_paquet():
                return self._pret()
            pid = (e or {}).get("pid") or processus_sur_le_port(port)
            if not pid:
                return self._erreur_tableau(
                    "Un ancien tableau de bord occupe le port %d et n'a pas pu être identifié : quitte-le (%s, "
                    "processus « python ») puis réessaie." % (port, hote.libelle("gestionnaire")))
            if not self.terminer_ancien(pid):
                return self._erreur_tableau("L'ancien tableau de bord (processus %s) ne s'arrête pas : le port %d "
                                            "reste pris." % (pid, port))
        elif genre == "refus":
            return self._erreur_tableau("Le port %d est pris par un tableau de bord qui refuse le jeton de cette "
                                        "installation (une autre installation ?). Quitte-la puis réessaie." % port)
        elif genre == "etranger":
            return self._erreur_tableau("Le port %d est pris par un autre programme : quitte-le puis réessaie." % port)
        return self._lancer_tableau()

    def terminer_ancien(self, pid):
        import os
        if not pid or int(pid) <= 1 or int(pid) == os.getpid():
            return False
        try:
            import psutil
            p = psutil.Process(int(pid))
            p.terminate()
            try:
                p.wait(3)
            except psutil.TimeoutExpired:
                p.kill()
        except Exception:
            pass
        for _ in range(12):
            if self.sonder()[0] == "rien":
                return True
            self.dormir(0.25)
        return False

    def _lancer_tableau(self):
        try:
            p = self._lancer(["web", "--hote", self.hote_web, "--port", str(self.port)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return self._erreur_tableau("Le tableau de bord ne démarre pas : %s" % e)
        self.web = p
        self.lance_le = time.monotonic()
        threading.Thread(target=self._surveiller_tableau, args=(p,), daemon=True).start()
        for _ in range(60):
            self.dormir(0.5)
            if p.poll() is not None:
                return self._erreur_tableau("Le tableau de bord s'est arrêté au démarrage (code %s) : le port %d "
                                            "est peut-être pris par un autre programme." % (p.returncode, self.port))
            if self.sonder()[0] == "tableau":
                return self._pret()
        return self._erreur_tableau("Le tableau de bord ne répond pas sur le port %d." % self.port)

    def _surveiller_tableau(self, p):
        p.wait()
        with self._verrou:
            if p is not self.web or self.quitte:
                return
            self.web = None
            if not self.tableau_pret:
                return                   # mort avant d'avoir répondu : assurer_tableau() l'a dit
            self.tableau_pret = False
            if time.monotonic() - self.lance_le > 600:
                self.relances = 0        # il avait tenu : nouvelle série
            if self.relances >= 3:
                self._erreur_tableau("Le tableau de bord s'arrête sans cesse (code %s). Réessaie, ou reconstruis "
                                     "l'application si cela persiste." % p.returncode)
                return
            self.relances += 1
            attente = 5 * self.relances
        self.journal("tableau de bord arrêté (code %s) : relance dans %d s" % (p.returncode, attente))
        self.dormir(attente)
        self.assurer_tableau()

    def entree(self, suivant="/"):
        """Une adresse à usage unique qui pose le cookie du jeton dans la vue web, puis mène à `suivant`."""
        try:
            r = self.http.post(self.base + "api/entree", headers=self._entetes(), timeout=3)
            code = r.json().get("code") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            code = None
        if not code:
            return None
        return "%sentree?code=%s&suivant=%s" % (self.base, quote(code), quote(suivant or "/", safe=""))

    # --- L'état -----------------------------------------------------------------------

    def rafraichir(self):
        try:
            r = self.http.get(self.base + "api/etat", headers=self._entetes(), timeout=5)
        except httpx.ConnectError:
            self._personne_sur_le_port()
            return None
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        self.refus = 0
        try:
            e = r.json()
        except ValueError:
            return None
        self.mode = e.get("mode") or self.mode
        self.emettre("etat", **{k: v for k, v in e.items() if k != "evenement"})
        return e

    def _personne_sur_le_port(self):
        """Le tableau de bord adopté ne prévient pas de sa mort : après trois refus de suite (une minute et
        demie), il repasse par assurer_tableau, dans la même limite de relances."""
        with self._verrou:
            self.refus += 1
            if self.refus < 3 or self.web is not None or not self.tableau_pret or self.en_demarrage or self.quitte:
                return
            self.refus = 0
            self.tableau_pret = False
            if self.relances >= 3:
                self._erreur_tableau("Le tableau de bord s'arrête sans cesse. Réessaie, ou reconstruis "
                                     "l'application si cela persiste.")
                return
            self.relances += 1
        self.assurer_tableau()

    # --- Échéances et commandes ---------------------------------------------------------

    def lancer_echeances(self):
        with self._verrou:
            if self.echeances is not None or self.quitte:
                return False
            try:
                p = self._lancer(["echeances"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                 encoding="utf-8", errors="replace")
            except OSError as e:
                self.journal("échéances : %s" % e)
                return False
            self.echeances = p
        self.garde(p, DELAI_PASSAGE)

        def suivre():
            texte = self._sortie_de(p)
            with self._verrou:
                self.echeances = None
            if self.quitte:
                return
            if '"passage"' in texte:
                self.emettre("message", texte="Passage du matin : terminé.")
            self.rafraichir()
        threading.Thread(target=suivre, daemon=True).start()
        return True

    def executer(self, args, titre, apres=None, delai=DELAI_COMMANDE, etapes=False):
        """Une commande `jobsearch` en arrière-plan ; une seule à la fois. `etapes` : ses lignes « étape : … »
        sont relayées (démarrage de la VM), et la première demande l'écran /demarrage."""
        with self._verrou:
            if self.commande is not None or self.quitte:
                if apres:
                    apres()
                return False
            try:
                p = self._lancer(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", bufsize=1)
            except OSError as e:
                self.emettre("message", texte="%s : impossible de lancer la commande (%s)." % (titre, e))
                if apres:
                    apres()
                return False
            self.commande = (p, titre)
        self.emettre("occupe", titre=titre)
        self.garde(p, delai)

        def suivre():
            dernieres, premiere = [], True
            for ligne in p.stdout:
                ligne = ligne.rstrip("\r\n")
                dernieres = (dernieres + [ligne])[-50:]
                if etapes and ligne.startswith("étape : "):
                    if premiere:
                        self.emettre("page", page="demarrage")
                        premiere = False
                    self.emettre("etape", texte=ligne[len("étape : "):])
            code = p.wait()
            with self._verrou:
                self.commande = None
            self.emettre("occupe", titre=None)
            if self.quitte:
                return
            derniere = next((l for l in reversed(dernieres) if l.strip()), "")
            self.emettre("message", texte="%s : terminé." % titre if code == 0 else "%s : échec. %s" % (titre, derniere))
            self.rafraichir()
            if etapes:
                self.emettre("recharger_accueil")
            if apres:
                apres()
        threading.Thread(target=suivre, daemon=True).start()
        return True

    def demarrer_vm(self, ensuite=None):
        return self.executer(["vm", "demarrer"], "Démarrage de la machine virtuelle", apres=ensuite, etapes=True)

    # --- Cycle de vie -------------------------------------------------------------------

    def demarrer(self):
        """Le lancement de l'application : tableau de bord, état, VM, puis échéances ; et les minuteries."""
        def suite():
            self.assurer_tableau()
            self.rafraichir()
            # La VM est éteinte à chaque fermeture : elle redémarre ici. Les échéances attendent qu'elle
            # réponde, pour qu'un passage dû ne parte pas sans elle.
            if self.mode == "distant":
                if not self.demarrer_vm(ensuite=self.lancer_echeances):
                    self.lancer_echeances()
            else:
                self.lancer_echeances()
            self._minuterie(INTERVALLE_ETAT, self.rafraichir)
            self._minuterie(INTERVALLE_ECHEANCES, self.lancer_echeances)
        threading.Thread(target=suite, daemon=True).start()

    def _minuterie(self, intervalle, action):
        def boucle():
            while not self.arret.wait(intervalle):
                if self.quitte:
                    return
                try:
                    action()
                except Exception as e:               # une minuterie ne meurt pas d'une erreur
                    self.journal("minuterie : %s: %s" % (type(e).__name__, e))
        threading.Thread(target=boucle, daemon=True).start()

    def quitter(self, eteindre_vm=True):
        """Arrête ce qui tourne ; avec `eteindre_vm`, éteint la machine virtuelle (écran d'arrêt), puis le
        tableau de bord lancé ici. Le passage en cours reprendra au prochain lancement."""
        with self._verrou:
            if self.quitte:
                return
            self.quitte = True
            commande, echeances, web = self.commande, self.echeances, self.web
        interrompu = echeances is not None or bool(commande and commande[1].startswith("Passage"))
        for p in (commande[0] if commande else None, echeances):
            if p is not None and p.poll() is None:
                p.terminate()
        if eteindre_vm and self.mode != "local":
            self.emettre("arret", passage_interrompu=interrompu)
            try:
                p = self._lancer(["vm", "arreter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    p.wait(DELAI_ARRET_VM)
                except subprocess.TimeoutExpired:
                    p.kill()
            except OSError as e:
                self.journal("arrêt de la VM : %s" % e)
        if web is not None and web.poll() is None:
            web.terminate()
            try:
                web.wait(10)
            except subprocess.TimeoutExpired:
                web.kill()
        self.arret.set()
        self.emettre("fin")

    def ordre(self, message):
        """Un ordre de l'enveloppe."""
        o = (message or {}).get("ordre")
        if o == "passage":
            self.executer(["passage"], "Passage", delai=DELAI_PASSAGE)
        elif o == "vm_demarrer":
            self.demarrer_vm()
        elif o == "vm_arreter":
            self.executer(["vm", "arreter"], "Arrêt de la machine virtuelle")
        elif o == "rafraichir":
            self.rafraichir()
        elif o == "reessayer":
            self.relances = 0
            threading.Thread(target=self.assurer_tableau, daemon=True).start()
        elif o == "entree":
            self.emettre("entree", id=message.get("id"), adresse=self.entree(message.get("suivant") or "/"))
        elif o == "quitter":
            threading.Thread(target=self.quitter, daemon=True).start()

    def boucle(self, entree=None):
        """Lit les ordres jusqu'à la fin de l'entrée ; rend quand tout est arrêté."""
        self.demarrer()
        for ligne in (entree or sys.stdin):
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                self.ordre(json.loads(ligne))
            except ValueError:
                self.journal("ordre illisible : %r" % ligne[:200])
            if self.arret.is_set():
                break
        if not self.quitte:
            self.quitter(eteindre_vm=False)       # l'enveloppe est morte : la VM sera adoptée au lancement
        self.arret.wait(DELAI_ARRET_VM + 30)
        return 0


def processus_sur_le_port(port):
    """Le processus `jobsearch web` qui écoute sur `port` (ancien serveur sans `pid` dans /api/etat), ou None.
    Seuls les processus du compte qui sont un tableau de bord sont regardés (aucun droit d'administrateur)."""
    try:
        import psutil
    except ImportError:
        return None
    trouves = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            ligne = " ".join(p.info.get("cmdline") or [])
            if "jobsearch" not in ligne or " web" not in ligne:
                continue
            for c in p.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port:
                    trouves.append(p.info["pid"])
                    break
        except (psutil.Error, OSError):
            continue
    return trouves[0] if len(trouves) == 1 else None


def commande(port=8775, hote_web="127.0.0.1"):
    return Superviseur(port=port, hote_web=hote_web).boucle()
