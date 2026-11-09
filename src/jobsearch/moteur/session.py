"""Session `claude -p` en flux JSON, pour les tâches qui appellent des outils.

On envoie un message, on lit le tour jusqu'au « result », en gardant chaque
appel d'outil avec sa réponse brute : Python lit ce que les outils ont rendu,
le modèle ne recopie rien. C'est ainsi qu'Indeed passe par le connecteur de
claude.ai (sources/indeed.py) ; Gmail suivra le même chemin.
"""

import json
import queue
import subprocess
import tempfile
import threading
import time

from . import MoteurImpossible, environnement


def texte_outil(contenu):
    """Contenu d'un tool_result → texte ; un connecteur claude.ai rend {"result": "…"}."""
    if isinstance(contenu, list):
        contenu = "".join(c.get("text", "") for c in contenu if isinstance(c, dict))
    contenu = contenu or ""
    try:
        d = json.loads(contenu)
        return d.get("result", contenu) if isinstance(d, dict) else contenu
    except ValueError:
        return contenu


class SessionClaude:
    """`erreur` : l'exception levée quand le tour échoue (RouteCassee pour une source) ;
    `etiquette` : le nom qui la préfixe."""

    def __init__(self, commande, cwd=None, journal=None, debogage=None, erreur=MoteurImpossible, etiquette="claude"):
        self.journal = open(journal, "a", encoding="utf-8") if journal else None
        self.debogage = debogage
        self.erreur, self.etiquette = erreur, etiquette
        self.proc = subprocess.Popen(commande, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, cwd=cwd or tempfile.gettempdir(),
                                     env=environnement())
        self.lignes = queue.Queue()
        self.outils, self.serveurs = None, {}
        threading.Thread(target=self._lire, daemon=True).start()

    def _lire(self):
        for l in self.proc.stdout:
            if self.journal:
                self.journal.write(l)
                self.journal.flush()
            self.lignes.put(l)
        self.lignes.put(None)

    def tour(self, message, delai):
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": message}}) + "\n")
        self.proc.stdin.flush()
        fin, appels, sortie = time.monotonic() + delai, {}, []
        while True:
            try:
                l = self.lignes.get(timeout=max(0.1, fin - time.monotonic()))
            except queue.Empty:
                raise self.erreur("%s : la session Claude n'a pas fini en %d s" % (self.etiquette, delai))
            if l is None:
                err = (self.proc.stderr.read() or "").strip()
                raise self.erreur("%s : la session Claude s'est arrêtée (%s)" % (self.etiquette, err[:200] or "sans message"))
            try:
                ev = json.loads(l)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.outils = ev.get("tools") or []
                self.serveurs = {m.get("name"): m.get("status") for m in ev.get("mcp_servers") or []}
            elif t == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        appels[b["id"]] = (b.get("name") or "", b.get("input") or {})
            elif t == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in appels:
                        nom, entree = appels[b["tool_use_id"]]
                        sortie.append({"outil": nom, "entree": entree, "texte": texte_outil(b.get("content")),
                                       "erreur": bool(b.get("is_error"))})
            elif t == "result":
                if ev.get("is_error"):
                    raise self.erreur("%s : %s" % (self.etiquette, (ev.get("result") or ev.get("subtype") or "erreur")[:200]))
                return sortie

    def fermer(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        if self.journal:
            self.journal.close()
