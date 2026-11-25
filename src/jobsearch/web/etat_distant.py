"""L'état de la machine virtuelle et des moteurs d'IA qu'elle porte, gardé en mémoire pour les pages.

Le lire coûte cher : `limactl list` (environ 0,5 s) et le /etat du travailleur, qui interroge chaque
moteur dans la VM (une à trois secondes). Une page ne l'attend donc jamais : elle lit la dernière copie,
et une copie trop vieille se relit en arrière-plan, une seule relecture à la fois. Seules la toute
première lecture et celle qui suit `oublier()` (connexion ou déconnexion d'un moteur) se font pendant
la requête : la page qui suit montre alors le nouvel état.

Une copie en erreur (VM qui démarre, travailleur pas encore prêt) se relit plus souvent qu'une copie
saine : l'IA paraît connectée dès que la VM est prête.
"""

import threading
import time

FRAIS_S = 20          # une copie saine se relit au-delà
FRAIS_ERREUR_S = 3    # une copie en erreur, plus vite

_verrou = threading.Lock()          # une seule relecture à la fois
_copie = {"t": None, "vm": None, "travailleur": None}
_oubliee = True


def mesurer():
    """{"vm": {"existe", "statut"} ou {"erreur"}, "travailleur": l'état rendu par /etat ou {"erreur"}}."""
    from .. import vm
    from ..travailleur import client as travailleur
    try:
        i = vm.instance()
        etat_vm = {"existe": bool(i), "statut": (i or {}).get("status")}
    except Exception as e:
        etat_vm = {"existe": False, "statut": None, "erreur": str(e)}
    try:
        tr = travailleur.Distant.depuis_reglages().etat()
    except Exception as e:
        tr = {"erreur": str(e)}
    return {"vm": etat_vm, "travailleur": tr}


def _relire():
    global _oubliee
    debut = time.monotonic()
    with _verrou:
        if _copie["t"] is not None and _copie["t"] >= debut and not _oubliee:
            return dict(_copie)                  # relue par un autre fil pendant qu'on attendait
        _oubliee = False
        _copie.update(mesurer(), t=time.monotonic())
    return dict(_copie)


def _en_erreur(c):
    return "erreur" in (c["travailleur"] or {}) or "erreur" in (c["vm"] or {})


def _relire_en_fond():
    if _verrou.locked():
        return
    threading.Thread(target=_relire, name="etat-distant", daemon=True).start()


def lire():
    """La dernière copie ; la relit pendant l'appel seulement s'il n'y en a pas encore ou qu'elle a été oubliée."""
    if _copie["t"] is None or _oubliee:
        return _relire()
    c = dict(_copie)
    if time.monotonic() - c["t"] > (FRAIS_ERREUR_S if _en_erreur(c) else FRAIS_S):
        _relire_en_fond()
    return c


def prechauffer():
    """Au démarrage du tableau de bord : la première page trouve la copie prête."""
    _relire_en_fond()


def oublier():
    """Un moteur vient d'être connecté ou déconnecté : la prochaine lecture est faite pendant la requête."""
    global _oubliee
    _oubliee = True
