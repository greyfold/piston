"""Le passage du matin, en étages indépendants (piste A6).

Chaque étage écrit sa ligne dans `run_etapes` dès qu'il démarre ; une panne
d'étage est notée et les étages suivants tournent quand même. Le passage
lui-même est écrit « démarré » avant tout travail : c'est le battement de cœur
que lit la surveillance (piste A2).
"""

import datetime as dt
import json
import sqlite3
import time
import traceback
from contextlib import contextmanager
from dataclasses import asdict

from . import courrier, dossier, doublons, evaluation, followups, hote, ingest, moteur, originaux, prefilter, profil
from .config import DATA, criteres, reglages
from .connecteurs import A_CONFIGURER, CATALOGUE
from .db import maintenant
from .notify import notifier
from .sources.base import Client, ConfigurationRequise


class Delai(Exception):
    pass


def clore_interrompus(reg):
    """Clôt en « interrompu » les passages restés « démarré » dont le processus est mort.

    Tout passage tient un verrou (`hote.prendre`) sur `passage.lock` pendant qu'il travaille ; le système le
    libère à la mort du processus. Verrou libre + passage « démarré » = passage tué. On garde le
    verrou pendant la clôture, pour qu'un passage qui démarre à cet instant ne soit pas clos.
    Renvoie les identifiants clos."""
    with hote.verrou(DATA / "passage.lock") as pris:
        if not pris:
            return []                                  # un passage tourne vraiment
        ids = [r[0] for r in reg.cx.execute("SELECT id FROM runs WHERE statut='demarre'")]
        for i in ids:
            reg.cx.execute("UPDATE runs SET fin=?, statut='interrompu', "
                           "resume=COALESCE(resume, '') || ? WHERE id=?",
                           (maintenant(), "Passage interrompu avant la fin (%s en veille ou "
                                          "application fermée)." % hote.libelle("ordinateur"), i))
            reg.cx.execute("UPDATE run_etapes SET fin=?, statut='interrompu' WHERE run_id=? AND fin IS NULL",
                           (maintenant(), i))
        if ids:
            reg.commit()
        return ids


@contextmanager
def verrou_sans_attendre():
    """Prend `passage.lock` sans attendre : rend True (verrou tenu le temps du bloc) ou False si un
    passage tourne déjà (le bloc s'exécute alors sans verrou : à celui qui l'appelle de passer son tour)."""
    with hote.verrou(DATA / "passage.lock") as pris:
        yield pris


def _libelle(nom):
    return (CATALOGUE.get(nom) or {}).get("nom") or nom


class Passage:
    def __init__(self, reg, sources=None, declencheur="manuel", notifier_fin=True, leger=False):
        """`leger` : la première recherche de l'accueil, sources et pré-tri seulement
        (ni courrier, ni IA, ni dossiers, ni échéances)."""
        self.reg = reg
        self.leger = leger
        self.p = reglages().get("passage") or {}
        self.declencheur = declencheur
        self.notifier_fin = notifier_fin
        self._sources = sources
        self.echecs = []
        self.a_configurer = []       # (source, message, nouveau) : un réglage à faire, pas une panne
        self.resume = {}

    # --- Mécanique des étages ---------------------------------------------

    def _ouvrir(self):
        cur = self.reg.cx.execute("INSERT INTO runs (debut, statut, declencheur) VALUES (?,?,?)",
                                  (maintenant(), "demarre", self.declencheur))
        self.reg.commit()
        self.id = cur.lastrowid
        self.dossier = DATA / "runs" / ("%s-%d" % (dt.date.today().isoformat(), self.id))
        self.dossier.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def etape(self, nom):
        cur = self.reg.cx.execute(
            "INSERT INTO run_etapes (run_id, etape, debut, statut) VALUES (?,?,?,?)",
            (self.id, nom, maintenant(), "en_cours"))
        self.reg.commit()
        eid, compteurs = cur.lastrowid, {}
        self._debut_etape = time.monotonic()
        try:
            yield compteurs
            statut, erreur = ("tronquee" if compteurs.pop("_tronquee", False) else "ok"), None
        except Exception as e:  # une étape en panne n'arrête pas le passage
            self.reg.cx.rollback()
            statut, erreur = "echec", "%s: %s" % (type(e).__name__, e)
            self.echecs.append((nom, erreur))
            (self.dossier / ("erreur-%s.txt" % nom)).write_text(traceback.format_exc(), encoding="utf-8")
        self.reg.cx.execute("UPDATE run_etapes SET fin=?, statut=?, compteurs=?, erreur=? WHERE id=?",
                            (maintenant(), statut, json.dumps(compteurs, ensure_ascii=False, default=str),
                             erreur, eid))
        self.reg.commit()
        if statut == "tronquee":
            self.echecs.append((nom, "durée maximale atteinte, résultat partiel"))

    def _budget(self):
        if time.monotonic() - self._debut_etape > float(self.p.get("duree_max_etape_s", 900)):
            raise Delai()

    # --- Sources ----------------------------------------------------------

    def sources(self):
        """Toutes les sources du passage ; celles confiées au travailleur (machine
        virtuelle) sont remplacées par leur double distant."""
        if self._sources is None:
            from .sources.catalogue import construire
            self._sources = construire(Client(pause=float(self.p.get("pause_requetes_s", 0.8))), self.reg)
            from .travailleur import client as travailleur
            if travailleur.distant():
                from . import vm
                try:
                    vm.assurer_a_jour()          # la VM reste allumée : son code peut dater d'avant la mise à jour
                except Exception as e:
                    self.echecs.append(("machine virtuelle", "mise à jour du code impossible : %s" % str(e)[:200]))
                d = travailleur.Distant.depuis_reglages()
                for nom in travailleur.confiees():
                    if nom in self._sources:
                        self._sources[nom] = d.source(nom, self._sources[nom],
                                                      lambda nom=nom: travailleur.connues(self.reg, nom))
        return self._sources

    def _a_lire(self, a):
        """Annonce dont il faut le texte complet : inconnue, pas écartée sur l'intitulé."""
        return not ingest.connue(self.reg, a) and not prefilter.evaluer(
            {"titre": a.titre, "entreprise": a.entreprise, "requetes": a.requetes})

    def fenetre(self):
        """Jours depuis le dernier sourcing réussi (règle R-45), bornés."""
        r = self.reg.cx.execute(
            "SELECT MAX(debut) FROM run_etapes WHERE etape='sourcing' AND statut='ok'").fetchone()[0]
        if not r:
            r = self.reg.cx.execute("SELECT MAX(date) FROM veilles").fetchone()[0]
        mini, maxi = int(self.p.get("fenetre_min_jours", 2)), int(self.p.get("fenetre_max_jours", 14))
        if not r:
            return maxi
        jours = (dt.date.today() - dt.date.fromisoformat(r[:10])).days + 1
        return max(mini, min(maxi, jours))

    # --- Le passage -------------------------------------------------------

    def lancer(self):
        # Un passage resté « démarré » alors que personne ne tient plus le verrou est mort
        # (veille, fermeture, exception) : on le clôt avant d'en ouvrir un autre.
        clore_interrompus(self.reg)
        with hote.verrou(DATA / "passage.lock") as pris:
            if not pris:
                raise RuntimeError("Un passage est déjà en cours.")
            return self._lancer()

    def _lancer(self):
        self._ouvrir()
        c = criteres()["sourcing"]
        brutes, routes_ok, nouvelles = [], [], []

        with self.etape("sauvegarde") as n:
            n["fichier"] = sauvegarder(self.reg).name

        with self.etape("test_routes") as n:
            self._etats_avant = {r[0]: r[1] for r in self.reg.cx.execute("SELECT id, etat FROM sources")}
            for nom, src in self.sources().items():
                try:
                    src.tester()
                    routes_ok.append(nom)
                    self._noter_source(nom, True)
                except ConfigurationRequise as e:
                    self._noter_a_configurer(nom, e)
                except Exception as e:
                    self._noter_source(nom, False, str(e))
                    self.echecs.append(("route %s" % nom, str(e)))
            n["ok"], n["en_panne"] = routes_ok, [s for s in self.sources() if s not in routes_ok]

        with self.etape("courrier") as n:
            # Réponses des employeurs (Apple Mail par MCP, lecture seule), avant le tri.
            if self.p.get("courrier", True) and not getattr(self, "_sans_courrier", False) and not self.leger:
                try:
                    n.update(courrier.relever(self.reg))
                except courrier.CourrierNonConfigure as e:
                    n["ignore"] = str(e)[:200]      # serveur non réglé : étape ignorée, pas un échec
                except courrier.CourrierImpossible as e:
                    n["ignore"] = str(e)[:200]
                    self.echecs.append(("courrier", str(e)[:200]))

        with self.etape("sourcing") as n:
            jours = self.fenetre()
            n["fenetre_jours"] = jours
            try:
                for nom in routes_ok:
                    src = self.sources()[nom]
                    if hasattr(src, "offres"):
                        # Pages carrière (toutes les offres ouvertes, l'ingestion reconnaît
                        # celles déjà vues), Indeed (une session Claude par passage) et
                        # LinkedIn (runs Apify, sur la fenêtre du passage).
                        # Une panne ici n'emporte pas les autres sources.
                        self._budget()
                        kw = {}
                        if getattr(src, "detail_en_session", False):
                            kw["a_lire"] = self._a_lire
                        if getattr(src, "selon_fenetre", False):
                            kw["jours"] = jours
                        try:
                            res = src.offres(**kw)
                        except ConfigurationRequise as e:
                            self._noter_a_configurer(nom, e)
                            n[nom] = "à configurer"
                            continue
                        except Exception as e:
                            self._noter_source(nom, False, str(e)[:300])
                            self.echecs.append(("source %s" % nom, str(e)[:200]))
                            n[nom] = "en panne"
                            continue
                        brutes += res
                        n[nom] = len(res)
                        continue
                    # Requête par requête : une coupure (jobup, 28.10) ne perd que sa requête,
                    # et une source qui ne répond plus n'emporte pas celles qui suivent.
                    vues, ratees, suite, erreur = 0, 0, 0, None
                    recherches = [(q, q) for q in c["requetes"]] + [
                        (ent, "entreprise:" + ent) for ent in c.get("entreprises") or []]
                    for mots, etiquette in recherches:
                        self._budget()
                        try:
                            res = self._chercher(src, mots, etiquette, c, jours)
                        except Exception as e:
                            ratees, suite, erreur = ratees + 1, suite + 1, "%s: %s" % (type(e).__name__, e)
                            if suite >= int(self.p.get("pannes_requetes_max", 3)):
                                break
                            continue
                        suite = 0
                        brutes += res
                        vues += len(res)
                    n[nom] = vues
                    if ratees:
                        n.setdefault("requetes_ratees", {})[nom] = ratees
                        abandon = suite >= int(self.p.get("pannes_requetes_max", 3))
                        if abandon:
                            self._noter_source(nom, False, erreur[:300])
                            n[nom] = "en panne"
                        self.echecs.append(("source %s" % nom, "%s requête(s) ratée(s)%s, dernière erreur %s" % (
                            ratees, " puis abandon" if abandon else "", erreur[:150])))
            except Delai:
                n["_tronquee"] = True
            brutes = ingest.regrouper(brutes)
            n["uniques"] = len(brutes)
            (self.dossier / "sourcing.json").write_text(json.dumps(
                [asdict(a) for a in brutes], ensure_ascii=False, indent=1), encoding="utf-8")
            if not routes_ok:
                raise RuntimeError("aucune source joignable")

        with self.etape("details") as n:
            # Le texte complet, seulement pour les annonces inconnues qui
            # survivent au pré-tri sur le titre : quelques dizaines de requêtes.
            a_lire = [a for a in brutes if not a.detail_charge
                      and not getattr(self.sources().get(a.source), "detail_en_session", False)
                      and self._a_lire(a)]
            n["a_lire"], n["lues"], n["ratees"] = len(a_lire), 0, 0
            try:
                for a in a_lire:
                    self._budget()
                    try:
                        self.sources()[a.source].detail(a)
                        n["lues"] += 1
                    except Exception:
                        n["ratees"] += 1
            except Delai:
                n["_tronquee"] = True

        with self.etape("ingestion") as n:
            bilan = ingest.ingerer(self.reg, brutes)
            nouvelles = bilan["nouvelles"] + bilan["reapparues"]
            n.update({k: len(v) for k, v in bilan.items()})
            self._maj_sources_reussies(brutes)

        with self.etape("doublons") as n:
            # La même offre sous deux fiches (noms d'employeur écrits autrement selon la
            # plateforme) : fusion des doublons sûrs, les autres restent en attente (`jobsearch doublons`).
            res = doublons.traiter(self.reg)
            n["fusionnees"], n["a_verifier"] = len(res["fusionnees"]), len(res["a_verifier"])
            nouvelles = [e for e in nouvelles if e not in res["fusionnees"]]

        with self.etape("pre_tri") as n:
            # Toutes les annonces encore « nouvelle », pas seulement celles du jour : un filtre resserré
            # (postes du profil, exclusions) rattrape ce qu'un passage précédent avait laissé à trancher.
            ecartees = prefilter.pre_trier(self.reg)
            du_jour = set(nouvelles)
            n["ecartees"] = sum(1 for f, _ in ecartees if f["empreinte"] in du_jour)
            n["reprises"] = len(ecartees) - n["ecartees"]
            n["limites"] = sum(1 for _, r in ecartees if r.get("limite"))
            n["a_trier"] = len(nouvelles) - n["ecartees"]
            par_regle = {}
            for _, r in ecartees:
                par_regle[r["id"]] = par_regle.get(r["id"], 0) + 1
            n["par_regle"] = par_regle

        if self.leger:
            return self._clore()

        with self.etape("originaux") as n:
            # Texte original (langue d'origine) pour les annonces actives qui n'ont
            # qu'un extrait : quelques-unes par passage, les retenues d'abord.
            sources_ok = {k: v for k, v in self.sources().items() if k in routes_ok}
            lot = originaux.a_recharger(self.reg)[:int(self.p.get("originaux_par_passage", 25))]
            n["a_recharger"], n["recharges"], n["introuvables"] = len(lot), 0, 0
            try:
                for f in lot:
                    self._budget()
                    if originaux.recharger(self.reg, f, sources_ok):
                        n["recharges"] += 1
                    else:
                        n["introuvables"] += 1
            except Delai:
                n["_tronquee"] = True

        with self.etape("evaluation") as n:
            # Note d'adéquation, signaux d'alerte et pré-sélection (métier hors des postes visés)
            # des annonces à trancher, par le moteur IA.
            try:
                texte_profil = evaluation.profil()
            except FileNotFoundError as e:           # pas encore de parcours : rien à noter, rien de cassé
                n["ignoree"] = str(e)
            else:
                lot = evaluation.a_evaluer(self.reg, texte_profil=texte_profil)
                lot = lot[:int(self.p.get("evaluations_par_passage", 30))]
                n["a_evaluer"] = len(lot)
                try:
                    c = self._moteur if hasattr(self, "_moteur") else moteur.ouvrir()
                except evaluation.EvaluationImpossible as e:
                    # Sans l'IA, les annonces restent à trancher sans note ni pré-sélection : la
                    # personne doit le savoir (13.11 : 31 annonces hors métier faute de VM démarrée).
                    n["ignoree"] = str(e)
                    if lot:
                        self.echecs.append(("évaluation", "%d annonce(s) sans note ni pré-sélection de l'IA : %s"
                                            % (len(lot), e)))
                else:
                    try:
                        n.update(evaluation.evaluer_lot(self.reg, lot, c=c, texte_profil=texte_profil,
                                                        budget=self._budget))
                    except Delai:
                        n["_tronquee"] = True
                    if n.get("echecs"):
                        self.echecs.append(("évaluation", "; ".join(n["erreurs"][:3])))

        with self.etape("dossiers") as n:
            # Rattrapage : un Go dont le dossier n'a pas pu se faire au moment du clic.
            lot = dossier.a_produire(self.reg)[:int(self.p.get("dossiers_par_passage", 3))]
            n["a_produire"], n["prets"], n["a_revoir"], n["echecs"] = len(lot), 0, 0, 0
            for f in lot:
                try:
                    n["prets" if dossier.produire(self.reg, f["empreinte"]) == "pret" else "a_revoir"] += 1
                except Exception as e:
                    n["echecs"] += 1
                    self.echecs.append(("dossier %s" % f["entreprise"], str(e)[:150]))
                dossier.prevenir(self.reg, f["empreinte"])

        with self.etape("echeances") as n:
            rel = followups.relances(self.reg)
            jr = followups.jobroom(self.reg)
            n["relances_dues"], n["relances_bientot"] = len(rel["dues"]), len(rel["bientot"])
            n["jobroom_a_saisir"] = len(jr["a_saisir"])
            n["jobroom_refus_a_reporter"] = len(jr["refus_a_reporter"])
            n["jobroom_prochaine_limite"] = str(jr["a_saisir"][0]["_limite"]) if jr["a_saisir"] else None

        return self._clore()

    def _chercher(self, src, mots, etiquette, c, jours):
        if src.nom == "job-room":
            return src.chercher(mots, c["cantons"], jours, requete=etiquette)
        out = []
        from .pays import CANTONS
        for lieu in c.get("jobup_lieux") or [CANTONS[x] for x in c.get("cantons") or [] if x in CANTONS]:
            out += src.chercher(mots, lieu, jours, requete=etiquette)
        return out

    def _noter_a_configurer(self, nom, e):
        """Une source qui attend un réglage de la personne (connecteur à activer sur claude.ai) :
        le passage reste complet, la source est marquée « à configurer » et la personne n'est
        prévenue qu'une fois, la première fois que ça arrive. Dès que le réglage est fait, le
        passage suivant la retrouve en service tout seul."""
        nouveau = (getattr(self, "_etats_avant", None) or {}).get(nom) != A_CONFIGURER
        self.a_configurer.append((nom, str(e)[:200], nouveau))
        self._noter_source(nom, False, str(e)[:300], etat=A_CONFIGURER)

    def _noter_source(self, nom, ok, remarque=None, etat=None):
        self.reg.cx.execute(
            "INSERT INTO sources (id, nom, dernier_test_le, dernier_test_ok, etat, remarques) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET dernier_test_le=excluded.dernier_test_le, "
            "dernier_test_ok=excluded.dernier_test_ok, etat=excluded.etat, remarques=excluded.remarques",
            (nom, nom, maintenant(), int(ok), etat or ("en service" if ok else "en panne"), remarque))
        self.reg.commit()

    def _maj_sources_reussies(self, brutes):
        for nom in {a.source for a in brutes}:
            self.reg.cx.execute("UPDATE sources SET derniere_reussite_le=? WHERE id=?", (maintenant(), nom))

    def _clore(self):
        etapes = {r["etape"]: dict(r) for r in self.reg.cx.execute(
            "SELECT etape, statut, compteurs FROM run_etapes WHERE run_id=?", (self.id,))}
        statut = "complet" if not self.echecs else "partiel"
        c = lambda e: json.loads(etapes.get(e, {}).get("compteurs") or "{}")
        ing, pt, ech, ev = c("ingestion"), c("pre_tri"), c("echeances"), c("evaluation")
        lignes = []
        if self.echecs:
            lignes.append("⚠ " + " ; ".join("%s : %s" % e for e in self.echecs)[:300])
        for nom, message, _ in self.a_configurer:
            lignes.append("À configurer : %s, %s. La source reprendra d'elle-même une fois le "
                          "réglage fait (page %s)." % (_libelle(nom), message,
                                                       "Sources" if _lien_reglage(nom).startswith("/sources") else "Connecteurs"))
        # Parcours ou critères pas encore renseignés : la personne ne peut rien trancher, le résumé n'en parle pas.
        trancher = profil.peut_trancher()
        lignes.append("%d nouvelles annonces, %d écartées au pré-tri (dont %d limites)%s."
                      % (ing.get("nouvelles", 0), pt.get("ecartees", 0), pt.get("limites", 0),
                         ", %d à trancher" % pt.get("a_trier", 0) if trancher else ""))
        if pt.get("reprises"):
            lignes.append("%d annonce(s) d'avant, écartée(s) par le pré-tri resserré." % pt["reprises"])
        if ev.get("evaluees"):
            lignes.append("IA : %d annonce(s) notée(s), %d écartée(s) comme hors des postes visés "
                          "(à revoir dans Offres, statut C)." % (ev["evaluees"], ev.get("ecartees", 0)))
        dbl = c("doublons")
        if dbl.get("fusionnees"):
            lignes.append("Doublons entre plateformes : %d fusionnés." % dbl["fusionnees"])
        if profil.option("jobroom"):
            lignes.append("%d relances dues, %d preuves Job-Room à saisir%s."
                          % (ech.get("relances_dues", 0), ech.get("jobroom_a_saisir", 0),
                             " (avant le %s)" % ech["jobroom_prochaine_limite"]
                             if ech.get("jobroom_prochaine_limite") else ""))
        else:
            lignes.append("%d relances dues." % ech.get("relances_dues", 0))
        if trancher:
            a_trancher = self.reg.cx.execute(
                "SELECT COUNT(*) FROM annonces WHERE statut IN ('nouvelle','A','B')").fetchone()[0]
            lignes.append("En attente de ta décision : %d." % a_trancher)
        texte = "\n".join(lignes)
        self.reg.cx.execute("UPDATE runs SET fin=?, statut=?, resume=? WHERE id=?",
                            (maintenant(), statut, texte, self.id))
        self.reg.cx.execute("INSERT OR IGNORE INTO veilles (date, origine) VALUES (?, 'plateforme')",
                            (dt.date.today().isoformat(),))
        self.reg.commit()
        (self.dossier / "resume.txt").write_text(texte, encoding="utf-8")
        if self.notifier_fin:
            notifier("Piston · passage %s" % ("complet" if statut == "complet" else "partiel"),
                     texte, urgent=bool(self.echecs), lien="/sante#notifications",
                     telephone=False)    # le téléphone reçoit l'alerte du matin, pas ce bilan technique
        for nom, message, nouveau in self.a_configurer:
            if nouveau and self.notifier_fin:        # une seule fois, pas à chaque passage
                notifier("Piston · %s à brancher" % _libelle(nom),
                         "%s n'a pas pu être lu : %s. Le reste du passage s'est fait normalement."
                         % (_libelle(nom), message), lien=_lien_reglage(nom))
        return {"id": self.id, "statut": statut, "resume": texte, "dossier": str(self.dossier)}


def _lien_reglage(nom):
    """Là où se fait le réglage qui manque : la page de la source pour Indeed, sinon Connecteurs."""
    from . import connecteurs
    return connecteurs.lien(nom) if nom in connecteurs.CATALOGUE else "/connecteurs"


def sauvegarder(reg, garder=None):
    garder = garder or int((reglages().get("sauvegardes") or {}).get("garder", 30))
    dossier = DATA / "sauvegardes"
    dossier.mkdir(parents=True, exist_ok=True)
    cible = dossier / ("jobsearch-%s.db" % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    dst = sqlite3.connect(cible)
    with dst:
        reg.cx.backup(dst)
    dst.close()
    for vieux in sorted(dossier.glob("jobsearch-*.db"))[:-garder]:
        vieux.unlink()
    return cible


def surveiller(reg, maintenant_dt=None):
    """Battement de cœur : alerte si aucun passage terminé depuis le seuil."""
    s = reglages().get("surveillance") or {}
    seuil = float(s.get("alerte_apres_heures", 26))
    rappel = float(s.get("rappel_toutes_heures", 6))
    now = maintenant_dt or dt.datetime.now()
    dernier = reg.cx.execute(
        "SELECT MAX(fin) FROM runs WHERE statut IN ('complet','partiel')").fetchone()[0]
    age = (now - dt.datetime.fromisoformat(dernier)).total_seconds() / 3600 if dernier else None
    if age is not None and age < seuil:
        return {"alerte": False, "age_heures": round(age, 1)}
    derniere_alerte = reg.meta("derniere_alerte_surveillance")
    if derniere_alerte and (now - dt.datetime.fromisoformat(derniere_alerte)).total_seconds() < rappel * 3600:
        return {"alerte": True, "envoyee": False, "age_heures": age and round(age, 1)}
    texte = ("Aucun passage terminé depuis %d h. %s était-il éteint, en veille, ou l'application "
             "fermée ? Voir la page Santé du tableau de bord." % (age, hote.libelle("le").capitalize())) if age else \
        "Aucun passage n'a encore eu lieu. Lance un passage depuis l'application (%s)." % (
            "barre des menus" if hote.SYSTEME == "macos" else "icône près de l'horloge")
    notifier("Piston · passage manquant", texte, urgent=True)
    reg.poser_meta("derniere_alerte_surveillance", now.isoformat(sep=" ", timespec="minutes"))
    reg.commit()
    return {"alerte": True, "envoyee": True, "age_heures": age and round(age, 1)}


def rappel_rythme(reg, maintenant_dt=None):
    """Le vendredi (ou plus tard si le Mac dormait), une notification par semaine
    si l'objectif de postulations de la semaine n'est pas atteint (sans objectif : rien)."""
    now = maintenant_dt or dt.datetime.now()
    r = followups.rythme(reg, now.date())
    s = r["semaine"]
    heure = str((reglages().get("objectifs") or {}).get("alerte_vendredi", "09:00"))
    if not r["alerte"] or (now.weekday() == 4 and now.strftime("%H:%M") < heure):
        return {"alerte": False, "semaine": s["n"]}
    if reg.meta("derniere_alerte_rythme") == s["debut"].isoformat():
        return {"alerte": True, "envoyee": False, "semaine": s["n"]}
    notifier("Piston · objectif de la semaine",
             "%d postulation%s cette semaine, l'objectif est %d : il en manque %d."
             % (s["n"], "s" if s["n"] > 1 else "", s["min"], s["manque"]), urgent=True)
    reg.poser_meta("derniere_alerte_rythme", s["debut"].isoformat())
    reg.commit()
    return {"alerte": True, "envoyee": True, "semaine": s["n"]}
