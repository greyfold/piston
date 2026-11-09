"""Les commandes d'une session Claude dans la machine virtuelle : `jobsearch-session …`.

La session (Claude Code et Chrome, dans la VM) n'a ni la base ni le profil : le Mac prépare
ce dont elle a besoin dans le dossier partagé, et relève ce qu'elle y note.

    /donnees/taches/<id>/donnees.json       préparé par le Mac : preuves à saisir, ou valeurs
                                            du formulaire et chemins des documents
    /donnees/taches/<id>/documents/         CV et lettre, copiés par le Mac pour cette tâche
    /donnees/taches/<id>/evenements.jsonl   noté ici, relevé et appliqué par le Mac
                                            (agent_claude.relever), qui vérifie chaque ligne

Mêmes commandes que la session d'avant, sur le Mac : `jobroom-attente`, `formulaire <annonce>`,
`tache <id> <état> [--message]`, `jobroom-fait <annonce> [--deja | --compteur]`,
`envoyee <annonce> [--url]`. La tâche courante vient de JOBSEARCH_TACHE, posée par le
travailleur au lancement : une session ne peut écrire que dans sa propre tâche.
"""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ETATS = ("en_cours", "connexion", "a_envoyer", "fait", "arret")


def dossier(tache=None):
    from .config import DATA
    t = str(tache or os.environ.get("JOBSEARCH_TACHE") or "")
    if not t.isdigit():
        raise SystemExit("Tâche inconnue : JOBSEARCH_TACHE manque.")
    return DATA / "taches" / t


def donnees():
    f = dossier() / "donnees.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def noter(evenement):
    d = dossier()
    d.mkdir(parents=True, exist_ok=True)
    evenement = dict(evenement, le=dt.datetime.now().isoformat(sep=" ", timespec="seconds"))
    with open(d / "evenements.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(evenement, ensure_ascii=False) + "\n")


def _empreintes(d):
    if "preuves" in d:
        return {p["empreinte"] for p in d["preuves"]}
    return {d.get("empreinte")} - {None}


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch-session")
    sp = p.add_subparsers(dest="commande", required=True)
    sp.add_parser("jobroom-attente")
    s = sp.add_parser("formulaire")
    s.add_argument("annonce")
    s = sp.add_parser("tache")
    s.add_argument("id")
    s.add_argument("etat", choices=ETATS)
    s.add_argument("--message")
    s = sp.add_parser("jobroom-fait")
    s.add_argument("annonce")
    s.add_argument("--deja", action="store_true")
    s.add_argument("--compteur")
    s = sp.add_parser("envoyee")
    s.add_argument("annonce")
    s.add_argument("--url")
    a = p.parse_args(argv)
    d = donnees()

    if a.commande == "jobroom-attente":
        print(json.dumps(d.get("preuves", []), ensure_ascii=False, indent=1))
    elif a.commande == "formulaire":
        if a.annonce != d.get("empreinte"):
            print(json.dumps({"erreur": "cette tâche ne porte pas sur l'annonce %s" % a.annonce}, ensure_ascii=False))
            return 1
        print(json.dumps({k: d[k] for k in ("lien", "valeurs", "documents") if k in d}, ensure_ascii=False, indent=1))
    elif a.commande == "tache":
        if a.id != dossier().name:
            print("Cette session ne porte que sur la tâche %s." % dossier().name)
            return 1
        noter({"type": "etat", "etat": a.etat, "message": a.message})
        print("Tâche %s : %s" % (a.id, a.etat))
    elif a.commande in ("jobroom-fait", "envoyee"):
        if a.annonce not in _empreintes(d):
            print("Annonce %s hors de cette tâche." % a.annonce)
            return 1
        if a.commande == "jobroom-fait":
            noter({"type": "jobroom_fait", "empreinte": a.annonce, "deja": a.deja, "compteur": a.compteur})
        else:
            noter({"type": "envoyee", "empreinte": a.annonce, "url": a.url or ""})
        print("Noté : %s %s." % (a.commande, a.annonce))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def chemin_vm(tache, nom):
    """Chemin, dans la VM, d'un document copié pour la tâche."""
    return str(Path("/donnees") / "taches" / str(tache) / "documents" / nom)
