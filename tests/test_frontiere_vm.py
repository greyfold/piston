"""La frontière Mac/VM : ce que le Mac écrit dans le dossier partagé, ce qu'il y relit, ce qu'il
en efface, et ce qu'il ne croit pas sur parole."""

import json
import os
import subprocess

import pytest

from conftest import annonce
from jobsearch import agent_claude, vm
from jobsearch.notify import alerte_action as ALERTE, notifier as NOTIFIER      # les vraies, avant conftest

pytestmark = pytest.mark.usefixtures("jobroom_en_service")   # code des preuves Job-Room, coupé en version 1


@pytest.fixture
def jobroom_prepare(reg, monkeypatch):
    """Deux candidatures parties ; seule la première est à saisir dans Job-Room."""
    e1 = annonce(reg, statut="candidature", entreprise="Alpha")
    e2 = annonce(reg, statut="candidature", entreprise="Beta", titre="Autre poste")
    monkeypatch.setattr("jobsearch.followups.jobroom", lambda reg: {"a_saisir": [{"empreinte": e1}]})
    monkeypatch.setattr("jobsearch.preremplissage.jobroom", lambda reg, emp: ({"poste": "Designer"}, ["alpha"]))
    return e1, e2


def _evenements(tache, *lignes):
    f = agent_claude.dossier_tache(tache) / "evenements.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lignes), encoding="utf-8")
    return f


def test_jobroom_permis_garde_cote_mac(reg, jobroom_prepare, monkeypatch):
    e1, e2 = jobroom_prepare
    saisies = []
    monkeypatch.setattr("jobsearch.actions.appliquer", lambda reg, emp, type_, d: saisies.append(emp) or "ok")
    t = agent_claude.lancer(reg, "jobroom")
    d = agent_claude.dossier_tache(t)
    prevu = json.loads((d / "donnees.json").read_text(encoding="utf-8"))
    assert [p["empreinte"] for p in prevu["preuves"]] == [e1]
    prevu["preuves"].append({"empreinte": e2})                       # la VM réécrit donnees.json
    (d / "donnees.json").write_text(json.dumps(prevu), encoding="utf-8")
    _evenements(t, {"type": "jobroom_fait", "empreinte": e2, "compteur": "1 → 2"},
                {"type": "jobroom_fait", "empreinte": e1, "compteur": "2 → 3"})
    assert agent_claude.relever(reg) == 1 and saisies == [e1]


def test_preparer_refuse_un_dossier_ou_un_lien_deja_la(reg, tmp_path, monkeypatch):
    monkeypatch.setattr("jobsearch.preremplissage.employeur",
                        lambda reg, emp: ("https://exemple.ch/postuler", {"prenom": "Camille"}, {}))
    cible = tmp_path / "fichiers-du-mac"
    cible.mkdir()
    (cible / "donnees.json").write_text("à garder", encoding="utf-8")
    taches = agent_claude.partage() / "taches"
    taches.mkdir(parents=True)
    os.symlink(cible, taches / "7")                                  # posé par la VM
    with pytest.raises(agent_claude.PreparationRefusee):
        agent_claude.preparer(reg, 7, "formulaire", "emp1")
    assert (cible / "donnees.json").read_text(encoding="utf-8") == "à garder"
    assert sorted(p.name for p in cible.iterdir()) == ["donnees.json"]
    (taches / "8").mkdir()                                            # un vrai dossier : refusé aussi
    with pytest.raises(agent_claude.PreparationRefusee):
        agent_claude.preparer(reg, 8, "formulaire", "emp1")
    # Rien ne traîne hors du partage : le dossier préparé à part est effacé.
    assert not [p for p in agent_claude.partage().parent.iterdir() if p.name.startswith(".tache-")]


def test_preparer_refuse_taches_en_lien(reg, tmp_path, monkeypatch):
    monkeypatch.setattr("jobsearch.preremplissage.employeur",
                        lambda reg, emp: ("https://exemple.ch/postuler", {}, {}))
    cible = tmp_path / "ailleurs"
    cible.mkdir()
    agent_claude.partage().mkdir(parents=True)
    os.symlink(cible, agent_claude.partage() / "taches")
    with pytest.raises(OSError):
        agent_claude.preparer(reg, 9, "formulaire", "emp1")
    assert list(cible.iterdir()) == []


def test_lancement_refuse_puis_lien_efface(reg, tmp_path, monkeypatch):
    e = annonce(reg, statut="retenue")
    monkeypatch.setattr("jobsearch.preremplissage.employeur", lambda reg, emp: ("https://exemple.ch/p", {}, {}))
    cible = tmp_path / "cible"
    cible.mkdir()
    taches = agent_claude.partage() / "taches"
    taches.mkdir(parents=True)
    prochain = (reg.cx.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM taches_claude").fetchone()[0]
                if reg.cx.execute("SELECT name FROM sqlite_master WHERE name='taches_claude'").fetchone() else 1)
    os.symlink(cible, taches / str(prochain))
    with pytest.raises(agent_claude.LancementImpossible, match="existe déjà"):
        agent_claude.lancer(reg, "formulaire", e)
    assert not os.path.lexists(taches / str(prochain)) and cible.exists() and agent_claude.LANCEES == []


def test_releve_ne_suit_pas_un_lien(reg, tmp_path, monkeypatch):
    e = annonce(reg, statut="retenue")
    monkeypatch.setattr("jobsearch.preremplissage.employeur", lambda reg, emp: ("https://exemple.ch/p", {}, {}))
    t = agent_claude.lancer(reg, "formulaire", e)
    ailleurs = tmp_path / "evenements-du-mac.jsonl"
    ailleurs.write_text(json.dumps({"type": "etat", "etat": "arret", "message": "x"}) + "\n", encoding="utf-8")
    os.symlink(ailleurs, agent_claude.dossier_tache(t) / "evenements.jsonl")
    assert agent_claude.relever(reg) == 0 and agent_claude.lire(reg, t)["etat"] == "en_cours"


def test_envoi_signale_ne_vaut_pas_envoi(reg, monkeypatch):
    lancees = []
    monkeypatch.setattr(agent_claude, "apres_envoi", lambda reg, emp: lancees.append(emp))
    monkeypatch.setattr("jobsearch.preremplissage.employeur", lambda reg, emp: ("https://exemple.ch/p", {}, {}))
    e = annonce(reg, statut="retenue")
    t = agent_claude.lancer(reg, "formulaire", e)
    j = agent_claude.lancer(reg, "jobroom")                          # attend que Chrome se libère
    assert agent_claude.lire(reg, j)["etat"] == "en_file"
    _evenements(t, {"type": "etat", "etat": "a_envoyer"},
                {"type": "envoyee", "empreinte": e, "url": "https://www.jobup.ch/fr/application/ok/"},
                {"type": "etat", "etat": "fait"})
    agent_claude.relever(reg)
    tache = agent_claude.lire(reg, t)
    assert tache["etat"] == "envoi_a_confirmer" and "Marquer envoyée" in tache["message"]
    assert reg.get(e)["statut"] == "retenue" and lancees == []         # rien de noté, pas de Job-Room
    assert not agent_claude.vue(tache)["active"] and agent_claude.lire(reg, j)["etat"] == "en_cours"
    assert not agent_claude.dossier_tache(t).exists()                 # CV et coordonnées effacés


def test_balayage_des_dossiers_restes(reg, monkeypatch):
    e = annonce(reg, statut="retenue")
    monkeypatch.setattr("jobsearch.preremplissage.employeur", lambda reg, emp: ("https://exemple.ch/p", {}, {}))
    t = agent_claude.lancer(reg, "formulaire", e)
    reste = agent_claude.partage() / "taches" / "999"
    (reste / "documents").mkdir(parents=True)                         # tâche inconnue de la base
    agent_claude.relever(reg)
    assert not reste.exists() and agent_claude.dossier_tache(t).exists()    # la tâche en cours garde le sien
    agent_claude.noter(reg, t, "arret", "arrêtée à la main")
    agent_claude.relever(reg)
    assert not agent_claude.dossier_tache(t).exists()


def test_alertes_ouvrent_l_application_jamais_chrome(monkeypatch):
    from jobsearch import notify
    scripts, commandes = [], []
    monkeypatch.setattr(notify.subprocess, "Popen", lambda cmd, **kw: scripts.append(cmd[-1]))
    monkeypatch.setattr(notify.subprocess, "run",
                        lambda cmd, **kw: commandes.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert ALERTE("Titre", "Texte")
    assert 'application id "app.rechercheemploi.mac"' in scripts[0] and "Ouvrir l'application" in scripts[0]
    assert "Chrome" not in scripts[0] and "localhost" not in scripts[0] and "do shell script" not in scripts[0]
    NOTIFIER("Titre", "Texte", telephone=False)
    assert [c[0] for c in commandes] == ["osascript"]                  # rien d'extérieur à macOS


def test_noter_n_ouvre_ni_chrome_ni_adresse(reg, monkeypatch):
    vues = []
    monkeypatch.setattr("jobsearch.notify.alerte_action", lambda *a, **k: vues.append((a, k)))
    monkeypatch.setattr("jobsearch.preremplissage.employeur", lambda reg, emp: ("https://exemple.ch/p", {}, {}))
    t = agent_claude.lancer(reg, "formulaire", annonce(reg, statut="retenue"))
    agent_claude.noter(reg, t, "arret", "panne")
    assert vues and all(len(a) == 2 and not k for a, k in vues)


def test_politique_chrome_sans_pages_locales():
    assert "file://*" in vm.POLITIQUE_CHROME["URLBlocklist"]
    s = vm.LANCER % {"donnees": "/donnees", "code": "c", "port": 8776, "ecran": 8777, "vnc": 5901, "devtools": 9222,
                     "politique": json.dumps(vm.POLITIQUE_CHROME)}
    assert '"URLBlocklist": ["file://*"' in s                          # reposée à chaque démarrage


def test_limactl_de_l_application_sans_repli(monkeypatch, tmp_path):
    ailleurs = tmp_path / "bin"
    ailleurs.mkdir()
    (ailleurs / "limactl").write_text("")
    os.chmod(ailleurs / "limactl", 0o755)
    monkeypatch.setenv("PATH", str(ailleurs))
    monkeypatch.setenv("JOBSEARCH_LIMACTL", str(tmp_path / "absent"))     # l'application : seulement le sien
    with pytest.raises(vm.VMImpossible):
        vm.limactl()
    monkeypatch.delenv("JOBSEARCH_LIMACTL")                                # développement : celui du PATH
    assert vm.limactl() == str(ailleurs / "limactl")
    monkeypatch.setenv("PATH", str(tmp_path / "vide"))
    with pytest.raises(vm.VMImpossible):
        vm.limactl()
