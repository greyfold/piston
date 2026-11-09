"""La première recherche, lancée à la fin de l'accueil : le premier résultat réel (« aha »).

Un passage léger (sources publiques et pré-tri, sans IA ni courrier) tourne en arrière-plan
dans le tableau de bord ; la page de l'accueil suit son état et montre ce qu'il a trouvé.
"""

import threading

from . import config
from .db import Registre

SOURCES = ("job-room", "jobup", "jobs-ch")       # publiques, sans compte ni IA
NOMS_SOURCES = {"job-room": "Job-Room", "jobup": "jobup.ch", "jobs-ch": "jobs.ch"}

_verrou = threading.Lock()
_etat = {"etat": "aucune"}                         # aucune · en_cours · finie · erreur


def etat():
    with _verrou:
        return dict(_etat)


def _poser(**kw):
    with _verrou:
        _etat.clear()
        _etat.update(kw)


def lancer(chemin_db=None, sources=None, attendre=False):
    """Démarre la première recherche (une seule à la fois). Renvoie False si elle tourne déjà."""
    with _verrou:
        if _etat.get("etat") == "en_cours":
            return False
        _etat.clear()
        _etat["etat"] = "en_cours"
    t = threading.Thread(target=_tourner, args=(chemin_db or config.chemin_db(), sources), daemon=True)
    t.start()
    if attendre:
        t.join()
    return True


def _tourner(chemin_db, sources):
    from .pipeline import Passage
    reg = Registre(chemin_db)
    try:
        if sources is None:
            from .sources.catalogue import construire
            sources = {k: v for k, v in construire(reg=reg).items() if k in SOURCES}
        avant = {r[0] for r in reg.cx.execute("SELECT empreinte FROM annonces")}
        res = Passage(reg, sources=sources, declencheur="accueil", notifier_fin=False, leger=True).lancer()
        _poser(etat="finie", run=res["id"], **resultats(reg, res["id"], avant))
    except Exception as e:
        _poser(etat="erreur", erreur=str(e)[:300])
    finally:
        reg.fermer()


def resultats(reg, run_id, avant=()):
    """Ce que la page montre : où on a cherché, combien, ce qui a été gardé et pourquoi le reste est écarté."""
    import json
    etapes = {r["etape"]: json.loads(r["compteurs"] or "{}") for r in reg.cx.execute(
        "SELECT etape, compteurs FROM run_etapes WHERE run_id=?", (run_id,))}
    sourcing, pt = etapes.get("sourcing", {}), etapes.get("pre_tri", {})
    sources = [NOMS_SOURCES.get(k, k) for k, v in sourcing.items() if k in NOMS_SOURCES and isinstance(v, int)]
    nouvelles = [f for f in reg.liste(["nouvelle"]) if f["empreinte"] not in avant]
    nouvelles.sort(key=lambda f: f.get("publiee_le") or f.get("vue_le") or "", reverse=True)
    ecartees = reg.cx.execute(
        "SELECT motif, COUNT(*) n FROM annonces WHERE statut='C' AND empreinte NOT IN (%s) GROUP BY motif ORDER BY n DESC"
        % ",".join("?" * len(avant)) if avant else
        "SELECT motif, COUNT(*) n FROM annonces WHERE statut='C' GROUP BY motif ORDER BY n DESC",
        tuple(avant)).fetchall()
    return {
        "sources": sources,
        "trouvees": len(nouvelles) + int(pt.get("ecartees", 0)),     # nouvelles, doublons entre sites fusionnés
        "gardees": len(nouvelles),
        "ecartees": int(pt.get("ecartees", 0)),
        "motifs": [(m.split(" : ", 1)[-1], n) for m, n in ecartees][:4],
        "apercu": [{"empreinte": f["empreinte"], "titre": f["titre"], "entreprise": f.get("entreprise") or "",
                    "lieu": f.get("lieu") or "", "url": f.get("url") or ""} for f in nouvelles[:4]],
    }
