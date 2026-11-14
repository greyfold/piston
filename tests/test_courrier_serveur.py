"""Le serveur MCP d'Apple Mail ne vient que du réglage, jamais d'un dossier extérieur à l'application."""

import pytest

from jobsearch import courrier


def regler(monkeypatch, valeur):
    monkeypatch.setattr(courrier, "reglages", lambda: {"courrier": {"serveur_mcp": valeur}})


def test_sans_reglage_erreur_claire(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))            # même avec un plugin dans ~/.claude : ignoré
    cache = tmp_path / ".claude/plugins/cache/apple-mail-mcp/apple-mail/1.0"
    cache.mkdir(parents=True)
    (cache / "start_mcp.sh").write_text("#!/bin/bash\n")
    regler(monkeypatch, "")
    with pytest.raises(courrier.CourrierImpossible, match="courrier.serveur_mcp"):
        courrier.commande_serveur()


def test_chemin_regle(monkeypatch, tmp_path):
    script = tmp_path / "start.sh"
    script.write_text("#!/bin/bash\n")
    regler(monkeypatch, str(script))
    assert courrier.commande_serveur() == str(script)
    regler(monkeypatch, str(tmp_path / "absent.sh"))
    with pytest.raises(courrier.CourrierImpossible, match="introuvable"):
        courrier.commande_serveur()


def test_serveur_non_regle_ignore_sans_echec(reg, monkeypatch):
    """Comptes choisis mais serveur non réglé : l'étape est ignorée, l'échec du passage n'en compte pas."""
    from jobsearch import connecteurs, pipeline
    regler(monkeypatch, "")
    monkeypatch.setattr(courrier, "reglages", lambda: {"courrier": {"serveur_mcp": "", "comptes": ["compte-test"]}})
    monkeypatch.setattr(connecteurs, "actif", lambda nom: nom == "apple-mail")
    b = courrier.relever(reg)
    assert "courrier.serveur_mcp" in b["ignore"] and "pannes" not in b
    p = pipeline.Passage(reg, sources={})
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: None)
    r = p.lancer()
    assert "courrier" not in r["resume"], r          # (sans sources, le passage est « partiel » pour une autre raison)
    ligne = reg.cx.execute("SELECT compteurs FROM run_etapes WHERE run_id=? AND etape='courrier'", (r["id"],)).fetchone()
    assert "courrier.serveur_mcp" in ligne[0]


def test_serveur_regle_mais_introuvable_reste_une_erreur(reg, monkeypatch, tmp_path):
    from jobsearch import connecteurs
    monkeypatch.setattr(courrier, "reglages", lambda: {"courrier": {"serveur_mcp": str(tmp_path / "absent.sh"),
                                                                    "comptes": ["compte-test"]}})
    monkeypatch.setattr(connecteurs, "actif", lambda nom: nom == "apple-mail")
    with pytest.raises(courrier.CourrierImpossible, match="introuvable"):
        courrier.relever(reg)
