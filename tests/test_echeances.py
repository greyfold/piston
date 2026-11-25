"""Le planificateur de l'application et la configuration livrée."""

import datetime as dt
from pathlib import Path

import yaml

from jobsearch import echeances

RACINE = Path(__file__).resolve().parents[1]


def _passage(reg, debut, statut="complet", declencheur="application"):
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur) VALUES (?,?,?,?)",
                   (debut, debut, statut, declencheur))
    reg.commit()


def _faux(appels):
    return {nom: (lambda reg, now, nom=nom: appels.append(nom) or {"ok": nom}) for nom in echeances.TACHES}


def test_rien_avant_l_heure_du_passage(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 05:50")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 0)) == []


def test_passage_une_fois_par_jour_puis_l_alerte(reg, tmp_path):
    matin = dt.datetime(2026, 11, 10, 6, 35)
    assert echeances.dues(reg, matin) == ["passage", "surveillance"]
    _passage(reg, "2026-11-10 06:35", statut="demarre")
    reg.poser_meta("echeance_surveillance", "2026-11-10 06:35")
    verrou = _verrou_tenu(tmp_path)                                          # le passage tourne vraiment
    assert echeances.dues(reg, matin + dt.timedelta(minutes=5)) == []        # en cours : rien
    verrou.close()
    reg.cx.execute("UPDATE runs SET statut='partiel'")
    assert echeances.dues(reg, matin + dt.timedelta(minutes=10)) == ["alerte"]
    reg.poser_meta("alerte_matin_le", "2026-11-10 06:50")
    assert echeances.dues(reg, matin + dt.timedelta(minutes=15)) == []
    # Le lendemain, avant l'heure : rien ; la surveillance revient chaque heure.
    assert echeances.dues(reg, dt.datetime(2026, 11, 11, 6, 0)) == ["surveillance"]


def test_le_passage_leger_de_l_accueil_ne_compte_pas(reg):
    _passage(reg, "2026-11-10 06:00", declencheur="accueil")
    reg.poser_meta("echeance_surveillance", "2026-11-10 07:00")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 1)) == ["passage"]


def test_mac_en_veille_a_l_heure_dite_le_passage_part_au_reveil(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 21:30")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 22, 0)) == ["passage"]


def test_executer_note_la_surveillance_et_continue_apres_un_echec(reg):
    appels = []
    taches = _faux(appels)
    taches["passage"] = lambda reg, now: (_ for _ in ()).throw(RuntimeError("panne"))
    bilan = echeances.executer(reg, dt.datetime(2026, 11, 10, 7, 0), taches)
    assert bilan["passage"] == {"erreur": "panne"} and appels == ["surveillance"]
    assert reg.meta("echeance_surveillance") == "2026-11-10 07:00"


def test_rattrapage_de_l_ia_une_fois_le_passage_fini(reg, monkeypatch, tmp_path):
    """Passage fait sans l'IA (VM éteinte, 13.11) : dès qu'une annonce à trancher est sans note, le
    rattrapage est dû, au plus toutes les 30 min, et seulement une fois le passage du jour terminé."""
    from conftest import annonce
    from jobsearch import evaluation
    monkeypatch.setattr(evaluation, "profil", lambda: "P")
    annonce(reg, texte="Texte.")
    reg.poser_meta("echeance_surveillance", "2026-11-10 07:00")
    reg.poser_meta("alerte_matin_le", "2026-11-10 06:50")
    _passage(reg, "2026-11-10 06:35", statut="demarre")
    verrou = _verrou_tenu(tmp_path)
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 0)) == []            # passage en cours
    verrou.close()
    reg.cx.execute("UPDATE runs SET statut='partiel'")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 0)) == ["rattrapage"]
    bilan = echeances.executer(reg, dt.datetime(2026, 11, 10, 7, 0))            # moteur absent en test
    assert bilan["rattrapage"]["a_evaluer"] == 1 and "ignore" in bilan["rattrapage"]
    assert reg.meta("echeance_rattrapage") == "2026-11-10 07:00"
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 20)) == []            # pas avant 30 min
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 31)) == ["rattrapage"]
    reg.cx.execute("UPDATE annonces SET statut='C'")                             # plus rien à noter
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 31)) == []


def test_rattrapage_note_et_preselectionne(reg, monkeypatch):
    from conftest import annonce
    from jobsearch import evaluation, moteur
    from test_evaluation import _ev
    monkeypatch.setattr(evaluation, "profil", lambda: "P")
    e = annonce(reg, titre="Cheffe de cuisine", texte="Cuisine.")
    monkeypatch.setattr(moteur, "ouvrir", lambda: moteur.FauxMoteur([_ev(perimetre="hors", perimetre_motif="Cuisine.")]))
    bilan = echeances._rattrapage(reg, dt.datetime(2026, 11, 10, 7, 0))
    assert bilan["evaluees"] == 1 and bilan["ecartees"] == 1 and reg.get(e)["statut"] == "C"


def test_rattrapage_passe_son_tour_pendant_un_passage(reg, monkeypatch, tmp_path):
    from conftest import annonce
    from jobsearch import evaluation, moteur
    monkeypatch.setattr(evaluation, "profil", lambda: "P")
    annonce(reg, texte="Texte.")
    ouvertures = []
    def ouvrir():
        ouvertures.append(1)
        raise moteur.MoteurImpossible("absent (test)", bloquant=True)
    monkeypatch.setattr(moteur, "ouvrir", ouvrir)
    verrou = _verrou_tenu(tmp_path)
    bilan = echeances._rattrapage(reg, dt.datetime(2026, 11, 10, 7, 0))
    assert "passage" in bilan["ignore"] and not ouvertures                       # rien traité en double
    verrou.close()
    echeances._rattrapage(reg, dt.datetime(2026, 11, 10, 7, 0))
    assert ouvertures == [1]                                                     # verrou libre : le lot est traité


def test_heure_illisible_repli_sur_6h30():
    assert echeances._heure("pas une heure") == dt.time(6, 30) and echeances._heure("7:05") == dt.time(7, 5)


def test_la_configuration_livree_ne_contient_rien_de_personnel():
    lire = lambda nom: yaml.safe_load((RACINE / "config" / nom).read_text(encoding="utf-8"))
    s, c = lire("settings.yaml"), lire("criteres.yaml")
    assert "coffre" not in s and s["notifications"]["ntfy_sujet"] == "" and s["web"]["hote"] == "127.0.0.1"
    assert s["objectifs"]["semaine_min"] == 0 and s["objectifs"]["mois_min"] == 0
    assert s["courrier"]["comptes"] == [] and s["indeed"]["lieux"] == []
    src = c["sourcing"]
    assert src["cantons"] == [] and src["jobup_lieux"] == [] and src["requetes"] == [] and src["entreprises"] == []
    assert src["linkedin_requetes"] == [] and src["linkedin_lieu"] == "" and c["familles_cibles"] == ""
    assert not [r for r in c["regles"] if r["motif"].startswith("Métier exclu")]
    canton = next(r for r in c["regles"] if r["id"] == "canton")
    assert canton["regex"] == "(?!)"                    # sans cantons choisis, la règle n'écarte rien
    assert set(lire("sources.yaml")) == {"job-room", "jobup", "jobs-ch", "indeed", "linkedin"}
    for f in (RACINE / "config").iterdir():
        assert "jeremy" not in f.read_text(encoding="utf-8").lower(), f.name


def test_le_code_ne_nomme_personne():
    for f in (RACINE / "src").rglob("*"):
        if f.suffix in (".py", ".html", ".js", ".css", ".md", ".yaml"):
            texte = f.read_text(encoding="utf-8").lower()
            assert "jeremy" not in texte and "greyfold" not in texte, f


def test_fichiers_crees_lisibles_par_le_seul_compte(tmp_path):
    import jobsearch  # noqa: F401  (pose le masque à l'import)
    from jobsearch.db import Registre
    r = Registre(tmp_path / "base.db")
    r.fermer()
    assert (tmp_path / "base.db").stat().st_mode & 0o077 == 0


def _verrou_tenu(tmp_path):
    """Un passage vivant : il tient le verrou `passage.lock` (le `reg` de conftest pointe DATA sur tmp_path)."""
    import fcntl
    f = open(tmp_path / "passage.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def test_passage_vivant_n_est_pas_interrompu(reg, tmp_path):
    reg.poser_meta("echeance_surveillance", "2026-11-10 06:40")
    _passage(reg, "2026-11-10 06:35", statut="demarre")
    verrou = _verrou_tenu(tmp_path)
    try:
        assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 45)) == []
    finally:
        verrou.close()
    assert reg.cx.execute("SELECT statut FROM runs").fetchone()[0] == "demarre"


def test_passage_tue_est_clos_puis_repart_une_seule_fois(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 06:40")
    _passage(reg, "2026-11-10 06:35", statut="demarre")                  # personne ne tient le verrou
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 45)) == ["passage"]
    r = reg.cx.execute("SELECT statut, fin FROM runs").fetchone()
    assert r["statut"] == "interrompu" and r["fin"]
    _passage(reg, "2026-11-10 06:46", statut="demarre")                  # la reprise meurt aussi
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 50)) == []   # pas de boucle
    assert "passage" not in echeances.dues(reg, dt.datetime(2026, 11, 10, 12, 0))
    assert "passage" in echeances.dues(reg, dt.datetime(2026, 11, 11, 6, 40))   # demain, on réessaie


def test_passage_interrompu_n_declenche_pas_l_alerte(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 06:40")
    _passage(reg, "2026-11-10 06:35", statut="interrompu")
    _passage(reg, "2026-11-10 06:36", statut="interrompu")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 50)) == []
