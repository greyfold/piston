"""La couche hôte : ce que chaque système fournit, testé sans le système (faux appels)."""

import subprocess
import threading

import pytest

from jobsearch import cles, hote
from jobsearch.hote import commun, linux, macos, windows

CLE = "apify_api_" + "x" * 40 + "abcd"


def service(code):
    import httpx
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(code, json={})))


def test_systeme_selon_la_plateforme():
    assert hote.systeme("darwin") == "macos" and hote.systeme("win32") == "windows"
    assert hote.systeme("linux") == "linux" and hote.SYSTEME in ("macos", "windows", "linux")


def test_code_pays():
    assert commun.code_pays("fr_CH.UTF-8") == "CH" and commun.code_pays("fr-CH") == "CH"
    assert commun.code_pays("en_FR") == "FR" and commun.code_pays("fr") == "" and commun.code_pays("") == ""
    assert commun.region_env({"LANG": "de_DE.UTF-8"}) == "DE"


def test_region_de_windows():
    assert windows.region({}, lambda: "fr-CH") == "CH"
    assert windows.region({"LANG": "it_IT"}, lambda: "") == "IT"


def test_verrou_exclusif_puis_libre(tmp_path):
    f = tmp_path / "x.lock"
    with hote.verrou(f) as pris:
        assert pris
        with open(f, "a+") as autre:
            assert not hote.prendre(autre)          # une autre ouverture ne l'a pas
        assert hote.tenu(f)
    assert not hote.tenu(f)
    with hote.verrou(f) as pris:
        assert pris


def test_verrou_attendu(tmp_path):
    f = tmp_path / "x.lock"
    tenu = open(f, "a+")
    assert hote.prendre(tenu)
    libere = threading.Timer(0.3, lambda: (hote.lacher(tenu), tenu.close()))
    libere.start()
    with hote.verrou(f, attendre=True) as pris:
        assert pris
    libere.join()


class FausseApiWindows:
    """Le gestionnaire d'identification en mémoire, par la même interface que ApiIdentification."""

    def __init__(self):
        self.entrees, self.appels = {}, []

    def lire(self, cible):
        return self.entrees.get(cible)

    def ecrire(self, cible, compte, valeur):
        self.appels.append(("ecrire", cible, compte))
        self.entrees[cible] = valeur
        return True

    def supprimer(self, cible):
        return self.entrees.pop(cible, None) is not None


def test_cle_dans_le_gestionnaire_d_identification():
    api = FausseApiWindows()
    coffre = windows.Coffre(api)
    msg = cles.enregistrer("apify", CLE, coffre=coffre, client=service(200))
    assert "…abcd" in msg and api.entrees == {"jobsearch-apify": CLE}
    assert api.appels == [("ecrire", "jobsearch-apify", "jobsearch")]
    assert cles.etat("apify", coffre)["presente"] and cles.lire("apify", coffre) == CLE
    cles.retirer("apify", coffre)
    assert api.entrees == {} and cles.lire("apify", coffre) is None


def test_cle_dans_secret_service():
    entrees, vus = {}, []

    def faux(cmd, input=None, **kw):
        vus.append((cmd, input))
        if cmd[1] == "lookup":
            v = entrees.get(cmd[3])
            return subprocess.CompletedProcess(cmd, 0 if v else 1, stdout=v or "", stderr="")
        if cmd[1] == "store":
            entrees[cmd[cmd.index("service") + 1]] = input
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "clear":
            entrees.pop(cmd[3], None)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    coffre = linux.Coffre(faux)
    cles.enregistrer("apify", CLE, coffre=coffre, client=service(200))
    assert entrees == {"jobsearch-apify": CLE}
    assert all(CLE not in " ".join(cmd) for cmd, _ in vus)        # la clé passe par l'entrée standard


def test_toast_echappe_et_ouvre_l_application():
    x = windows.xml_toast('Titre & "guillemets"', "a < b '@ fin", urgent=True, bouton="Ouvrir l'application")
    assert "&amp;" in x and "&quot;" in x and "&lt;" in x and "&apos;@" in x and "'@" not in x
    assert 'launch="piston://ouvrir"' in x and 'scenario="reminder"' in x and "Notification.Reminder" in x
    simple = windows.xml_toast("T", "x")
    assert "<actions>" not in simple and "scenario" not in simple


def test_groupe_windows_par_taskkill():
    vus = []

    class P:
        pid = 4242

        def terminate(self):
            vus.append("terminate")
    windows.terminer_groupe(P(), executer=lambda cmd, **kw: vus.append(cmd))
    assert vus == [["taskkill", "/PID", "4242", "/T", "/F"]]
    assert windows.options_groupe()["creationflags"] & windows.CREATE_NEW_PROCESS_GROUP


def test_dossier_application_par_systeme(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert windows.dossier_application() == tmp_path / "Local" / "Recherche emploi"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert linux.dossier_application() == tmp_path / "share" / "recherche-emploi"
    assert macos.dossier_application().parts[-3:] == ("Library", "Application Support", "Recherche emploi")


def test_libelles_dans_les_pages(monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setitem(appmod.tpl.env.globals, "ordi", windows.LIBELLES)
    from fastapi.testclient import TestClient
    page = TestClient(appmod.app).get("/confidentialite").text
    assert "sur ton PC" in page and "ton Mac" not in page and "Outlook" in page


@pytest.mark.parametrize("module", [macos, windows, linux])
def test_le_courrier_local_est_nomme_ou_vide(module):
    assert isinstance(module.LIBELLES["courrier"], str)


@pytest.mark.parametrize("module", [windows] + ([] if hote.SYSTEME == "windows" else [hote._impl]))
def test_partage_sans_lien(module, tmp_path):
    """Le dossier partagé avec la VM : dépôt, lecture, liste, purge ; un lien posé par la VM est refusé."""
    import os
    racine = tmp_path / "partage"
    prepare = tmp_path / "prepare"
    prepare.mkdir()
    (prepare / "evenements.jsonl").write_bytes(b'{"a": 1}\n')
    module.partage_deposer(racine, "taches", prepare, "7")
    with pytest.raises(FileExistsError):
        autre = tmp_path / "autre"
        autre.mkdir()
        module.partage_deposer(racine, "taches", autre, "7")
    assert module.partage_lire(racine, "taches", "7", "evenements.jsonl", 100) == b'{"a": 1}\n'
    assert module.partage_lister(racine, "taches") == ["7"]
    secret = tmp_path / "secret.txt"
    secret.write_text("coordonnées", encoding="utf-8")
    try:
        os.symlink(secret, racine / "taches" / "7" / "lien.jsonl")
        os.symlink(tmp_path, racine / "taches" / "8")
    except (OSError, NotImplementedError):
        pytest.skip("liens symboliques indisponibles")
    assert module.partage_lire(racine, "taches", "7", "lien.jsonl", 100) is None
    assert module.partage_lire(racine, "taches", "8", "secret.txt", 100) is None
    assert module.partage_purger(racine, "taches", "8") and secret.exists()       # le lien seul est retiré
    assert module.partage_purger(racine, "taches", "7") and not (racine / "taches" / "7").exists()
    assert secret.exists() and module.partage_lister(racine, "taches") == []


def test_dossier_protege_sous_windows(tmp_path, monkeypatch):
    vus = []
    monkeypatch.setenv("USERDOMAIN", "PC")
    monkeypatch.setattr("getpass.getuser", lambda: "camille")
    assert windows.proteger_dossier(tmp_path / "d", executer=lambda cmd, **kw: vus.append(cmd)
                                    or subprocess.CompletedProcess(cmd, 0))
    cmd = vus[0]
    assert cmd[:4] == ["icacls", str(tmp_path / "d"), "/inheritance:r", "/grant:r"]
    assert "PC\\camille:(OI)(CI)F" in cmd and "*S-1-5-18:(OI)(CI)F" in cmd and not any("Everyone" in c for c in cmd)
