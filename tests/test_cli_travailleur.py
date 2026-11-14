"""Le travailleur n'écoute que sur 127.0.0.1 par défaut ; la connexion ne se fait que dans la VM."""

import pytest

from jobsearch import cli


def test_hote_du_travailleur_par_defaut_local(monkeypatch):
    import uvicorn
    from jobsearch.travailleur import jeton
    vus = {}
    monkeypatch.setattr(jeton, "lire", lambda creer=True: "jeton-de-test")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: vus.update(kw))
    with pytest.raises(SystemExit):
        cli.main(["travailleur"])
    assert vus["host"] == "127.0.0.1"
    with pytest.raises(SystemExit):
        cli.main(["travailleur", "--hote", "10.0.2.15"])           # la VM peut le passer explicitement
    assert vus["host"] == "10.0.2.15"


def test_connexion_hors_vm_refusee(monkeypatch, capsys):
    from jobsearch.travailleur import client
    monkeypatch.setattr(client, "distant", lambda: False)
    assert cli.cmd_connexion(None) == 1
    assert "machine virtuelle" in capsys.readouterr().out
