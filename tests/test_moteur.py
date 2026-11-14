"""Le moteur IA : une session `claude -p` cadrée, sur un faux exécutable."""

import json
import os
import subprocess

import pytest
from pydantic import BaseModel

from jobsearch import moteur

ETAT = moteur.etat          # la vraie fonction : le conftest la remplace pendant les tests


class Reponse(BaseModel):
    sujet: str
    ville: str


def faux(sortie, code=0, stderr="", vus=None):
    """Un exécuteur qui garde la commande, la demande, l'environnement et la consigne lue."""
    def executer(cmd, input=None, env=None, cwd=None, **kw):
        fichier = cmd[cmd.index("--system-prompt-file") + 1]
        if vus is not None:
            vus.update(cmd=cmd, input=input, env=env, cwd=cwd, fichier=fichier,
                       consigne=open(fichier, encoding="utf-8").read(), mode=os.stat(fichier).st_mode & 0o777)
        out = sortie if isinstance(sortie, str) else json.dumps(sortie)
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=stderr)
    return executer


@pytest.fixture(autouse=True)
def claude_present(monkeypatch):
    monkeypatch.setattr(moteur, "executable", lambda: "/opt/claude")
    monkeypatch.setattr(moteur, "reglage", lambda cle, defaut=None: {"repli": "claude-sonnet-5-5"}.get(cle, defaut))


def succes(**k):
    return dict({"type": "result", "subtype": "success", "is_error": False, "stop_reason": "end_turn",
                 "result": "Bonjour.", "total_cost_usd": 0.01, "session_id": "s1",
                 "usage": {"input_tokens": 3, "output_tokens": 5, "cache_read_input_tokens": 1500}}, **k)


def test_tache_cadree_sans_outils_ni_reglages(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ne-doit-pas-servir")
    vus = {}
    t = moteur.Tache("essai", "Consigne fixe.", contexte="Profil de la personne.")
    r = moteur.executer(t, "La demande.", executer_=faux(succes(), vus=vus))
    cmd = vus["cmd"]
    assert cmd[:2] == ["/opt/claude", "-p"] and "--no-session-persistence" in cmd and "--safe-mode" in cmd
    assert cmd[cmd.index("--tools") + 1] == "" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5" and cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--fallback-model") + 1] == "claude-sonnet-5-5"
    assert "--allowedTools" not in cmd and "--json-schema" not in cmd and "--mcp-config" not in cmd
    # Le profil passe par un fichier du seul compte et la demande par l'entrée standard, jamais en argument.
    assert vus["consigne"] == "Consigne fixe.\n\nProfil de la personne." and vus["mode"] == 0o600
    assert vus["input"] == "La demande." and not any("Profil" in c or "demande" in c for c in cmd)
    assert not os.path.exists(vus["fichier"])                            # effacé après la session
    assert "ANTHROPIC_API_KEY" not in vus["env"]                          # l'abonnement, jamais l'API
    assert r.texte == "Bonjour." and r.objet is None and r.usage["cache_read_input_tokens"] == 1500 and r.cout_usd == 0.01


def test_reponse_structuree_validee():
    vus = {}
    t = moteur.Tache("essai", "C.", schema=Reponse, modele="haiku", effort="low")
    r = moteur.executer(t, "x", executer_=faux(succes(structured_output={"sujet": "Recrutement", "ville": "Lausanne"}),
                                               vus=vus))
    schema = json.loads(vus["cmd"][vus["cmd"].index("--json-schema") + 1])
    assert schema["required"] == ["sujet", "ville"] and vus["cmd"][vus["cmd"].index("--model") + 1] == "haiku"
    assert r.objet == Reponse(sujet="Recrutement", ville="Lausanne")


def test_reponse_hors_format_refusee():
    t = moteur.Tache("essai", "C.", schema=Reponse)
    with pytest.raises(moteur.MoteurImpossible, match="hors format"):
        moteur.executer(t, "x", executer_=faux(succes(structured_output={"sujet": "Recrutement"})))
    with pytest.raises(moteur.MoteurImpossible, match="illisible"):
        moteur.executer(t, "x", executer_=faux(succes()))


@pytest.mark.parametrize("sortie, code, stderr, motif, bloquant", [
    ("", 1, "Not logged in · Please run /login", "pas connecté", True),
    (succes(is_error=True, subtype="error_during_execution", result="Claude AI usage limit reached|1762470000"),
     1, "", "limite d'usage", True),
    (succes(stop_reason="refusal"), 0, "", "refus du modèle", False),
    (succes(stop_reason="max_tokens"), 0, "", "tronquée", False),
    (succes(is_error=True, subtype="error_max_turns", result=""), 1, "", "error_max_turns", False),
])
def test_erreurs_lisibles(sortie, code, stderr, motif, bloquant):
    with pytest.raises(moteur.MoteurImpossible, match=motif) as e:
        moteur.executer(moteur.Tache("essai", "C."), "x", executer_=faux(sortie, code, stderr))
    assert e.value.bloquant is bloquant


def test_delai_depasse():
    def lent(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    with pytest.raises(moteur.MoteurImpossible, match="pas de réponse en 5 s"):
        moteur.executer(moteur.Tache("essai", "C.", delai_s=5), "x", executer_=lent)


def test_outils_et_connecteurs_nommes_par_la_tache():
    vus = {}
    t = moteur.Tache("indeed", "C.", outils=["Read"], autorises=["mcp__claude_ai_Indeed__search_jobs"],
                     mcp={"mcpServers": {}}, sans_reglages=False)
    moteur.executer(t, "x", executer_=faux(succes(), vus=vus))
    cmd = vus["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "Read" and "--safe-mode" not in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__claude_ai_Indeed__search_jobs"
    assert json.loads(cmd[cmd.index("--mcp-config") + 1]) == {"mcpServers": {}}


def test_etat_de_la_connexion():
    statut = lambda d: (lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(d), stderr=""))
    e = ETAT(statut({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max", "email": "x@y.ch"}))
    assert e["connecte"] and e["abonnement"] == "max" and "x@y.ch" not in json.dumps(e)   # jamais l'adresse
    assert not ETAT(statut({"loggedIn": False}))["connecte"]
    api = ETAT(statut({"loggedIn": True, "authMethod": "api_key"}))
    assert not api["connecte"] and "autrement" in api["message"]


def test_hors_de_la_vm_le_mac_ne_lance_jamais_claude(monkeypatch):
    monkeypatch.undo()                                  # la vraie executable(), sans le faux chemin
    monkeypatch.delenv("JOBSEARCH_VM", raising=False)
    from jobsearch.moteur import LancementImpossible
    with pytest.raises(LancementImpossible, match="machine virtuelle"):
        moteur.executable()
    assert not ETAT()["installe"] and "machine virtuelle" in ETAT()["message"]


def test_dans_la_vm_claude_se_cherche_comme_avant(monkeypatch, tmp_path):
    monkeypatch.undo()
    faux_claude = tmp_path / "claude"
    faux_claude.write_text("#!/bin/sh\n")
    monkeypatch.setenv("JOBSEARCH_VM", "1")
    monkeypatch.setattr(moteur, "reglage", lambda cle, defaut=None: str(faux_claude) if cle == "claude" else defaut)
    assert moteur.executable() == str(faux_claude)
