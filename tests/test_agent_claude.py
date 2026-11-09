import subprocess

import pytest

from conftest import annonce
from jobsearch import actions, agent_claude


def test_lancer_une_seule_session_jobroom(reg):
    appels = []

    def executer(cmd, **kw):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · 1a2b3c4d\n", stderr="")
    t1 = agent_claude.lancer(reg, "jobroom", executer=executer)
    t2 = agent_claude.lancer(reg, "jobroom", executer=executer)          # déjà en cours : pas de seconde session
    assert t1 == t2 and len(appels) == 1
    cmd = appels[0]
    assert cmd[0].endswith("claude") and cmd[1] == "--bg" and "--chrome" in cmd and "dontAsk" in cmd
    consigne = cmd[2]
    assert "jobroom-attente" in consigne and "tache %d" % t1 in consigne and "mot de passe" in consigne
    assert "createIfEmpty: true" in consigne and "ton groupe d'onglets" in consigne
    outils = cmd[cmd.index("--allowedTools") + 1]
    js = agent_claude.commande_jobsearch()
    assert js.startswith(("/", "'/")) and "%s tache 1 " % js in consigne and "`uv run" not in consigne
    assert "mcp__claude-in-chrome" in outils and "Bash(%s jobroom-fait:*)" % js in outils
    assert "Bash(*)" not in outils
    assert agent_claude.derniere(reg, "jobroom")["session"] == "1a2b3c4d"


def test_echec_de_lancement_note(reg):
    def executer(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Not logged in · Run /login")
    with pytest.raises(agent_claude.LancementImpossible, match="login"):
        agent_claude.lancer(reg, "jobroom", executer=executer)
    t = agent_claude.derniere(reg, "jobroom")
    assert t["etat"] == "arret" and "login" in t["message"]


def test_etats_et_notifications(reg, monkeypatch):
    vus = []
    monkeypatch.setattr("jobsearch.notify.notifier", lambda titre, texte, urgent=False: vus.append((titre, urgent)))
    t = agent_claude.lancer(reg, "jobroom")
    agent_claude.noter(reg, t, "connexion", "Job-Room")
    assert agent_claude.derniere(reg, "jobroom")["etat"] == "connexion" and vus[-1] == ("Job-Room : connecte-toi", True)
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


def test_formulaire_consigne_ne_pas_envoyer(reg):
    appels = []

    def executer(cmd, **kw):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · 9f9f9f9f\n", stderr="")
    e = annonce(reg, statut="retenue", entreprise="ACG")
    agent_claude.lancer(reg, "formulaire", e, executer=executer)
    consigne = appels[0][2]
    assert "SANS L'ENVOYER" in consigne and "N'envoie pas" in consigne and "jobsearch envoyee %s" % e in consigne
    assert "Formulaire · ACG" in appels[0]


def test_connecte_lit_auth_status():
    oui = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": true}', stderr="")
    non = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout='{"loggedIn": false}', stderr="")
    assert agent_claude.connecte(oui) and not agent_claude.connecte(non)


def test_tache_active_puis_perdue(reg):
    t = agent_claude.lire(reg, agent_claude.lancer(reg, "jobroom"))
    assert agent_claude.active(t) and agent_claude.vue(t)["libelle"] == "Claude travaille…"
    assert not agent_claude.active({**t, "etat": "arret"}) and not agent_claude.active(None)
    assert not agent_claude.active({**t, "debut": "2026-01-01 08:00"})          # session perdue : le bouton se libère


def test_consigne_formulaire_listes_et_valeurs_par_defaut(reg):
    appels = []
    def executer(cmd, **kw):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · 1a2b3c4d\n", stderr="")
    agent_claude.lancer(reg, "formulaire", annonce(reg, statut="retenue"), executer=executer)
    consigne = appels[0][2]
    assert "listes déroulantes" in consigne and "civilité n'est pas une question sensible" in consigne
    assert "date par défaut du portail" in consigne


def test_une_seule_session_dans_chrome_les_autres_en_file(reg, monkeypatch):
    lances = []

    def executer(cmd, **kw):
        lances.append(cmd[cmd.index("--name") + 1])
        return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · 1a2b3c4d\n", stderr="")
    e = annonce(reg, statut="retenue")
    monkeypatch.setattr(agent_claude, "EXECUTER", executer)
    f = agent_claude.lancer(reg, "formulaire", e)
    agent_claude.noter(reg, f, "a_envoyer", "relis")                  # le formulaire attend Camille : Chrome reste tenu
    j = agent_claude.lancer(reg, "jobroom")
    assert agent_claude.lire(reg, j)["etat"] == "en_file" and len(lances) == 1
    assert agent_claude.lancer(reg, "jobroom") == j                    # pas deux fois dans la file
    assert agent_claude.active(agent_claude.lire(reg, j))
    agent_claude.noter(reg, f, "fait")                                 # Chrome se libère : la preuve part
    assert agent_claude.lire(reg, j)["etat"] == "en_cours" and lances[-1] == "Job-Room · preuves"


def test_consigne_un_seul_onglet(reg):
    appels = []
    def executer(cmd, **kw):
        appels.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="backgrounded · 1a2b3c4d\n", stderr="")
    agent_claude.lancer(reg, "jobroom", executer=executer)
    c = appels[0][2]
    assert "UN SEUL onglet" in c and "c'est dans cet onglet vide que tu travailles" in c
    assert "ne laisse jamais d'onglet vide" in c and "toujours dans ce même onglet" in c
    assert "toutes les minutes" in c and "RECHARGE la page" in c and "en_cours`, et reprends" in c


def test_file_relancee_si_l_occupante_a_disparu(reg, monkeypatch):
    e = annonce(reg, statut="retenue")
    f = agent_claude.lancer(reg, "formulaire", e)
    j = agent_claude.lancer(reg, "jobroom")
    assert agent_claude.lire(reg, j)["etat"] == "en_file"
    reg.cx.execute("UPDATE taches_claude SET debut='2026-01-01 08:00', maj='2026-01-01 08:00' WHERE id=?", (f,))
    reg.commit()                                                       # la session du formulaire s'est perdue
    assert agent_claude.lancer(reg, "jobroom") == j and agent_claude.lire(reg, j)["etat"] == "en_cours"


def test_uv_trouve_meme_avec_le_path_minimal_des_sessions(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")        # celui du démon des sessions Claude
    assert agent_claude.uv().endswith("/uv")


def test_dans_l_application_la_session_lance_le_python_embarque(monkeypatch, tmp_path):
    """Sans .venv à la racine (le code embarqué dans l'application) : le Python qui tourne,
    par son chemin complet, entre guillemets s'il contient une espace."""
    monkeypatch.setattr(agent_claude, "RACINE", tmp_path)
    monkeypatch.setattr("sys.executable", "/Applications/Recherche emploi.app/Contents/Resources/python/bin/python3")
    js = agent_claude.commande_jobsearch()
    assert js == "'/Applications/Recherche emploi.app/Contents/Resources/python/bin/python3' -m jobsearch"
    assert "Bash(%s formulaire:*)" % js in agent_claude.outils(js)
