"""Sessions Claude dans le Chrome de la machine virtuelle : lancement, file, consignes, relève."""

import json

import pytest

from conftest import annonce
from jobsearch import actions, agent_claude, session_vm

pytestmark = pytest.mark.usefixtures("jobroom_en_service")   # code des preuves Job-Room, coupé en version 1


@pytest.fixture
def sans_preparation(monkeypatch):
    """Le dossier de la tâche sans le profil ni les documents (testés à part)."""
    def preparer(reg, tache, type_, empreinte):
        d = agent_claude.dossier_tache(tache)
        d.mkdir(parents=True, exist_ok=True)
        contenu = ({"type": type_, "preuves": []} if type_ == "jobroom"
                   else {"type": type_, "empreinte": empreinte, "lien": "https://exemple.ch/postuler", "valeurs": {}})
        (d / "donnees.json").write_text(json.dumps(contenu), encoding="utf-8")
        return contenu
    monkeypatch.setattr(agent_claude, "preparer", preparer)


def _noter_dans_la_vm(tache, *args, monkeypatch):
    """Ce que la session écrit par `jobsearch-session …`, dans la VM."""
    monkeypatch.setenv("JOBSEARCH_TACHE", str(tache))
    monkeypatch.setattr("jobsearch.config.DATA", agent_claude.partage())
    return session_vm.main(list(args))


def test_lancer_une_seule_session_jobroom(reg, sans_preparation):
    t1 = agent_claude.lancer(reg, "jobroom")
    t2 = agent_claude.lancer(reg, "jobroom")                  # déjà en cours : pas de seconde session
    assert t1 == t2 and len(agent_claude.LANCEES) == 1
    tache, type_, consigne = agent_claude.LANCEES[0]
    assert tache == t1 and type_ == "jobroom"
    # Les outils des deux serveurs MCP de l'application, mêmes noms pour tous les moteurs ; aucune commande.
    assert "preuves_en_attente" in consigne and "etat_tache" in consigne and "Tâche %d " % t1 in consigne
    assert "jobsearch-session" not in consigne and "`" not in consigne
    assert "mot de passe" in consigne and "serveur « chrome »" in consigne and "claude-in-chrome" not in consigne
    assert agent_claude.derniere(reg, "jobroom")["session"] == "vm"


def test_echec_de_lancement_note(reg, sans_preparation, monkeypatch):
    def refus(tache, type_, consigne):
        raise RuntimeError("travailleur injoignable : la machine virtuelle est-elle démarrée ?")
    monkeypatch.setattr(agent_claude, "lancer_dans_la_vm", refus)
    with pytest.raises(agent_claude.LancementImpossible, match="machine virtuelle"):
        agent_claude.lancer(reg, "jobroom")
    t = agent_claude.derniere(reg, "jobroom")
    assert t["etat"] == "arret" and "machine virtuelle" in t["message"]


def test_etats_et_notifications(reg, sans_preparation, monkeypatch):
    vus = []
    monkeypatch.setattr("jobsearch.notify.notifier", lambda titre, texte, urgent=False: vus.append((titre, texte, urgent)))
    t = agent_claude.lancer(reg, "jobroom")
    agent_claude.noter(reg, t, "connexion", "Job-Room")
    assert agent_claude.derniere(reg, "jobroom")["etat"] == "connexion"
    assert vus[-1][0] == "Job-Room : connecte-toi" and "Navigateur" in vus[-1][1] and vus[-1][2]
    with pytest.raises(ValueError):
        agent_claude.noter(reg, t, "nimporte")


def test_marquer_envoyee_lance_la_preuve(reg, monkeypatch):
    lances = []
    monkeypatch.setattr(agent_claude, "lancer", lambda r, t, e=None, executer=None: lances.append(t) or 1)
    e = annonce(reg, statut="retenue")
    actions.appliquer(reg, e, "candidature", {"date": "2026-10-26"})
    assert agent_claude.apres_envoi(reg, e) == 1 and lances == ["jobroom"]
    monkeypatch.setattr(agent_claude, "reglage", lambda cle, defaut=True: False)
    assert agent_claude.apres_envoi(reg, e) is None


def test_formulaire_consigne_ne_pas_envoyer(reg, sans_preparation):
    e = annonce(reg, statut="retenue", entreprise="ACG")
    agent_claude.lancer(reg, "formulaire", e)
    consigne = agent_claude.LANCEES[0][2]
    assert "SANS L'ENVOYER" in consigne and "N'envoie pas" in consigne and "envoyee avec l'annonce %s" % e in consigne
    assert "upload_file" in consigne


def test_connecte_lit_auth_status(monkeypatch, claude_dans_la_vm):
    import subprocess
    oui = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": true}', stderr="")
    non = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": false}', stderr="")
    assert agent_claude.connecte(oui) and not agent_claude.connecte(non)


def test_tache_active_puis_perdue(reg, sans_preparation):
    t = agent_claude.lire(reg, agent_claude.lancer(reg, "jobroom"))
    assert agent_claude.active(t) and agent_claude.vue(t)["libelle"] == "L'IA travaille…"
    assert not agent_claude.active({**t, "etat": "arret"}) and not agent_claude.active(None)
    assert not agent_claude.active({**t, "debut": "2026-01-01 08:00"})          # session perdue : le bouton se libère


def test_consigne_formulaire_listes_et_valeurs_par_defaut(reg, sans_preparation):
    agent_claude.lancer(reg, "formulaire", annonce(reg, statut="retenue"))
    consigne = agent_claude.LANCEES[0][2]
    assert "listes déroulantes" in consigne and "civilité n'est pas une question sensible" in consigne
    assert "date par défaut du portail" in consigne


def test_une_seule_session_dans_chrome_les_autres_en_file(reg, sans_preparation):
    e = annonce(reg, statut="retenue")
    f = agent_claude.lancer(reg, "formulaire", e)
    agent_claude.noter(reg, f, "a_envoyer", "relis")                  # le formulaire attend Camille : Chrome reste tenu
    j = agent_claude.lancer(reg, "jobroom")
    assert agent_claude.lire(reg, j)["etat"] == "en_file" and len(agent_claude.LANCEES) == 1
    assert agent_claude.lancer(reg, "jobroom") == j                    # pas deux fois dans la file
    assert agent_claude.active(agent_claude.lire(reg, j))
    agent_claude.noter(reg, f, "fait")                                 # Chrome se libère : la preuve part
    assert agent_claude.lire(reg, j)["etat"] == "en_cours" and agent_claude.LANCEES[-1][1] == "jobroom"


def test_consigne_un_seul_onglet(reg, sans_preparation):
    agent_claude.lancer(reg, "jobroom")
    c = agent_claude.LANCEES[0][2]
    assert "UN SEUL onglet" in c and "new_page" in c and "toujours dans ce même onglet" in c
    assert "toutes les minutes" in c and "RECHARGE la page" in c and "etat_tache « en_cours », et reprends" in c


def test_file_relancee_si_l_occupante_a_disparu(reg, sans_preparation):
    e = annonce(reg, statut="retenue")
    f = agent_claude.lancer(reg, "formulaire", e)
    j = agent_claude.lancer(reg, "jobroom")
    assert agent_claude.lire(reg, j)["etat"] == "en_file"
    reg.cx.execute("UPDATE taches_claude SET debut='2026-01-01 08:00', maj='2026-01-01 08:00' WHERE id=?", (f,))
    reg.commit()                                                       # la session du formulaire s'est perdue
    assert agent_claude.lancer(reg, "jobroom") == j and agent_claude.lire(reg, j)["etat"] == "en_cours"


# --- Le dossier partagé : ce que la session lit, ce qu'elle note, ce que le Mac applique ----------

def test_preparer_formulaire_copie_les_documents_et_rien_d_autre(reg, monkeypatch, tmp_path):
    cv = tmp_path / "Camille-Exemple-CV.pdf"
    cv.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr("jobsearch.preremplissage.employeur",
                        lambda reg, emp: ("https://exemple.ch/postuler", {"prenom": "Camille"}, {"cv": {"pdf": cv}}))
    c = agent_claude.preparer(reg, 7, "formulaire", "emp1")
    d = agent_claude.dossier_tache(7)
    assert d == agent_claude.partage() / "taches" / "7"
    assert (d / "documents" / "cv.pdf").read_bytes() == b"%PDF-1.4"
    assert c["documents"] == {"cv": {"pdf": "/donnees/taches/7/documents/cv.pdf"}}
    assert sorted(p.name for p in d.iterdir()) == ["documents", "donnees.json"]


def test_la_session_lit_sa_tache_et_note_ses_evenements(reg, sans_preparation, monkeypatch, capsys):
    e = annonce(reg, statut="retenue")
    t = agent_claude.lancer(reg, "formulaire", e)
    assert _noter_dans_la_vm(t, "formulaire", e, monkeypatch=monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["lien"] == "https://exemple.ch/postuler"
    assert _noter_dans_la_vm(t, "formulaire", "autre", monkeypatch=monkeypatch) == 1      # pas une autre annonce
    assert _noter_dans_la_vm(t, "tache", str(t + 5), "fait", monkeypatch=monkeypatch) == 1     # pas une autre tâche
    assert _noter_dans_la_vm(t, "tache", str(t), "a_envoyer", "--message", "relis", monkeypatch=monkeypatch) == 0
    assert _noter_dans_la_vm(t, "envoyee", "autre", monkeypatch=monkeypatch) == 1
    lignes = (agent_claude.dossier_tache(t) / "evenements.jsonl").read_text().splitlines()
    assert [json.loads(x)["type"] for x in lignes] == ["etat"]


def test_releve_applique_et_verifie(reg, sans_preparation, monkeypatch):
    envois = []
    monkeypatch.setattr("jobsearch.preremplissage.noter_envoi", lambda emp, url: envois.append((emp, url)))
    e = annonce(reg, statut="retenue")
    t = agent_claude.lancer(reg, "formulaire", e)
    f = agent_claude.dossier_tache(t) / "evenements.jsonl"
    lignes = [{"type": "etat", "etat": "a_envoyer", "message": "relis"},
              {"type": "etat", "etat": "pirate"},                                   # état inconnu : ignoré
              {"type": "etat", "etat": "envoi_a_confirmer"},                        # pas un état de session
              {"type": "envoyee", "empreinte": "une-autre-annonce", "url": "x"},    # hors de la tâche : ignoré
              {"type": "jobroom_fait", "empreinte": e}]                             # pas une tâche Job-Room : ignoré
    f.write_text("\n".join(json.dumps(x) for x in lignes) + "\npas du json\n", encoding="utf-8")
    assert agent_claude.relever(reg) == 1
    assert agent_claude.lire(reg, t)["etat"] == "a_envoyer"
    assert agent_claude.relever(reg) == 0                                           # déjà relevé
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "etat", "etat": "en_cours"}) + "\n" + '{"type": "etat", "etat": "fa')
    assert agent_claude.relever(reg) == 1                                           # ligne inachevée : plus tard
    with open(f, "a", encoding="utf-8") as fh:
        fh.write('it"}\n')
    assert agent_claude.relever(reg) == 1 and agent_claude.lire(reg, t)["etat"] == "fait"
    assert envois == [] and not agent_claude.dossier_tache(t).exists()              # terminée : dossier effacé


def test_preremplissage_sans_claude_passe_par_la_vm(reg, sans_preparation, monkeypatch):
    from jobsearch import preremplissage
    from jobsearch.db import Registre
    chemin = reg.cx.execute("PRAGMA database_list").fetchone()[2]          # la même base de test
    monkeypatch.setattr("jobsearch.db.Registre", lambda *a, **k: Registre(chemin))
    e = annonce(reg, statut="retenue")
    t = preremplissage.lancer_en_arriere_plan(e)
    assert agent_claude.LANCEES[-1][:2] == (t, "pre-formulaire")
    assert agent_claude.vue(agent_claude.lire(reg, t))["libelle"].startswith("Pré-remplissage ouvert")
    with pytest.raises(preremplissage.PreremplissageImpossible, match="occupé"):
        preremplissage.lancer_en_arriere_plan(e, jobroom_=True)


def test_balayage_garde_la_tache_active_recente_et_note_arret_la_perdue(reg, sans_preparation, monkeypatch):
    monkeypatch.setattr("jobsearch.notify.notifier", lambda *a, **k: None)
    monkeypatch.setattr("jobsearch.notify.alerte_action", lambda *a, **k: None)
    recente = agent_claude.lancer(reg, "jobroom")
    agent_claude.noter(reg, recente, "connexion")
    agent_claude._balayer(reg)
    assert agent_claude.dossier_tache(recente).exists() and agent_claude.lire(reg, recente)["etat"] == "connexion"
    reg.cx.execute("UPDATE taches_claude SET maj='2026-01-01 08:00', debut='2026-01-01 08:00' WHERE id=?", (recente,))
    reg.commit()
    agent_claude._balayer(reg)
    assert not agent_claude.dossier_tache(recente).exists()
    assert agent_claude.lire(reg, recente)["etat"] == "arret"
