"""Commande `jobsearch` : tout ce que la plateforme sait faire, en français."""

import argparse
import json
import sys
from pathlib import Path

from .config import reglages
from .db import Registre


def _stats(reg):
    s = reg.stats()
    print("\nRegistre : %s" % reg.chemin)
    print("Total    : %d annonces\n\nPar statut" % s["total"])
    from .db import STATUTS
    for st in STATUTS:
        if st in s["par_statut"]:
            print("  %-12s %4d" % (st, s["par_statut"][st]))
    print("\nPar source")
    for src, n in s["par_source"].items():
        print("  %-14s %4d" % (src, n))
    if s["ratio_a"] is not None:
        print("\nRatio A / triées : %.1f %%  (cible 5 à 15 %%)" % s["ratio_a"])
    from .followups import rythme
    r = rythme(reg)
    print("\nPostulations")
    for nom, lib in (("semaine", "cette semaine"), ("mois", "ce mois")):
        p = r[nom]
        suivi = ("objectif %d, %s" % (p["min"], "atteint" if p["atteint"] else "il en manque %d" % p["manque"])
                 if p["actif"] else "pas d'objectif")
        print("  %-14s %4d  (%s)" % (lib, p["n"], suivi))
    print()


def cmd_passage(a):
    from .pipeline import Passage
    reg = Registre()
    r = Passage(reg, declencheur=a.declencheur, notifier_fin=not a.sans_notification).lancer()
    print("Passage n°%d : %s\n%s\nDétails : %s" % (r["id"], r["statut"], r["resume"], r["dossier"]))
    return 0 if r["statut"] == "complet" else 1


def cmd_pre_tri(a):
    from .prefilter import pre_trier
    reg = Registre()
    res = pre_trier(reg, essai=a.essai)
    print("%s %d annonces :" % ("Écarterait" if a.essai else "Écartées :", len(res)))
    for f, r in res:
        print("  %s  passe %s  %-45.45s %-28.28s %s" % (
            f["empreinte"][:8], r["passe"], f.get("titre"), f.get("entreprise"), r["motif"]))


def cmd_stats(a):
    _stats(Registre())


def cmd_relances(a):
    from .followups import jobroom, relances
    reg = Registre()
    r = relances(reg, horizon=a.horizon)
    for titre, items in (("À faire, échéance atteinte", r["dues"]),
                         ("À venir sous %d jours" % a.horizon, r["bientot"])):
        print("\n%s — %d" % (titre, len(items)))
        for f in items:
            print("  %s  %s  %-10s %-26.26s %-40.40s %s" % (
                f["empreinte"][:8], f["_echeance"], f["statut"], f.get("entreprise"),
                f.get("titre"), f.get("prochaine_action") or ""))
    jr = jobroom(reg)
    print("\nPreuves Job-Room à saisir — %d" % len(jr["a_saisir"]))
    for f in jr["a_saisir"]:
        print("  %s  avant le %s  %s — %s" % (f["empreinte"][:8], f["_limite"], f.get("entreprise"), f.get("titre")))
    print()


def cmd_tester_routes(a):
    from .pipeline import Passage
    ok = True
    for nom, src in Passage(Registre()).sources().items():
        try:
            print("%-17s en service (%d résultat)" % (nom, src.tester()))
        except Exception as e:
            ok = False
            print("%-17s EN PANNE : %s" % (nom, e))
    return 0 if ok else 1


def cmd_surveiller(a):
    from .pipeline import rappel_rythme, surveiller
    reg = Registre()
    print(json.dumps(dict(surveiller(reg), rythme=rappel_rythme(reg)), ensure_ascii=False))


def cmd_echeances(a):
    from .echeances import dues, executer
    reg = Registre()
    if a.essai:
        print(json.dumps({"dues": dues(reg)}, ensure_ascii=False))
        return 0
    print(json.dumps(executer(reg), ensure_ascii=False, default=str))
    return 0


def cmd_sauvegarder(a):
    from .pipeline import sauvegarder
    print("Sauvegarde : %s" % sauvegarder(Registre()))


def cmd_web(a):
    import uvicorn
    w = reglages().get("web") or {}
    uvicorn.run("jobsearch.web.app:app", host=a.hote or w.get("hote", "127.0.0.1"),
                port=a.port or int(w.get("port", 8765)), log_level="info")


def cmd_travailleur(a):
    """Le travailleur (dans la machine virtuelle) : tâches Claude et sources confiées."""
    import uvicorn
    from .travailleur import jeton
    if not jeton.lire(creer=False):
        print("Jeton du travailleur absent (%s) : le Mac le crée au démarrage de la machine virtuelle." % jeton.chemin())
        return 1
    uvicorn.run("jobsearch.travailleur.serveur:app", host=a.hote, port=a.port, log_level="info")


def cmd_connexion(a):
    """Connexion de Claude Code au compte Claude, dans la machine virtuelle (le Mac ne lance jamais
    `claude`). Interactif : l'adresse s'affiche, le code se colle."""
    from .travailleur import client as travailleur
    if travailleur.distant():
        from . import vm
        return vm.connexion()
    print("Claude Code ne tourne que dans la machine virtuelle : régler travailleur.mode: distant, puis relancer.")
    return 1


def cmd_vm(a):
    from . import vm
    return vm.commande(a.action)


def cmd_doublons(a):
    from . import doublons
    reg = Registre()
    ids = [reg.resoudre(x) or x for x in a.annonces or []]
    try:
        if a.action == "fusionner":
            p, s = doublons.ordonner(*(reg.get(e) or {"empreinte": e} for e in ids[:2]))
            doublons.fusionner(reg, p["empreinte"], s["empreinte"], "même offre (confirmée à la main)")
            print("Fusionnées : %s garde la fiche, %s devient doublon." % (p["empreinte"][:12], s["empreinte"][:12]))
        elif a.action == "differentes":
            doublons.differentes(reg, *ids[:2])
            print("Notées comme deux offres distinctes : plus jamais proposées.")
        elif a.action == "separer":
            print("Séparée, statut rétabli : %s" % doublons.separer(reg, ids[0]))
        elif a.action == "appliquer":
            res = doublons.traiter(reg)
            print("%d doublons sûrs fusionnés, %d paires à vérifier." % (len(res["fusionnees"]), len(res["a_verifier"])))
        else:
            res = doublons.detecter(reg)
            for titre, paires in (("Sûrs (fusionnés au prochain passage)", res["surs"]),
                                  ("À vérifier (page Sources)", res["a_verifier"])):
                print("\n%s — %d" % (titre, len(paires)))
                for p, s, raison in paires:
                    print("  %s %-11s %s — %s [%s]" % (p["empreinte"][:8], p["statut"], p["entreprise"], p["titre"], ", ".join(p["sources"])))
                    print("  %s %-11s %s — %s [%s]" % (s["empreinte"][:8], s["statut"], s["entreprise"], s["titre"], ", ".join(s["sources"])))
                    print("           → %s" % raison)
            print()
    except (ValueError, TypeError) as e:
        print("Refusé : %s" % e)
        return 1


def cmd_source(a):
    from . import repertoire
    reg = Registre()
    try:
        if a.action == "ajouter":
            sid = repertoire.ajouter(reg, a.nom, a.url or "", a.nature or "", a.acces, a.remarques or "")
            print("Source ajoutée au répertoire : %s (%s)" % (a.nom, sid))
        elif a.action == "retirer":
            repertoire.retirer(reg, a.nom)
            print("Source retirée : %s" % a.nom)
        elif a.action == "examiner":
            from .sources import examen
            try:
                e = examen.examiner(reg, a.nom)
            except examen.ExamenImpossible as ex:
                print("Examen impossible : %s" % ex)
                return 1
            print("%s\nAccès automatisé : %s%s\nAPI : %s%s\nServeur MCP : %s%s\nAccès conseillé : %s — %s" % (
                e.nature, e.automatisation, " (%s)" % e.conditions_url if e.conditions_url else "",
                e.api, " (%s)" % e.api_url if e.api_url else "", e.mcp, " (%s)" % (e.mcp_url or e.mcp_nom) if e.mcp_nom else "",
                e.acces_conseille, e.raisons))
        else:
            for sid, f in repertoire.lister(reg).items():
                print("%-22s %-28.28s %-24s %s%s" % (sid, f.get("nom"), f.get("acces") or "",
                                                     f.get("url") or "", "  (ajoutée)" if f["manuelle"] else ""))
    except repertoire.SourceRefusee as e:
        print("Refusé : %s" % e)
        return 1


def cmd_annonce(a):
    from . import ajout, evaluation
    texte = ""
    if a.texte:
        texte = sys.stdin.read() if a.texte == "-" else Path(a.texte).read_text(encoding="utf-8")
    reg = Registre()
    try:
        r = ajout.ajouter(reg, a.url or "", texte, a.entreprise or "", a.titre or "", a.lieu or "")
    except ajout.AjoutRefuse as e:
        print("Refusé : %s" % e)
        return 1
    print("%s  %s" % (r["empreinte"], r["message"]))
    if a.sans_evaluation or not ajout.a_evaluer(reg, r["empreinte"]):
        return
    try:
        bilan = evaluation.evaluer_lot(reg, [reg.get(r["empreinte"])])
    except (evaluation.EvaluationImpossible, FileNotFoundError) as e:
        print("Évaluation impossible : %s" % e)
        return
    e = evaluation.lire(reg, r["empreinte"])
    if e:
        print("Évaluée : %d/100 · profil %d %% · %d alerte(s)" % (e["note_globale"], e["adequation_profil"], e["nb_alertes"]))
    for err in bilan["erreurs"]:
        print("Évaluation impossible : %s" % err)


def cmd_originaux(a):
    from . import originaux
    from .pipeline import Passage
    reg = Registre()
    lot = originaux.a_recharger(reg)
    print("%d annonces actives sans texte original." % len(lot))
    if a.essai:
        for f in lot:
            print("  %s  %-10s %s — %s" % (f["empreinte"][:8], f["statut"], f["entreprise"], f["titre"]))
        return
    sources = Passage(reg).sources()
    for f in lot:
        ok = originaux.recharger(reg, f, sources)
        print("  %s  %s — %s" % ("ok  " if ok else "absente", f["entreprise"], f["titre"]))


def cmd_evaluer(a):
    from . import evaluation
    reg = Registre()
    texte_profil = evaluation.profil()
    if a.annonce:
        emp = reg.resoudre(a.annonce) or a.annonce
        lot = [reg.get(emp)] if reg.get(emp) else []
    else:
        lot = evaluation.a_evaluer(reg, texte_profil=texte_profil)
    print("%d annonce(s) à évaluer." % len(lot))
    if a.essai:
        for f in lot:
            print("  %s  %-9s %s — %s" % (f["empreinte"][:8], f["statut"], f["entreprise"], f["titre"]))
        return
    bilan = evaluation.evaluer_lot(reg, lot, texte_profil=texte_profil)
    for f in lot:
        e = evaluation.lire(reg, f["empreinte"])
        if e:
            print("  %3d/100  profil %3d %%  %d alerte(s)  %s — %s" % (
                e["note_globale"], e["adequation_profil"], e["nb_alertes"], f["entreprise"], f["titre"]))
    print("Bilan : %s" % {k: v for k, v in bilan.items() if k != "erreurs"})
    for err in bilan["erreurs"]:
        print("  échec : %s" % err)


def cmd_courrier(a):
    from . import courrier
    reg = Registre()
    try:
        if a.relire:
            courrier.relire_a_trancher(reg)
        if not a.essai:
            print("Courrier relevé : %s" % courrier.relever(reg))
            return
        r = reglages().get("courrier") or {}
        debut = courrier.depuis(reg)
        messages = courrier.lire_boite(courrier.mots_recherche(reg), debut, r.get("comptes") or [])
        ouvertes = reg.liste(["candidature", "entretien", "offre"])
        lies = 0
        for m in messages:
            coups = courrier.lie_aux_postes(m, ouvertes)
            if coups is None:
                continue                       # sans lien avec une candidature : jamais affiché
            lies += 1
            classe, _ = courrier.classer(m.get("subject"), m.get("content_preview"))
            print("  %s  %-9s %-34.34s %s" % (courrier._date(m), classe,
                  coups[0]["entreprise"] if len(coups) == 1 else ("%d possibles" % len(coups) if coups else "?"),
                  (m.get("subject") or "")[:70]))
        print("Depuis le %s : %d messages trouvés par les mots-clés, %d liés aux candidatures (rien n'est écrit)."
              % (debut, len(messages), lies))
    except courrier.CourrierImpossible as e:
        print("Courrier impossible : %s" % e)
        return 1


def _envoi_signale(emp, url):
    from . import preremplissage
    reg = Registre()
    try:
        f = reg.get(reg.resoudre(emp) or emp)
        return preremplissage.envoi_signale(f, url) if f else ""
    finally:
        reg.fermer()


def cmd_preremplir(a):
    from . import navigateur, preremplissage
    reg = Registre()
    try:
        if a.jobroom:
            v, jetons = preremplissage.jobroom(reg, a.annonce)
            reg.fermer()
            navigateur.preremplir_jobroom(v, jetons, apres_sauvegarde=lambda: preremplissage.noter_preuve(a.annonce))
        else:
            url, valeurs, docs = preremplissage.employeur(reg, a.annonce)
            reg.fermer()
            # La page qui semble confirmer l'envoi ne vaut pas envoi : rien n'est noté, la personne
            # clique elle-même « Marquer envoyée » (comme pour une session de la VM, agent_claude).
            navigateur.preremplir_employeur(url, valeurs, docs, a_l_envoi=lambda u: _envoi_signale(a.annonce, u))
    except (preremplissage.PreremplissageImpossible, navigateur.NavigateurOccupe) as e:
        print("Pré-remplissage impossible : %s" % e)
        return 1


def cmd_jobroom_attente(a):
    """JSON des preuves Job-Room à saisir, avec leurs valeurs (pour la session Claude)."""
    from . import followups, preremplissage
    from .navigateur import jetons_poste
    reg = Registre()
    sortie = []
    # Seulement les preuves dont le délai court encore (avant le 5 du mois suivant la postulation) :
    # les mois passés sont déjà transmis à l'ORP, une saisie après coup y serait fausse.
    for f in followups.jobroom(reg)["a_saisir"]:
        try:
            v, jetons = preremplissage.jobroom(reg, f["empreinte"])
        except preremplissage.PreremplissageImpossible:
            continue
        sortie.append({"empreinte": f["empreinte"], **v, "pays": "Suisse",
                       "anti_doublon": {"employeur": jetons, "poste": jetons_poste(v.get("poste", ""))}})
    print(json.dumps(sortie, ensure_ascii=False, indent=1))


def cmd_jobroom_fait(a):
    from . import actions
    import datetime as _dt
    reg = Registre()
    f = reg.get(reg.resoudre(a.annonce) or a.annonce)
    if not f:
        print("Annonce introuvable.")
        return 1
    note = ("Job-Room : déjà présente dans la liste (constaté par Claude), pas de nouvelle saisie" if a.deja else
            "Job-Room : saisie le %s par Claude (compteur %s, ligne constatée une seule fois)"
            % (_dt.date.today().isoformat(), a.compteur or "?"))
    print(actions.appliquer(reg, f["empreinte"], "jobroom_saisie", {"date": _dt.date.today().isoformat(), "note": note}))


def cmd_tache(a):
    from . import agent_claude
    agent_claude.noter(Registre(), a.id, a.etat, a.message)
    print("Tâche %s : %s" % (a.id, a.etat))


def cmd_formulaire(a):
    """JSON pour remplir le formulaire de l'employeur (pour la session Claude)."""
    from . import preremplissage
    reg = Registre()
    emp = reg.resoudre(a.annonce) or a.annonce
    try:
        url, valeurs, docs = preremplissage.employeur(reg, emp)
    except preremplissage.PreremplissageImpossible as e:
        print(json.dumps({"erreur": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"lien": url, "valeurs": valeurs,
                      "documents": {g: {k: str(p) for k, p in fmt.items()} for g, fmt in docs.items()}},
                     ensure_ascii=False, indent=1))


def cmd_envoyee(a):
    from . import preremplissage
    reg = Registre()
    emp = reg.resoudre(a.annonce) or a.annonce
    reg.fermer()
    print(preremplissage.noter_envoi(emp, a.url or ""))


def cmd_claude(a):
    from . import agent_claude
    reg = Registre()
    try:
        print("Session lancée (tâche %s)." % agent_claude.lancer(reg, a.type, a.annonce))
    except agent_claude.LancementImpossible as e:
        print("Lancement impossible : %s" % e)
        return 1


def cmd_jeton(a):
    from .web.auth import jeton
    print(jeton())


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch", description="Plateforme locale de recherche d'emploi.")
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("passage", help="lance le passage du matin")
    s.add_argument("--declencheur", default="manuel")
    s.add_argument("--sans-notification", action="store_true")
    s.set_defaults(f=cmd_passage)
    s = sp.add_parser("pre-tri", help="applique le pré-tri aux annonces nouvelles")
    s.add_argument("--essai", action="store_true")
    s.set_defaults(f=cmd_pre_tri)
    sp.add_parser("stats", help="état du registre").set_defaults(f=cmd_stats)
    s = sp.add_parser("relances", help="relances et preuves Job-Room dues")
    s.add_argument("--horizon", type=int, default=7)
    s.set_defaults(f=cmd_relances)
    sp.add_parser("tester-routes", help="vérifie que Job-Room et jobup répondent").set_defaults(f=cmd_tester_routes)
    sp.add_parser("surveiller", help="alerte si aucun passage depuis 26 h, et le vendredi si le minimum de postulations manque").set_defaults(f=cmd_surveiller)
    s = sp.add_parser("echeances", help="lance ce qui est dû (passage du matin, alerte, surveillance) ; "
                                        "l'application l'appelle toutes les cinq minutes")
    s.add_argument("--essai", action="store_true", help="affiche ce qui est dû sans rien lancer")
    s.set_defaults(f=cmd_echeances)
    sp.add_parser("sauvegarder", help="copie datée de la base").set_defaults(f=cmd_sauvegarder)
    s = sp.add_parser("web", help="lance le tableau de bord")
    s.add_argument("--hote")
    s.add_argument("--port", type=int)
    s.set_defaults(f=cmd_web)
    sp.add_parser("jeton", help="affiche le jeton d'accès du tableau de bord").set_defaults(f=cmd_jeton)
    s = sp.add_parser("travailleur", help="lance le travailleur (dans la machine virtuelle)")
    s.add_argument("--hote", default="127.0.0.1")   # la VM le passe explicitement (vm.py)
    s.add_argument("--port", type=int, default=8776)
    s.set_defaults(f=cmd_travailleur)
    sp.add_parser("connexion", help="connecte Claude Code au compte Claude (dans la VM)").set_defaults(f=cmd_connexion)
    s = sp.add_parser("vm", help="machine virtuelle : creer, demarrer, arreter, etat, connexion, supprimer")
    s.add_argument("action", choices=["creer", "demarrer", "arreter", "etat", "connexion", "supprimer"])
    s.set_defaults(f=cmd_vm)
    s = sp.add_parser("jobroom-attente", help="JSON des preuves Job-Room à saisir")
    s.set_defaults(f=cmd_jobroom_attente)
    s = sp.add_parser("jobroom-fait", help="note la preuve Job-Room d'une annonce")
    s.add_argument("annonce")
    s.add_argument("--compteur")
    s.add_argument("--deja", action="store_true", help="la ligne existait déjà dans Job-Room")
    s.set_defaults(f=cmd_jobroom_fait)
    s = sp.add_parser("tache", help="état d'une tâche de session Claude")
    s.add_argument("id", type=int)
    s.add_argument("etat", choices=["en_cours", "connexion", "a_envoyer", "fait", "arret"])
    s.add_argument("--message")
    s.set_defaults(f=cmd_tache)
    s = sp.add_parser("formulaire", help="JSON pour remplir le formulaire de l'employeur")
    s.add_argument("annonce")
    s.set_defaults(f=cmd_formulaire)
    s = sp.add_parser("envoyee", help="note une candidature envoyée (page de confirmation constatée)")
    s.add_argument("annonce")
    s.add_argument("--url")
    s.set_defaults(f=cmd_envoyee)
    s = sp.add_parser("claude", help="lance une session Claude (jobroom ou formulaire)")
    s.add_argument("type", choices=["jobroom", "formulaire"])
    s.add_argument("annonce", nargs="?")
    s.set_defaults(f=cmd_claude)
    s = sp.add_parser("preremplir", help="ouvre Chrome et pré-remplit le formulaire (ou Job-Room), sans envoyer")
    s.add_argument("annonce", help="empreinte de l'annonce")
    s.add_argument("--jobroom", action="store_true", help="preuve Job-Room au lieu du formulaire de l'employeur")
    s.set_defaults(f=cmd_preremplir)
    s = sp.add_parser("courrier", help="relève les réponses des employeurs (Apple Mail, lecture seule)")
    s.add_argument("--essai", action="store_true", help="lit et classe sans rien écrire")
    s.add_argument("--relire", action="store_true", help="reclasse le courrier encore à trancher")
    s.set_defaults(f=cmd_courrier)
    s = sp.add_parser("evaluer", help="note d'adéquation des annonces à trancher (Claude Code)")
    s.add_argument("annonce", nargs="?", help="empreinte d'une annonce (sinon : toutes celles à trancher)")
    s.add_argument("--essai", action="store_true", help="liste seulement, aucun appel")
    s.set_defaults(f=cmd_evaluer)
    s = sp.add_parser("originaux", help="recharge le texte original des annonces actives")
    s.add_argument("--essai", action="store_true", help="liste seulement")
    s.set_defaults(f=cmd_originaux)
    from .repertoire import ACCES
    s = sp.add_parser("doublons", help="même offre sous deux fiches : liste, appliquer, fusionner, differentes, separer")
    s.add_argument("action", nargs="?", default="liste",
                   choices=["liste", "appliquer", "fusionner", "differentes", "separer"])
    s.add_argument("annonces", nargs="*", help="empreintes (ou préfixes) : deux pour fusionner ou differentes, une pour separer")
    s.set_defaults(f=cmd_doublons)
    s = sp.add_parser("source", help="répertoire des sources : liste, ajouter, retirer, examiner (conditions, API, MCP par l'IA)")
    s.add_argument("action", choices=["liste", "ajouter", "retirer", "examiner"])
    s.add_argument("nom", nargs="?", help="nom de la source (ou son identifiant pour retirer ou examiner)")
    s.add_argument("--url")
    s.add_argument("--nature")
    s.add_argument("--acces", default="en session", choices=ACCES)
    s.add_argument("--remarques")
    s.set_defaults(f=cmd_source)
    s = sp.add_parser("annonce", help="ajoute une annonce à la main (lien ou texte), puis l'évalue")
    s.add_argument("action", choices=["ajouter"])
    s.add_argument("--url", help="lien de l'annonce (Job-Room, jobup, jobs.ch ou page de l'employeur)")
    s.add_argument("--texte", help="fichier du texte de l'annonce, ou « - » pour l'entrée standard")
    s.add_argument("--entreprise")
    s.add_argument("--titre")
    s.add_argument("--lieu")
    s.add_argument("--sans-evaluation", action="store_true")
    s.set_defaults(f=cmd_annonce)
    a = p.parse_args(argv)
    sys.exit(a.f(a) or 0)


if __name__ == "__main__":
    main()
