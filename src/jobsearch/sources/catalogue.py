"""Les sources interrogées au passage, construites en un seul endroit.

Le passage (sur le Mac) et le travailleur (dans la machine virtuelle) partent de
la même liste : le travailleur n'exécute que celles que les réglages lui confient
(`travailleur.sources`), le Mac les remplace par leur double distant.
"""

from ..config import criteres, reglages
from . import employeurs
from .base import Client
from .indeed import Indeed
from .jobroom import JobRoom
from .jobup import Jobup
from .linkedin import LinkedIn

# Le socle proposé à l'accueil (étape « Tes sources ») : sites publics, sans compte ni IA.
SOCLE = [
    {"id": "job-room", "nom": "Job-Room", "detail": "Le portail officiel suisse de l'emploi. Certaines annonces y paraissent cinq jours avant les autres sites."},
    {"id": "jobs-ch", "nom": "jobs.ch", "detail": "Le plus grand site d'annonces de Suisse."},
    {"id": "jobup", "nom": "jobup.ch", "detail": "Les annonces de Suisse romande. Les doublons avec jobs.ch sont fusionnés."},
]


def socle_choisi():
    """Les sites du socle que la personne a gardés (tous, tant qu'elle n'a pas choisi)."""
    from .. import profil
    choix = profil.lire(profil.charger(), "sources.socle")
    return [s["id"] for s in SOCLE] if choix is None else list(choix)


def construire(client=None, reg=None):
    """{nom: source} : le socle, les pages carrière branchées dans le code, celles que
    l'utilisateur a ajoutées (si `reg` est donné), Indeed et LinkedIn s'ils sont actifs."""
    from .. import profil
    p = reglages().get("passage") or {}
    client = client or Client(pause=float(p.get("pause_requetes_s", 0.8)))
    out = {"job-room": JobRoom(client), "jobup": Jobup(client)}
    out.update(employeurs.connecteurs(client))
    r = reglages().get("jobs_ch") or {}
    if r.get("actif", True):
        out["jobs-ch"] = Jobup(client, site="jobs.ch")
    choisis = socle_choisi()
    for s in SOCLE:
        if s["id"] not in choisis:
            out.pop(s["id"], None)
    if reg is not None:
        from . import ajoutees
        out.update(ajoutees.connecteurs(reg, client))
        if (reglages().get("alertes_email") or {}).get("actif"):
            from .alertes import NOM, AlertesEmail
            out[NOM] = AlertesEmail(reg, client)
    from .. import connecteurs
    r = reglages().get("indeed") or {}
    if connecteurs.actif("indeed"):
        c = criteres()["sourcing"]
        out["indeed"] = Indeed(r, c["requetes"], c["cantons"])
    r = reglages().get("linkedin") or {}
    if r.get("actif"):
        c = criteres()["sourcing"]
        out["linkedin"] = LinkedIn(client, r, c.get("linkedin_requetes"),
                                   c.get("linkedin_lieu") or profil.identite().get("ville", ""))
    return out
