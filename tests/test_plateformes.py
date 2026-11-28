"""Le monorepo : le code commun ne dépend d'aucun système.

Ce qui touche l'ordinateur hôte passe par `jobsearch.hote` ; ce qui pilote la machine virtuelle,
par l'hyperviseur de chaque système (`jobsearch/vm/lima.py`, `jobsearch/vm/wsl.py`) ; le code qui
ne tourne que dans l'invité Linux (travailleur, sessions) peut parler Linux. Le reste est commun.
"""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "jobsearch"
RACINE = SRC.parents[1]

# Ce qui peut dépendre d'un système : l'hôte, chaque hyperviseur, et le code de l'invité Linux.
PERMIS = ("hote/", "vm/lima.py", "vm/wsl.py", "travailleur/", "session_vm.py", "mcp_vm.py", "preremplir_vm.py",
          "vm.py")       # vm.py : jusqu'à son découpage en vm/ (un hyperviseur par système)

MOTIFS = [
    r"\bimport fcntl\b", r"\bimport msvcrt\b", r"\bwinreg\b", r"\bwindll\b", r"\bosascript\b",
    r"[\"']security[\"']", r"\bsys\.platform\b", r"Library/Application Support", r"\bLOCALAPPDATA\b",
    r"\bkillpg\b", r"\bstart_new_session\b", r"\bscutil\b", r"[\"']defaults[\"']", r"\blimactl[\"' ]",
    r"[\"']wsl(\.exe)?[\"']", r"\bpowershell\b",
]


def _lignes_de_documentation(source):
    """Les lignes des docstrings : on y nomme les systèmes sans en dépendre."""
    out = set()
    for n in ast.walk(ast.parse(source)):
        corps = getattr(n, "body", None)
        if isinstance(corps, list) and corps and isinstance(corps[0], ast.Expr) \
                and isinstance(getattr(corps[0], "value", None), ast.Constant) and isinstance(corps[0].value.value, str):
            out.update(range(corps[0].lineno, corps[0].end_lineno + 1))
    return out


def _fichiers():
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if not rel.startswith(PERMIS):
            yield rel, f


@pytest.mark.parametrize("motif", MOTIFS)
def test_le_code_commun_ne_depend_d_aucun_systeme(motif):
    fautifs = []
    for rel, f in _fichiers():
        source = f.read_text(encoding="utf-8")
        doc = _lignes_de_documentation(source)
        for n, ligne in enumerate(source.splitlines(), 1):
            if n in doc:
                continue
            code = ligne.split("#", 1)[0]
            if re.search(motif, code):
                fautifs.append("%s:%d %s" % (rel, n, ligne.strip()))
    assert not fautifs, "à faire passer par jobsearch.hote ou vm/<hyperviseur>.py :\n" + "\n".join(fautifs)


def test_chaque_systeme_a_son_dossier():
    for os_ in ("macos", "windows", "linux"):
        assert (RACINE / "plateformes" / os_).is_dir()
        assert (SRC / "hote" / ("%s.py" % os_)).is_file()
    assert (RACINE / "plateformes" / "macos" / "app" / "Package.swift").is_file()


def test_chaque_hote_fournit_la_meme_interface():
    from jobsearch.hote import linux, macos, windows
    attendu = {"dossier_application", "prendre", "lacher", "Coffre", "notifier", "alerte_action", "region",
               "nom_reseau", "options_groupe", "terminer_groupe", "LIBELLES", "APPLICATION",
               "partage_deposer", "partage_purger", "partage_lire", "partage_lister"}
    cles = set(macos.LIBELLES)
    for m in (macos, windows, linux):
        assert attendu <= set(dir(m)), m.__name__
        assert set(m.LIBELLES) == cles, m.__name__
        for nom in ("lire", "ecrire", "retirer"):
            assert callable(getattr(m.Coffre, nom))
