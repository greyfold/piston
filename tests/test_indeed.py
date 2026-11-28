import datetime as dt

import pytest

from conftest import fixture
from jobsearch.fingerprint import empreinte
from jobsearch.sources import indeed
from jobsearch.sources.base import ConfigurationRequise, RouteCassee

AUJ = dt.date(2026, 10, 26)
REGLAGES = {"actif": True, "lieux": ["Genève"], "recherches_max": 40, "details_max": 25,
            "anciennete_max_jours": 120, "duree_max_s": 600, "pause_tours_s": 0, "pause_absent_s": 0}


class FausseSession:
    """Rejoue une session `claude -p` : réponses brutes des outils, comme dans le flux JSON."""

    def __init__(self, recherche=None, detail=None, outils=("mcp__claude_ai_Indeed__search_jobs",
                                                             "mcp__claude_ai_Indeed__get_job_details")):
        self.recherche = recherche if recherche is not None else fixture("indeed_recherche.json")
        self.detail = detail if detail is not None else fixture("indeed_detail.json")
        self.outils, self.messages, self.ferme = list(outils), [], False
        self.serveurs = {"claude.ai Indeed": "connected"}

    def tour(self, message, delai):
        self.messages.append(message)
        if "search_jobs" in message:
            lignes = [l for l in message.split("\n") if l.startswith("- search:")]
            out = []
            for l in lignes:
                q, _, lieu = l[len("- search: "):].partition(" ; location: ")
                out.append({"outil": "mcp__claude_ai_Indeed__search_jobs", "erreur": False,
                            "entree": {"search": q, "location": lieu, "country_code": "CH"},
                            "texte": indeed._texte_outil(__import__("json").dumps(self.recherche))})
            return out
        ids = [l[2:] for l in message.split("\n") if l.startswith("- JOBSEARCH_")]
        return [{"outil": "mcp__claude_ai_Indeed__get_job_details", "erreur": False,
                 "entree": {"job_id": i}, "texte": self.detail["result"] if i == "JOBSEARCH_100014" else ""}
                for i in ids]

    def fermer(self):
        self.ferme = True


def _src(session, reglages=None, requetes=("Head of Product",), cantons=("VD", "GE")):
    return indeed.Indeed(dict(REGLAGES, **(reglages or {})), requetes, cantons, ouvrir=lambda: session,
                         connecte=lambda: True, aujourdhui=AUJ)


def test_lecture_d_une_recherche():
    res = indeed.lire_recherche(fixture("indeed_recherche.json")["result"])
    assert len(res) == 10
    ident, f = res[1]
    a = indeed.convertir(f, "indeed:Head of Product")
    assert ident == "JOBSEARCH_100014" and a.entreprise == "Proton" and a.titre == "Head of Product (Mail)"
    assert a.lieu == "Geneva GE" and a.canton == "GE" and a.publiee_le == "2026-07-30"
    assert a.id_source == empreinte("Proton", "Head of Product (Mail)") and a.url.startswith("https://to.indeed.com/")
    assert indeed.convertir(res[0][1]).taux_max == 100
    assert indeed.convertir(res[3][1]).permanent is True


def test_lecture_d_un_detail():
    ident, texte = indeed.lire_detail(fixture("indeed_detail.json")["result"])
    assert ident == "JOBSEARCH_100014" and texte.startswith("Join Proton") and "**" not in texte
    assert "Manage, coach, and grow a team of Product Managers" in texte


def test_offres_ancienneté_canton_et_detail_seulement_pour_a_lire():
    s = FausseSession()
    offres = _src(s).offres(a_lire=lambda a: a.entreprise == "Proton" and "Product" in a.titre)
    # Blockstream (mars) dépasse 120 jours ; les neuf autres sont à Genève.
    assert len(offres) == 9 and all(a.canton == "GE" for a in offres)
    assert "Blockstream" not in {a.entreprise for a in offres}
    lues = [a for a in offres if a.detail_charge]
    assert [a.titre for a in lues] == ["Head of Product (Mail)"] and lues[0].texte.startswith("Join Proton")
    assert "- JOBSEARCH_100014" in s.messages[1] and "JOBSEARCH_100015" not in s.messages[1]
    assert s.ferme


def test_meme_annonce_par_deux_requetes_fusionnee():
    offres = _src(FausseSession(), requetes=("Head of Product", "Product Manager")).offres(a_lire=lambda a: False)
    assert len(offres) == 9
    assert offres[0].requetes == ["indeed:Head of Product", "indeed:Product Manager"]


def test_canton_hors_romandie_ecarte():
    assert _src(FausseSession(), cantons=("VD",)).offres(a_lire=lambda a: False) == []


def test_budget_de_recherches():
    src = _src(FausseSession(), {"lieux": ["Lausanne", "Genève"], "recherches_max": 3},
               requetes=("UX Lead", "Head of Design", "Product Manager"))
    toutes = [(q, l) for q in ("UX Lead", "Head of Design", "Product Manager") for l in ("Lausanne", "Genève")]
    jour1 = src.recherches()
    src.aujourdhui = AUJ + dt.timedelta(days=1)
    jour2 = src.recherches()
    assert len(jour1) == 3 and set(jour1) | set(jour2) == set(toutes)     # deux jours couvrent tout


def test_connecteur_absent_de_claude_code():
    s = FausseSession(outils=["mcp__claude_ai_Gmail__search"])
    s.serveurs, s.tour = {"claude.ai Gmail": "connected"}, lambda message, delai: []
    with pytest.raises(ConfigurationRequise, match="pas activé"):
        _src(s).offres()
    assert s.ferme


def test_indeed_pas_encore_branche_au_premier_tour_puis_present():
    """Le connecteur se branche en arrière-plan : un premier tour trop tôt ne voit pas Indeed
    (machine virtuelle, 09.11). On rouvre une fois avant de conclure qu'il manque."""
    absente = FausseSession(outils=[])
    absente.serveurs, absente.tour = {}, lambda message, delai: []
    presente = FausseSession()
    sessions = iter([absente, presente])
    src = indeed.Indeed(REGLAGES, ("Head of Product",), ("VD", "GE"), ouvrir=lambda: next(sessions),
                        connecte=lambda: True, aujourdhui=AUJ)
    assert src.offres() and absente.ferme


def test_les_sessions_indeed_attendent_les_connecteurs(monkeypatch):
    from jobsearch.moteur import session
    vus = {}
    class Faux:
        def __init__(self, cmd, **kw):
            vus.update(kw.get("env") or {})
            raise RuntimeError("arrêt")
    monkeypatch.setattr(session.subprocess, "Popen", Faux)
    with pytest.raises(RuntimeError):
        indeed.SessionClaude(["claude"])
    assert vus.get("MCP_CONNECTION_NONBLOCKING") == "false"


def test_connecteur_a_reautoriser():
    s = FausseSession()
    s.serveurs, s.tour = {"claude.ai Indeed": "needs-auth"}, lambda message, delai: []
    with pytest.raises(ConfigurationRequise, match="réautoriser"):
        _src(s).offres()


def test_connexion_refusee_un_instant_puis_relance():
    """« Try again in 15 seconds » : la première session n'a pas Indeed, la seconde oui."""
    ratee = FausseSession()
    ratee.serveurs, ratee.tour = {"claude.ai Indeed": "failed"}, lambda message, delai: []
    bonne = FausseSession()
    sessions = [ratee, bonne]
    src = indeed.Indeed(dict(REGLAGES, pause_essais_s=0), ["Head of Product"], ["GE"],
                        ouvrir=lambda: sessions.pop(0), connecte=lambda: True, aujourdhui=AUJ)
    assert len(src.offres(a_lire=lambda a: False)) == 9 and ratee.ferme and bonne.ferme


def test_aucune_recherche_aboutie():
    s = FausseSession()
    s.serveurs, s.tour = {"claude.ai Indeed": "failed"}, lambda message, delai: []
    with pytest.raises(RouteCassee, match="4 essais"):
        _src(s, {"pause_essais_s": 0}).offres()


def test_details_par_paquets():
    s = FausseSession()
    _src(s, {"details_par_tour": 3}).offres(a_lire=lambda a: True)
    assert len(s.messages) == 1 + 3          # neuf annonces, trois par tour


def test_tester_signale_claude_deconnecte(monkeypatch, claude_dans_la_vm):
    src = indeed.Indeed(REGLAGES, [], [], connecte=lambda: False)
    with pytest.raises(RouteCassee, match="pas connecté"):
        src.tester()
    with pytest.raises(RouteCassee, match="désactivé"):
        indeed.Indeed({"actif": False}, [], []).tester()


def test_flux_json_de_claude(tmp_path):
    """La vraie lecture du flux `claude -p --output-format stream-json`, sur un faux exécutable."""
    import json
    import stat
    import sys
    ev = [{"type": "system", "subtype": "init", "tools": ["mcp__claude_ai_Indeed__search_jobs"]},
          {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1",
                                                          "name": "mcp__claude_ai_Indeed__search_jobs",
                                                          "input": {"search": "Head of Product"}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                                     "content": [{"type": "text", "text": json.dumps(fixture("indeed_recherche.json"))}]}]}},
          {"type": "result", "subtype": "success", "is_error": False, "result": "fini"}]
    faux = tmp_path / "faux_claude.py"
    faux.write_text("import sys\nsys.stdin.readline()\n" + "".join(
        "print(%r, flush=True)\n" % json.dumps(e) for e in ev) + "sys.stdin.read()\n")
    s = indeed.SessionClaude([sys.executable, str(faux)])
    try:
        rep = s.tour("go", 20)
    finally:
        s.fermer()
    assert s.outils == ["mcp__claude_ai_Indeed__search_jobs"]
    assert len(rep) == 1 and rep[0]["entree"] == {"search": "Head of Product"}
    assert len(indeed.lire_recherche(rep[0]["texte"])) == 10


def test_passage_indeed_en_panne_n_emporte_pas_les_autres(reg, monkeypatch):
    from jobsearch import courrier, evaluation, pipeline
    from jobsearch.sources.base import Annonce
    from test_pipeline_web import FausseSource
    monkeypatch.setattr(courrier, "lire_boite", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: None)
    monkeypatch.setattr("jobsearch.moteur.ouvrir", lambda: (_ for _ in ()).throw(evaluation.EvaluationImpossible("test")))
    s = FausseSession()
    s.tour = lambda message, delai: (_ for _ in ()).throw(RouteCassee("indeed : la session Claude s'est arrêtée"))
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    r = pipeline.Passage(reg, sources={"job-room": jr, "indeed": _src(s)}).lancer()
    assert r["statut"] == "partiel" and "indeed" in r["resume"]
    assert reg.stats()["par_statut"].get("nouvelle", 0) + reg.stats()["par_statut"].get("A", 0) >= 1


def test_passage_ingere_indeed_sans_relire_le_detail(reg, monkeypatch, claude_dans_la_vm):
    from jobsearch import courrier, evaluation, pipeline
    from test_pipeline_web import FausseSource
    monkeypatch.setattr(courrier, "lire_boite", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: None)
    monkeypatch.setattr("jobsearch.moteur.ouvrir", lambda: (_ for _ in ()).throw(evaluation.EvaluationImpossible("test")))
    r = pipeline.Passage(reg, sources={"job-room": FausseSource("job-room"), "indeed": _src(FausseSession())}).lancer()
    assert r["statut"] == "complet", r
    f = reg.get(empreinte("Proton", "Head of Product (Mail)"))
    assert f and f["sources"] == ["indeed"] and f["texte"].startswith("Join Proton")


def test_delai_conseille_lu_dans_le_journal_de_debogage(tmp_path):
    j = tmp_path / "mcp.txt"
    j.write_text('[ERROR] MCP server "claude.ai Indeed" Connection failed (-32429): Rate limit exceeded '
                 'for account 1 on toolset claude. Try again in 42 seconds.\n')
    s = indeed.SessionClaude.__new__(indeed.SessionClaude)
    s.debogage = j
    assert s.attente_conseillee() == 42
    s.debogage = tmp_path / "absent.txt"
    assert s.attente_conseillee() is None


def test_relance_apres_le_delai_d_indeed(monkeypatch):
    attentes = []
    monkeypatch.setattr(indeed.time, "sleep", attentes.append)
    ratee = FausseSession()
    ratee.serveurs, ratee.tour = {"claude.ai Indeed": "failed"}, lambda message, delai: []
    ratee.attente_conseillee = lambda: 42
    sessions = [ratee, FausseSession()]
    src = indeed.Indeed(REGLAGES, ["Head of Product"], ["GE"], ouvrir=lambda: sessions.pop(0),
                        connecte=lambda: True, aujourdhui=AUJ)
    assert len(src.offres(a_lire=lambda a: False)) == 9
    assert attentes == [47.0]                 # 42 s demandées par Indeed + 5 s de marge


def test_recherches_par_paquets_et_limite_en_cours_de_route(monkeypatch):
    monkeypatch.setattr(indeed.time, "sleep", lambda s: None)
    s = FausseSession()
    vraie = s.tour
    appels = []

    def tour(message, delai):
        appels.append(message)
        if "search_jobs" in message and len(appels) >= 3:
            raise RouteCassee("indeed : la session Claude s'est arrêtée")
        return vraie(message, delai)
    s.tour = tour
    src = _src(s, {"recherches_par_tour": 1, "lieux": ["Genève"]}, requetes=("A", "B", "C", "D"))
    offres = src.offres(a_lire=lambda a: False)
    # Deux paquets passés, le troisième casse : on garde ce qui a été trouvé.
    assert len(offres) == 9 and offres[0].requetes == ["indeed:A", "indeed:B"]


def test_sans_poste_vise_rien_a_chercher():
    """Sans requête, aucune session ouverte : un réglage à faire, pas une panne (avant : IndexError)."""
    src = indeed.Indeed({"lieux": ["Lausanne"]}, [], ["VD"], ouvrir=lambda: pytest.fail("session ouverte"))
    with pytest.raises(ConfigurationRequise, match="aucun poste visé"):
        src.offres()


def test_parametres_du_mac_repris_par_le_travailleur():
    """La VM n'a pas le profil : la source du travailleur reprend requêtes, cantons et lieux du Mac."""
    mac = indeed.Indeed({"lieux": ["Lausanne", "Sion"]}, ["Product Owner"], ["VD", "VS"])
    vm = indeed.Indeed({"modele": "haiku"}, [], [])
    copie = vm.selon(mac.parametres())
    assert copie.recherches() == [("Product Owner", "Lausanne"), ("Product Owner", "Sion")]
    assert copie.cantons == {"VD", "VS"} and copie.r["modele"] == "haiku" and vm.requetes == []


def test_appels_refuses_pour_limite_refaits_apres_l_attente(monkeypatch):
    """Comme le 24.11 : la limite d'Indeed tombe au milieu d'un paquet. Les recherches et les détails
    refusés sont refaits une fois, après l'attente demandée, au lieu d'être perdus."""
    attentes = []
    monkeypatch.setattr(indeed.time, "sleep", attentes.append)
    s = FausseSession()
    s.attente_conseillee = lambda: 30
    vraie, refuses = s.tour, set()
    limite = "Rate limit exceeded for account 1 on toolset claude. Try again in 30 seconds."

    def tour(message, delai):
        out = vraie(message, delai)
        genre = "recherches" if "search_jobs" in message else "details"
        if genre not in refuses:                             # le premier tour de chaque genre : dernier appel refusé
            refuses.add(genre)
            out[-1] = dict(out[-1], erreur=True, texte=limite)
        return out
    s.tour = tour
    src = _src(s, {"lieux": ["Genève", "Lausanne"]}, requetes=("A",))
    offres = src.offres(a_lire=lambda a: a.entreprise == "Proton" and "Product" in a.titre)
    assert "location: Lausanne" in s.messages[1] and "location: Genève" not in s.messages[1]   # seule la refusée
    assert offres[0].requetes == ["indeed:A"] and any(a.detail_charge for a in offres)          # détail repris
    assert attentes.count(35.0) == 2                         # 30 s demandées par Indeed + 5 s de marge
