"""Alertes e-mail des sites d'emploi : une source comme les autres.

L'utilisateur s'abonne aux alertes d'un site (y compris d'un site que la plateforme
ne lit pas) ; à chaque passage, les connecteurs de courrier actifs (Apple Mail,
Gmail) relèvent les alertes reçues depuis le dernier passage, et le moteur en tire
les offres (page_ia.extraire : seuls les liens présents dans l'e-mail sont gardés).
Le texte complet de chaque offre est lu ensuite par script, sur sa page.

Désactivé par défaut (`alertes_email.actif`) : chaque alerte lue coûte une tâche
Claude sur l'abonnement.
"""

import datetime as dt
import re

from .. import connecteurs, courrier
from ..config import reglages
from ..db import maintenant
from .base import RouteCassee, html_vers_texte
from .page_ia import annonces_d_alerte

NOM = "alertes-email"
MOTS = ["alerte", "alert", "nouvelles offres", "nouvelle offre", "new jobs", "jobs matching", "recommended jobs",
        "offres qui pourraient", "Jobabo", "Stellenalarm"]
URL = re.compile(r"https?://[^\s<>\")\]]+")


def liens(texte):
    """[(libellé, url)] : chaque adresse du texte, avec sa ligne comme libellé."""
    out, vus, avant = [], set(), ""
    for ligne in (texte or "").splitlines():
        reste = URL.sub("", ligne).strip(" :-–\t")
        for u in URL.findall(ligne):
            u = u.rstrip(".,;")
            if u not in vus:
                vus.add(u)
                out.append(((reste or avant)[:150], u))      # une adresse seule sur sa ligne : la ligne d'avant
        if reste:
            avant = reste
    return out


class AlertesEmail:
    nom = NOM

    def __init__(self, reg, client, lire=None, lire_gmail_=None, moteur=None):
        self.reg, self.c = reg, client
        self.r = reglages().get("alertes_email") or {}
        self.lire, self.lire_gmail, self.moteur = lire, lire_gmail_, moteur

    def tester(self):
        actifs = [n for n in ("apple-mail", "gmail") if connecteurs.actif(n)]
        if not actifs:
            raise RouteCassee("alertes e-mail : aucun connecteur de courrier actif")
        return len(actifs)

    def _messages(self, debut):
        comptes = (reglages().get("courrier") or {}).get("comptes") or ["iCloud", "Google"]
        out = []
        if connecteurs.actif("apple-mail"):
            out += (self.lire or courrier.lire_boite)(MOTS, debut, comptes, limite=40, longueur=20000)
        if connecteurs.actif("gmail"):
            out += (self.lire_gmail or courrier.lire_gmail)(MOTS, debut, longueur=20000)
        return [m for m in out if courrier._PAS_UNE_REPONSE.search(courrier.plier(m.get("subject") or ""))
                or "alert" in (m.get("subject") or "").lower()]

    def offres(self):
        lu = self.reg.meta("alertes_lues_le")
        debut = (lu or (dt.date.today() - dt.timedelta(days=2)).isoformat())[:10]
        messages = self._messages(debut)[:int(self.r.get("mails_par_passage", 5))]
        out = []
        for m in messages:
            texte = m.get("content_preview") or ""
            ls = liens(texte)
            if ls:
                out += annonces_d_alerte(texte, ls, "%s · %s" % (m.get("sender") or "", m.get("subject") or ""),
                                         NOM, self.moteur)
        self.reg.poser_meta("alertes_lues_le", maintenant())
        self.reg.commit()
        return out

    def detail(self, a):
        a.texte = html_vers_texte(self.c.page(a.url))[:20000]
        a.detail_charge = True
        return a
