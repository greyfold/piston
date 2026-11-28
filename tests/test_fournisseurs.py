"""Les moteurs Codex, Antigravity CLI et Grok : commandes, lecture des réponses, connexion, sessions
Chrome ; les serveurs MCP des sessions (mcp_vm.py). Aucun moteur n'est lancé : leurs réponses
sont simulées."""

import io
import json
import subprocess

import pytest
from conftest import invite_linux
from pydantic import BaseModel

from jobsearch import connexion_ia, mcp_vm, moteur
from jobsearch.moteur import fournisseurs as fr


class Note(BaseModel):
    note: str
    raison: str = ""


@pytest.fixture
def neuve(monkeypatch, tmp_path):
    from jobsearch import config, profil
    monkeypatch.setattr(config, "CONFIG", config.RACINE / "config")
    monkeypatch.setattr("jobsearch.travailleur.client.distant", lambda: False)
    monkeypatch.setenv("JOBSEARCH_PROFIL", str(tmp_path / "neuf" / "profil.yaml"))
    profil.oublier()
    yield
    profil.oublier()


@pytest.fixture
def dans_la_vm(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBSEARCH_VM", "1")
    monkeypatch.setattr(fr, "DOSSIER_MOTEURS", str(tmp_path / "moteurs"))
    (tmp_path / "outils" / "bin").mkdir(parents=True)
    (tmp_path / "outils" / "bin" / "codex").write_text("")
    (tmp_path / "outils" / "antigravity").mkdir()
    (tmp_path / "outils" / "antigravity" / "agy").write_text("")
    (tmp_path / "outils" / "grok" / "bin").mkdir(parents=True)
    (tmp_path / "outils" / "grok" / "bin" / "grok").write_text("")
    monkeypatch.setattr(fr, "PREFIXE_OUTILS", str(tmp_path / "outils"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ne-doit-pas-passer")
    monkeypatch.setenv("XAI_API_KEY", "xai-ne-doit-pas-passer")
    monkeypatch.setattr(moteur, "reglage", lambda cle, defaut=None: defaut)
    return tmp_path


def faux(sortie, vus, code=0):
    def executer(cmd, **kw):
        vus.append((cmd, kw, {f: open(f).read() for f in _fichiers(cmd, kw)}))
        return subprocess.CompletedProcess(cmd, code, stdout=sortie, stderr="")
    return executer


def _fichiers(cmd, kw):
    """Les fichiers de consigne et de schéma, lus pendant que la tâche tourne (ils sont effacés après)."""
    import os
    out = [c.split("=", 1)[1].strip('"') for c in cmd if str(c).startswith("model_instructions_file=")]
    out += [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--output-schema", "--system-prompt-file")]
    out += [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--settings", "--json-schema") and str(cmd[i + 1]).startswith("/")]
    if kw.get("cwd"):
        out += [os.path.join(kw["cwd"], "AGENTS.md"), os.path.join(kw["cwd"], ".agents", "mcp_config.json")]
    return [f for f in out if os.path.exists(f)]


def tache(**kw):
    return moteur.Tache("essai", "Consigne fixe.", contexte="Profil.", schema=Note, **kw)


# --- Codex -------------------------------------------------------------------------------

CODEX_OK = "\n".join(json.dumps(e) for e in [
    {"type": "thread.started", "thread_id": "x"},
    {"type": "item.completed", "item": {"type": "error", "message": "Code Mode is unavailable"}},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": '{"note": "A", "raison": null}'}},
    {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 800, "output_tokens": 20}},
])


@invite_linux
def test_codex_tache_sans_outil_ni_cle(dans_la_vm):
    vus = []
    r = moteur.executer(tache(fournisseur="codex"), "Annonce.", faux(CODEX_OK, vus))
    assert r.objet == Note(note="A") and r.usage["input_tokens"] == 900 and r.usage["cache_read_input_tokens"] == 800
    cmd, kw, lus = vus[0]
    assert cmd[1:3] == ["exec", "--ephemeral"] and cmd[-1] == "-" and kw["input"] == "Annonce."
    assert "--ignore-user-config" in cmd and ["-s", "read-only"] == cmd[cmd.index("-s"):cmd.index("-s") + 2]
    for f in ("shell_tool", "browser_use", "computer_use", "memories", "multi_agent"):
        assert f in cmd and cmd[cmd.index(f) - 1] == "--disable"
    assert 'web_search="disabled"' in cmd and "analytics.enabled=false" in cmd
    assert "Annonce." not in " ".join(cmd) and "Profil." not in " ".join(cmd)       # rien du profil dans ps
    assert "Consigne fixe.\n\nProfil." in lus.values()
    schema = json.loads(next(v for v in lus.values() if v.startswith("{")))
    assert schema["additionalProperties"] is False and schema["required"] == ["note", "raison"]
    env = kw["env"]
    assert "OPENAI_API_KEY" not in env and "XAI_API_KEY" not in env and env["CODEX_HOME"].endswith("codex/.codex")
    assert env["HOME"].endswith("moteurs/codex")


def test_codex_non_connecte_bloque(dans_la_vm):
    sortie = json.dumps({"type": "error", "message": "unexpected status 401 Unauthorized: Missing bearer"})
    with pytest.raises(moteur.MoteurImpossible, match="Codex n'est pas connecté") as e:
        moteur.executer(tache(fournisseur="codex"), "x", faux(sortie, []))
    assert e.value.bloquant


def test_codex_recherche_web_seulement_si_demandee(dans_la_vm):
    vus = []
    t = moteur.Tache("examen", "C.", outils=["WebSearch", "WebFetch"], fournisseur="codex")
    moteur.executer(t, "x", faux(CODEX_OK, vus))
    assert 'web_search="live"' in vus[0][0]


def test_schema_strict():
    s = fr.schema_strict(Note.model_json_schema())
    assert s["required"] == ["note", "raison"] and s["properties"]["raison"]["anyOf"][1] == {"type": "null"}
    assert "default" not in json.dumps(s)


# --- Antigravity -------------------------------------------------------------------------

def resultat_agy(**r):
    return "\n".join(json.dumps(e) for e in [
        {"event": "init", "init": {"tools": [], "permission_mode": "request-review"}},
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "…"}},
        {"event": "result", "result": dict({"conversation_id": "c1", "status": "SUCCESS", "response": "",
                                            "usage": {"input_tokens": 50, "output_tokens": 7, "cache_read_tokens": 40}},
                                           **r)}])


@invite_linux
def test_antigravity_consigne_en_regle_demande_sur_l_entree(dans_la_vm):
    vus = []
    r = moteur.executer(tache(fournisseur="antigravity"), "Annonce.",
                        faux(resultat_agy(structured_output={"note": "B"}), vus))
    assert r.objet == Note(note="B") and r.usage == {"input_tokens": 50, "output_tokens": 7, "cache_read_input_tokens": 40}
    cmd, kw, lus = vus[0]
    assert cmd[-1] == "-p=" and cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert json.loads(kw["input"]) == {"event": "user", "message": {"content": "Annonce."}}
    assert "Annonce." not in " ".join(cmd) and "Profil." not in " ".join(cmd)       # rien du profil dans ps
    assert lus[kw["cwd"] + "/AGENTS.md"] == "Consigne fixe.\n\nProfil."
    reglages = json.loads(lus[cmd[cmd.index("--settings") + 1]])
    assert reglages["permissions"]["allow"] == []
    assert {"command(*)", "write_file(*)", "read_file(*)", "read_url(*)", "mcp(*)"} <= set(reglages["permissions"]["deny"])
    assert json.loads(lus[cmd[cmd.index("--json-schema") + 1]])["properties"]["note"]
    env = kw["env"]
    assert env["HOME"].endswith("moteurs/antigravity") and env["ANTIGRAVITY_SENTRY_SAMPLE_RATE"] == "0"
    assert "GEMINI_API_KEY" not in env


@invite_linux
def test_antigravity_contexte_trop_long_pour_une_regle(dans_la_vm, monkeypatch):
    monkeypatch.setattr(fr.Antigravity, "REGLE_MAX", 20)
    vus = []
    moteur.executer(tache(fournisseur="antigravity"), "Annonce.", faux(resultat_agy(structured_output={"note": "B"}), vus))
    cmd, kw, lus = vus[0]
    assert lus[kw["cwd"] + "/AGENTS.md"] == "Consigne fixe."
    assert json.loads(kw["input"])["message"]["content"].startswith("Profil.") and "Annonce." in kw["input"]


@invite_linux
def test_antigravity_recherche_web_seulement_si_demandee(dans_la_vm):
    vus = []
    t = moteur.Tache("examen", "C.", outils=["WebSearch", "WebFetch"], fournisseur="antigravity")
    moteur.executer(t, "x", faux(resultat_agy(response="ok"), vus))
    cmd, _, lus = vus[0]
    reglages = json.loads(lus[cmd[cmd.index("--settings") + 1]])
    assert reglages["permissions"]["allow"] == ["read_url(*)", "mcp(web/*)"] and "read_url(*)" not in reglages["permissions"]["deny"]
    # Antigravity n'a pas d'outil pour lire une page : la tâche reçoit `lire_page`, seul serveur MCP permis.
    serveurs = json.loads(lus[kw_cwd(vus) + "/.agents/mcp_config.json"])["mcpServers"]
    assert list(serveurs) == ["web"] and serveurs["web"]["args"] == ["-m", "jobsearch.mcp_vm", "web"]
    assert "mcp(*)" not in reglages["permissions"]["deny"] and {"command(*)", "read_file(*)"} <= set(reglages["permissions"]["deny"])


def kw_cwd(vus):
    return vus[0][1]["cwd"]


def test_serveur_web_lit_une_page_et_rien_d_autre():
    """`lire_page` (mcp_vm web) : le texte de la page, tronqué ; une adresse hors http(s) est refusée."""
    vues = []

    def rendre(url):
        vues.append(url)
        if not url.startswith(("http://", "https://")):
            raise ValueError("adresse refusée : http ou https seulement")
        return {"html": "<p>x</p>", "texte": "Conditions : robots interdits. " + "x" * (mcp_vm.PAGE_MAX + 10)}
    mcp_vm._PAGES.clear()
    appels = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
              {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "lire_page", "arguments": {"url": "https://exemple.ch/cgu"}}},
              {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "lire_page", "arguments": {"url": "https://exemple.ch/cgu", "debut": mcp_vm.PAGE_MAX - 10}}},
              {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "lire_page", "arguments": {"url": "file:///etc/passwd"}}},
              {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "commande", "arguments": {}}}]
    sortie = io.StringIO()
    mcp_vm.web(io.StringIO("\n".join(map(json.dumps, appels)) + "\n"), sortie, rendre)
    rep = {m["id"]: m for m in map(json.loads, sortie.getvalue().splitlines())}
    assert [o["name"] for o in rep[1]["result"]["tools"]] == ["lire_page"]
    texte = rep[2]["result"]["content"][0]["text"]
    assert "robots interdits" in texte and "debut=%d" % mcp_vm.MORCEAU in texte and not rep[2]["result"]["isError"]
    assert len(texte) < mcp_vm.MORCEAU + 300                              # un morceau, pas toute la page
    assert "[Fin de la page.]" in rep[5]["result"]["content"][0]["text"]  # la suite, sans rouvrir la page
    assert vues.count("https://exemple.ch/cgu") == 1
    assert rep[3]["result"]["isError"] and rep[4]["result"]["isError"]


def test_verifier_url_publique_bloque_les_adresses_internes():
    assert mcp_vm.verifier_url_publique("https://exemple.ch/cgu", resoudre=False) == "https://exemple.ch/cgu"
    for interdite in ("http://127.0.0.1:9222/json", "http://localhost:8775/", "http://[::1]:8777/",
                      "http://host.lima.internal:8775/", "http://169.254.169.254/latest", "http://192.168.1.1/",
                      "http://10.0.0.1/", "http://foo.local/", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            mcp_vm.verifier_url_publique(interdite, resoudre=False)


def test_envoyer_code_tube_ferme_ne_plante_pas():
    class TubeFerme(FauxProcessus):
        def __init__(self):
            super().__init__("")
            self.pid = None

            class Stdin:
                def write(self, _):
                    raise BrokenPipeError("tube fermé")

                def flush(self):
                    pass
            self.stdin = Stdin()

        def terminate(self):
            self.fini = -15
    connexion_ia.annuler()
    with connexion_ia._verrou:
        connexion_ia._proc = TubeFerme()
        connexion_ia._etat.update(etat="attente_code", pty=False)
    assert connexion_ia.envoyer_code("123456") is False
    connexion_ia.annuler()


def test_antigravity_non_connecte(dans_la_vm):
    sortie = json.dumps({"event": "result", "result": {"status": "ERROR", "error": "authentication failed or timed out"}})
    with pytest.raises(moteur.MoteurImpossible, match="Antigravity n'est pas connecté") as e:
        moteur.executer(tache(fournisseur="antigravity"), "x", faux(sortie, [], code=1))
    assert e.value.bloquant


def test_antigravity_erreur_de_quota(dans_la_vm):
    sortie = json.dumps({"event": "result", "result": {"status": "ERROR", "error": "RESOURCE_EXHAUSTED: quota"}})
    with pytest.raises(moteur.MoteurImpossible, match="limite d'usage de l'abonnement Google") as e:
        moteur.executer(tache(fournisseur="antigravity"), "x", faux(sortie, []))
    assert e.value.bloquant


def test_antigravity_etat_par_agy_models(dans_la_vm):
    oui = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="gemini-3-pro", stderr="")
    non = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="Please sign in", stderr="")
    assert fr.get("antigravity").etat(oui)["connecte"] and not fr.get("antigravity").etat(non)["connecte"]


# --- Grok --------------------------------------------------------------------------------

@invite_linux
def test_grok_lit_la_ligne_result(dans_la_vm):
    vus = []
    sortie = "\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init", "tools": []},
        {"type": "result", "subtype": "success", "is_error": False, "result": "", "structured_output": {"note": "C"},
         "usage": {"input_tokens": 10, "output_tokens": 2}, "session_id": ""}])
    r = moteur.executer(tache(fournisseur="grok"), "x", faux(sortie, vus))
    assert r.objet == Note(note="C") and r.usage["input_tokens"] == 10
    cmd, kw, _ = vus[0]
    assert cmd[cmd.index("--tools") + 1] == "" and "--disable-web-search" in cmd and "--no-subagents" in cmd
    coupes = cmd[cmd.index("--disallowed-tools") + 1].split(",")
    assert {"run_terminal_cmd", "read_file", "write", "web_fetch"} <= set(coupes)
    assert cmd[cmd.index("--sandbox") + 1] == "strict" and "--json-schema" in cmd
    assert kw["env"]["GROK_HOME"].endswith("grok/.grok") and "XAI_API_KEY" not in kw["env"]


def test_grok_non_connecte(dans_la_vm):
    sortie = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                         "errors": ["Not signed in. To authenticate without a browser, run: grok login --device-code"]})
    with pytest.raises(moteur.MoteurImpossible, match="Grok n'est pas connecté"):
        moteur.executer(tache(fournisseur="grok"), "x", faux(sortie, []))


def test_etats_des_moteurs(dans_la_vm):
    assert not fr.get("grok").etat()["connecte"]
    (dans_la_vm / "moteurs" / "grok" / ".grok").mkdir(parents=True)
    (dans_la_vm / "moteurs" / "grok" / ".grok" / "auth.json").write_text("{}")
    assert fr.get("grok").etat()["connecte"]
    oui = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="Logged in using ChatGPT", stderr="")
    non = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="Not logged in", stderr="")
    assert fr.get("codex").etat(oui)["connecte"] and not fr.get("codex").etat(non)["connecte"]


def test_hors_de_la_vm_aucun_moteur(monkeypatch):
    monkeypatch.delenv("JOBSEARCH_VM", raising=False)
    for f in ("codex", "antigravity", "grok"):
        with pytest.raises(moteur.LancementImpossible, match="machine virtuelle"):
            fr.get(f).executable()


def test_tache_garde_son_moteur_vers_le_travailleur():
    t = moteur.Tache("x", "C.", fournisseur="antigravity", niveau="leger")
    assert moteur.Tache.depuis_dict(t.vers_dict()) == t


def test_modele_leger_de_claude(monkeypatch):
    monkeypatch.setattr(moteur, "reglage", lambda cle, defaut=None: defaut)
    assert moteur.modele_claude(moteur.Tache("x", "C.", niveau="leger")) == "claude-sonnet-5-5"
    assert moteur.modele_claude(moteur.Tache("x", "C.")) == moteur.MODELE


# --- Sessions Chrome ---------------------------------------------------------------------

@pytest.mark.parametrize("fid", ["claude", "codex", "antigravity", "grok"])
def test_session_n_a_que_les_deux_serveurs_de_l_application(dans_la_vm, monkeypatch, fid):
    monkeypatch.setattr(moteur, "executable", lambda: "/vm/claude")
    cmd, env, cwd, entree = fr.get(fid).session("Consigne.", 7, "/donnees", dans_la_vm)
    texte = " ".join(cmd)
    if fid == "grok":
        texte += (dans_la_vm / "moteurs" / "grok" / "sessions" / "7" / ".grok" / "config.toml").read_text()
    if fid == "antigravity":
        texte += open(cwd + "/.agents/mcp_config.json").read()
        reglages = json.loads(open(cmd[cmd.index("--settings") + 1]).read())
        assert reglages["permissions"]["allow"] == ["mcp(chrome/*)", "mcp(session/*)"]
        assert "command(*)" in reglages["permissions"]["deny"] and "mcp(*)" not in reglages["permissions"]["deny"]
        assert "Consigne." in entree and "Consigne." not in " ".join(cmd)
    else:
        assert entree is None
    assert "jobsearch.mcp_vm" in texte and "--tache" in texte and "chrome-devtools-mcp" not in texte
    assert "jobsearch-session" not in texte and "Bash" not in texte


def test_filtre_chrome_retire_et_refuse():
    liste = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "click"}, {"name": "evaluate_script"}]}}
    assert [o["name"] for o in mcp_vm.filtrer_liste(liste)["result"]["tools"]] == ["click"]
    r = mcp_vm.refus({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "evaluate_script"}})
    assert r["result"]["isError"] and mcp_vm.refus({"id": 4, "method": "tools/call", "params": {"name": "click"}}) is None


def test_filtre_chrome_relaye(tmp_path):
    """Le filtre devant un faux serveur MCP : la liste revient filtrée, l'appel refusé ne part pas."""
    import sys
    serveur = tmp_path / "faux.py"
    serveur.write_text(
        "import sys, json\n"
        "for l in sys.stdin:\n"
        "    m = json.loads(l)\n"
        "    if m.get('method') == 'tools/list':\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'tools': [{'name': 'click'}, {'name': 'evaluate_script'}]}}), flush=True)\n"
        "    elif 'id' in m:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'vu': m.get('method')}}), flush=True)\n")
    entree = io.StringIO("\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "evaluate_script"}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "click"}}]) + "\n")
    sortie = io.StringIO()
    mcp_vm.chrome(entree, sortie, [sys.executable, str(serveur)])
    rep = {m["id"]: m for m in map(json.loads, sortie.getvalue().splitlines())}
    assert [o["name"] for o in rep[1]["result"]["tools"]] == ["click"]
    assert rep[2]["result"]["isError"] and rep[3]["result"] == {"vu": "tools/call"}


def test_serveur_session_note_dans_sa_tache(tmp_path, monkeypatch):
    d = tmp_path / "taches" / "5"
    d.mkdir(parents=True)
    (d / "donnees.json").write_text(json.dumps({"type": "formulaire", "empreinte": "abc", "lien": "https://x",
                                                "valeurs": {"prenom": "{PRENOM}"}}))
    monkeypatch.setattr("jobsearch.config.DATA", tmp_path)
    appels = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
              {"jsonrpc": "2.0", "method": "notifications/initialized"},
              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
              {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "formulaire", "arguments": {"annonce": "abc"}}},
              {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "formulaire", "arguments": {"annonce": "autre"}}},
              {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "etat_tache", "arguments": {"etat": "a_envoyer", "message": "civilité"}}},
              {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "envoyee", "arguments": {"annonce": "zzz"}}},
              {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "etat_tache", "arguments": {"etat": "fini"}}}]
    sortie = io.StringIO()
    mcp_vm.session("5", str(tmp_path), io.StringIO("\n".join(map(json.dumps, appels)) + "\n"), sortie)
    rep = {m["id"]: m for m in map(json.loads, sortie.getvalue().splitlines())}
    assert 0 not in rep and len(rep) == 7                                  # pas de réponse à une notification
    assert rep[1]["result"]["protocolVersion"] == "2025-03-26"
    assert {o["name"] for o in rep[2]["result"]["tools"]} == {"preuves_en_attente", "formulaire", "etat_tache",
                                                              "jobroom_fait", "envoyee"}
    assert "https://x" in rep[3]["result"]["content"][0]["text"] and not rep[3]["result"]["isError"]
    assert rep[4]["result"]["isError"] and rep[6]["result"]["isError"] and rep[7]["result"]["isError"]
    lignes = [json.loads(l) for l in (d / "evenements.jsonl").read_text().splitlines()]
    assert [(e["type"], e["etat"]) for e in lignes] == [("etat", "a_envoyer")]


# --- Connexion depuis le tableau de bord -------------------------------------------------

class FauxProcessus:
    def __init__(self, sortie, code=0):
        self.stdout, self.stdin, self.pid, self.code, self.fini = io.BytesIO(sortie.encode()), io.BytesIO(), 1, code, None

    def poll(self):
        return self.fini

    def wait(self):
        self.fini = self.code
        return self.code


def test_connexion_par_appareil_montre_le_code(monkeypatch):
    import time
    choisis = []
    monkeypatch.setattr("jobsearch.profil.poser_fournisseur", choisis.append)
    sortie = ("\x1b[1mWelcome to Codex\x1b[0m\nOpen this link in your browser:\n  \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
              "Enter this one-time code:\n  \x1b[94mABCD-12345\x1b[0m\n")
    lents = []

    class Lent(FauxProcessus):
        def wait(self):
            lents.append(connexion_ia.etat())
            self.fini = 0
            return 0
    connexion_ia.annuler()
    e = connexion_ia.demarrer("codex", popen=lambda *a, **k: Lent(sortie))
    assert e["mode"] == "appareil" and e["nom"] == "Codex"
    for _ in range(50):
        if connexion_ia.etat()["etat"] == "connecte":
            break
        time.sleep(0.02)
    assert lents[0]["etat"] == "attente_appareil" and lents[0]["code"] == "ABCD-12345"
    assert lents[0]["url"] == "https://auth.openai.com/codex/device"
    assert connexion_ia.etat()["etat"] == "connecte" and choisis == ["codex"]
    assert connexion_ia.commande("grok")[-1].endswith("'--fournisseur', 'grok']))")
    with pytest.raises(ValueError):
        connexion_ia.commande("deepseek")


def test_le_moteur_connecte_devient_celui_de_l_application(neuve):
    from jobsearch import profil
    assert moteur.fournisseur_actif() == "claude"
    profil.poser_fournisseur("antigravity")
    assert moteur.fournisseur_actif() == "antigravity" and moteur.fournisseur().nom == "Antigravity"
    profil.poser_fournisseur("claude")
    assert moteur.fournisseur_actif() == "claude" and "ia" not in profil.charger()
    with pytest.raises(profil.ProfilInvalide):
        profil.poser_fournisseur("deepseek")


def test_page_confidentialite_suit_le_moteur(neuve, monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch import profil
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    page = web.get("/confidentialite").text
    assert "compte Claude (Anthropic)" in page and "claude.ai/settings/data-privacy-controls" in page
    profil.poser_fournisseur("codex")
    page = web.get("/confidentialite").text
    assert "compte Codex (OpenAI)" in page and "abonnement ChatGPT" in page and "chatgpt.com" in page
    assert "Aider à améliorer Claude" not in page and "ne servent qu'avec Claude" in page
    profil.poser_fournisseur("grok")
    assert "compte Grok (xAI)" in web.get("/confidentialite").text
    profil.poser_fournisseur("antigravity")
    page = web.get("/confidentialite").text
    assert "compte Antigravity (Google)" in page and "Enable Telemetry" in page
