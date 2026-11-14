"""Le pré-remplissage sans Claude, dans la machine virtuelle : `python -m jobsearch.preremplir_vm`.

Lancé par le travailleur (POST /preremplir) pour la tâche JOBSEARCH_TACHE. Lit ce que le Mac a
préparé (`taches/<id>/donnees.json` : valeurs et documents du formulaire, ou valeurs Job-Room et
mots de l'anti-doublon), remplit dans le Chrome de la VM (navigateur.py) et note dans
`evenements.jsonl` ce que le Mac doit appliquer : l'envoi constaté (que la personne confirme
elle-même), la preuve Job-Room sauvegardée, la fin. Le Mac relève ces lignes (agent_claude.relever) et les vérifie.
"""

import sys

from . import navigateur
from .session_vm import donnees, noter


def main():
    d = donnees()
    try:
        if d.get("type") == "pre-formulaire":
            def a_l_envoi(url):
                noter({"type": "envoyee", "empreinte": d["empreinte"], "url": url})
                return "Signalée au tableau de bord : confirme l'envoi avec « Marquer envoyée »."
            navigateur.preremplir_employeur(d["lien"], d["valeurs"], d["documents"], a_l_envoi=a_l_envoi)
        elif d.get("type") == "pre-jobroom":
            def apres_sauvegarde(*_a, **_k):
                noter({"type": "jobroom_fait", "empreinte": d["empreinte"], "deja": False,
                       "compteur": "pré-remplissage, ligne constatée une seule fois"})
            navigateur.preremplir_jobroom(d["valeurs"], d["jetons"], apres_sauvegarde=apres_sauvegarde)
        else:
            noter({"type": "etat", "etat": "arret", "message": "tâche de pré-remplissage inconnue"})
            return 1
    except navigateur.NavigateurOccupe as e:
        noter({"type": "etat", "etat": "arret", "message": str(e)})
        return 1
    except Exception as e:
        noter({"type": "etat", "etat": "arret", "message": "pré-remplissage interrompu : %s" % str(e)[:200]})
        return 1
    noter({"type": "etat", "etat": "fait", "message": "fenêtre fermée"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
