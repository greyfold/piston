"""Ce que le Mac utilise pour parler au travailleur.

`Distant` a la même interface que le moteur local (`executer(tache, message)`) :
évaluation et dossiers ne savent ni où tourne le moteur, ni lequel c'est. `SourceDistante` a celle
d'une source (`tester`, `offres`, `detail`) : le passage ne sait pas où elle tourne.

Réglages `travailleur:` de config/settings.yaml : `mode` (local ou distant),
`adresse` (http://127.0.0.1:8776, le port que la VM ouvre vers le Mac), `sources`.
"""

import httpx

from .. import moteur
from ..config import reglages
from ..sources.base import Annonce, ConfigurationRequise, RouteCassee
from . import jeton

ADRESSE = "http://127.0.0.1:8776"


def reglage():
    return reglages().get("travailleur") or {}


def distant():
    return reglage().get("mode") == "distant"


def confiees():
    return list(reglage().get("sources") or ["indeed"])


_HTTP = None


def _http():
    """Un seul client HTTP vers le travailleur, réutilisé : un client par appel laissait ses
    connexions ouvertes (une de plus toutes les 30 s, à chaque lecture de l'état)."""
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(timeout=30)
    return _HTTP


def message_injoignable(e):
    """Le travailleur ne répond pas. Pendant le démarrage de la VM (lancement de l'application, « Continuer
    sans attendre »), c'est normal : le message le dit, avec l'avancement, au lieu d'une panne."""
    from .. import vm
    a = vm.avancement() if vm.en_demarrage() else None
    if a and a["en_cours"]:
        return ("la machine virtuelle démarre encore (%d %%, encore environ %d min) : l'IA sera prête ensuite, "
                "réessaie à ce moment-là" % (a["pourcentage"], a["reste_min"]))
    return "travailleur injoignable (%s) : la machine virtuelle est-elle démarrée ?" % type(e).__name__


class Distant:
    def __init__(self, adresse=ADRESSE, jeton_=None, http=None):
        self.adresse = adresse.rstrip("/")
        self.http = http or _http()
        self.entetes = {"Authorization": "Bearer %s" % (jeton_ or jeton.lire())}

    @classmethod
    def depuis_reglages(cls):
        return cls(reglage().get("adresse") or ADRESSE)

    def _appel(self, methode, chemin, delai=30, erreur=moteur.MoteurImpossible, **kw):
        try:
            # TestClient (Starlette) déprécie `timeout=` par requête ; httpx.Client l'accepte.
            if type(self.http).__name__ != "TestClient":
                kw["timeout"] = delai
            r = self.http.request(methode, self.adresse + chemin, headers=self.entetes, **kw)
        except httpx.HTTPError as e:
            raise erreur(message_injoignable(e),
                         **({"bloquant": True} if erreur is moteur.MoteurImpossible else {})) from e
        if r.status_code == 401:
            raise erreur("travailleur : jeton refusé", **({"bloquant": True} if erreur is moteur.MoteurImpossible else {}))
        if r.status_code == 422:
            d = r.json()
            if erreur is moteur.MoteurImpossible:
                raise erreur(d.get("erreur") or "refus du travailleur", bloquant=bool(d.get("bloquant")))
            if "a_configurer" in d and erreur is RouteCassee:
                raise ConfigurationRequise(d.get("erreur") or "réglage à faire", connecteur=d["a_configurer"] or None)
            raise erreur(d.get("erreur") or "refus du travailleur")
        if r.status_code >= 400:
            raise erreur("travailleur : HTTP %d" % r.status_code)
        return r.json()

    def etat(self):
        return self._appel("GET", "/etat")

    def verifie(self, fournisseur=None):
        """Lève si le travailleur ne répond pas ou si le moteur choisi n'y est pas connecté.
        Le moteur vérifié est ensuite celui de chaque tâche confiée par ce Distant."""
        f = moteur.fournisseur(fournisseur)
        e = self.etat()
        etat_ = (e.get("moteurs") or {}).get(f.id) or (e.get("moteur") if f.id == "claude" else None) or {}
        if not etat_.get("connecte"):
            raise moteur.MoteurImpossible("%s n'est pas connecté dans la machine virtuelle : « Se connecter » "
                                          "sur la page IA de l'application" % f.nom, bloquant=True)
        self.fournisseur = f.id
        return self

    fournisseur = None

    def _fournisseur(self):
        return self.fournisseur or moteur.fournisseur_actif()

    def executer(self, tache, message):
        import dataclasses
        if not tache.fournisseur:
            tache = dataclasses.replace(tache, fournisseur=self._fournisseur())
        d = self._appel("POST", "/moteur", delai=tache.delai_s + 60,
                        json={"tache": tache.vers_dict(), "message": message})
        r = moteur.Resultat(texte=d.get("texte") or "", brut=d.get("brut"), usage=d.get("usage") or {},
                            cout_usd=d.get("cout_usd"), session=d.get("session"), modele=d.get("modele"))
        return moteur.valider(tache, r)

    def connecteur(self, nom):
        """(ok, message) : état d'un connecteur du compte Claude, vu de la machine virtuelle."""
        d = self._appel("POST", "/connecteurs/%s/tester" % nom, delai=180)
        return d["ok"], d["message"]

    def gmail(self, mots, depuis, limite=40, longueur=1500):
        from ..courrier import CourrierImpossible
        return self._appel("POST", "/courrier/gmail", delai=900, erreur=CourrierImpossible,
                           json={"mots": list(mots), "depuis": depuis, "limite": limite, "longueur": longueur})

    def session(self, tache, consigne):
        """Lance une session du moteur choisi dans le Chrome de la VM (en arrière-plan)."""
        return self._appel("POST", "/sessions", delai=60,
                           json={"tache": tache, "consigne": consigne, "fournisseur": self._fournisseur()})

    def preremplir(self, tache):
        """Lance le pré-remplissage sans IA dans le Chrome de la VM (en arrière-plan)."""
        return self._appel("POST", "/preremplir", delai=60, json={"tache": tache})

    def rendre(self, url, attente_ms=4000, delai_ms=30000):
        """{"html", "texte"} d'une page rendue par le Chrome de la VM (contexte isolé, sans profil)."""
        return self._appel("POST", "/chrome/rendre", delai=(delai_ms + attente_ms) / 1000 + 60,
                           json={"url": url, "attente_ms": attente_ms, "delai_ms": delai_ms})

    def pdf(self, html):
        import base64
        return base64.b64decode(self._appel("POST", "/chrome/pdf", delai=120, json={"html": html})["pdf"])

    def source(self, nom, locale, connues=None):
        return SourceDistante(self, nom, locale, connues)


class SourceDistante:
    """Une source exécutée par le travailleur. `connues()` donne ce que le Mac sait déjà
    (clés source et empreintes), pour que le travailleur ne lise que les nouvelles."""

    def __init__(self, distant, nom, locale, connues=None):
        self.d, self.nom, self.connues = distant, nom, connues
        # Ce que la source tient du profil (requêtes, lieux…) : le travailleur ne l'a pas.
        self.parametres = getattr(locale, "parametres", None)
        for drapeau in ("detail_en_session", "selon_fenetre", "page_carriere"):
            if getattr(locale, drapeau, False):
                setattr(self, drapeau, True)
        self.delai = float(getattr(locale, "r", {}).get("duree_max_s", 600)) + 120

    def _appel(self, chemin, **kw):
        return self.d._appel("POST", "/sources/%s/%s" % (self.nom, chemin), erreur=RouteCassee, **kw)

    def tester(self):
        return self._appel("tester", delai=60)["n"]

    def offres(self, a_lire=None, jours=None):
        corps = {"jours": jours, "connues": self.connues() if (a_lire is not None and self.connues) else None,
                 "parametres": self.parametres() if self.parametres else None}
        return [Annonce(**a) for a in self._appel("offres", delai=self.delai, json=corps)]

    def detail(self, a):
        from dataclasses import asdict
        return Annonce(**self._appel("detail", delai=120, json=asdict(a)))


def connues(reg, source):
    """Ce que le registre connaît déjà : clés de cette source et empreintes de toutes les annonces."""
    cles = [r[0] for r in reg.cx.execute("SELECT cle FROM ids_source WHERE cle LIKE ?", (source + ":%",))]
    emps = [r[0] for r in reg.cx.execute("SELECT empreinte FROM annonces")]
    return {"cles": cles, "empreintes": emps}
