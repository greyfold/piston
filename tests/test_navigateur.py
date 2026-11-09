"""Pré-remplissage sur un formulaire local, dans Chrome sans fenêtre : `uv run pytest -m navigateur`."""

import pytest

from jobsearch import navigateur

pytestmark = pytest.mark.navigateur

FORMULAIRE = """<!doctype html><html><body>
<form id="f" onsubmit="window.envoye = true; return false;">
  <label>Photo <input type="file" name="photo"></label>
  <label for="r">Resume/CV *</label><input type="file" id="r" name="resume" required>
  <label>Cover Letter <input type="file" name="cover_letter"></label>
  <label>First Name * <input name="first_name" required></label>
  <label>Last Name * <input name="last_name" required></label>
  <input type="email" name="mail" placeholder="Email">
  <label>Phone <input type="tel" name="phone"></label>
  <label>LinkedIn Profile <input name="urls[LinkedIn]"></label>
  <label>Country <select name="country"><option value="">--</option><option>France</option><option>Switzerland</option></select></label>
  <label>Salary expectations <input name="salary"></label>
  <label>Why do you want to join us? <textarea name="why" maxlength="80"></textarea></label>
  <label>Gender * <select name="gender" required><option value="">--</option><option>F</option><option>M</option></select></label>
  <label><input type="checkbox" name="consent" required> I agree to the privacy policy</label>
  <button type="submit">Submit application</button>
</form></body></html>"""


def test_remplit_sans_envoyer(tmp_path):
    from playwright.sync_api import sync_playwright
    cv, lettre = tmp_path / "cv.pdf", tmp_path / "lettre.docx"
    cv.write_bytes(b"%PDF-1.4")
    lettre.write_bytes(b"PK")
    valeurs = {"prenom": "Camille", "nom": "Exemple", "email": "j@example.ch", "telephone": "+41 79 000 00 00",
               "linkedin": "https://www.linkedin.com/in/x/", "salaire": "150000",
               "motivation": "Structurer un domaine neuf et faire adopter des solutions : c'est mon travail depuis des années."}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(FORMULAIRE)
        r = navigateur._remplir_page(page, valeurs, {"cv": cv, "lettre": lettre}, set())
        lu = page.evaluate("""() => { const f = document.getElementById('f'); return {
            prenom: f.first_name.value, nom: f.last_name.value, mail: f.mail.value, tel: f.phone.value,
            linkedin: f['urls[LinkedIn]'].value, pays: f.country.value, salaire: f.salary.value, why: f.why.value,
            photo: f.photo.files.length, cv: f.resume.files.length, lettre: f.cover_letter.files.length,
            consent: f.consent.checked, envoye: !!window.envoye }; }""")
        navigateur._bandeau(page, "Pré-remplissage", ["Rempli : test"])
        bandeau = page.locator("#jobsearch-bandeau").inner_text()
        b.close()
    assert lu["prenom"] == "Camille" and lu["nom"] == "Exemple" and lu["mail"] == "j@example.ch"
    assert lu["tel"].startswith("+41") and lu["linkedin"].endswith("/x/") and lu["pays"] == "Switzerland"
    assert lu["salaire"] == "150000" and len(lu["why"]) == 80          # coupé à la longueur permise
    assert lu["cv"] == 1 and lu["lettre"] == 1 and lu["photo"] == 0       # la photo n'est pas le CV
    assert lu["consent"] is False and lu["envoye"] is False
    assert any("gender" in x for x in r["restants"])
    assert "rien n'est envoyé sans toi" in bandeau


JOBUP = """<!doctype html><html><body><form>
  <section><h2>Ajouter votre CV *</h2><p>Veuillez télécharger votre CV le plus récent.</p>
    <div class="carte">Camille-Exemple-CV-Yverdon.pdf · Depuis le profil</div></section>
  <section><h2>Écrivez votre lettre de motivation ici*</h2><textarea name="coverLetterText"></textarea></section>
  <section><h2>Autres documents <small>(optionnel)</small></h2><p>Certificats, portfolios…</p>
    <input type="file" name="file" id="upload-1"></section>
</form></body></html>"""


def test_jobup_cv_jamais_dans_autres_documents(tmp_path):
    from playwright.sync_api import sync_playwright
    cv = tmp_path / "Camille-Exemple-CV-ACG.pdf"
    cv.write_bytes(b"%PDF-1.4")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(JOBUP)
        r = navigateur._remplir_page(page, {"lettre": "Lausanne, le 26 octobre 2026\n\nObjet : candidature"},
                                     {"cv": cv, "lettre": None}, set())
        autres = page.evaluate("() => document.getElementById('upload-1').files.length")
        lettre = page.evaluate("() => document.forms[0].coverLetterText.value")
        # Camille clique « Remplacer » : un champ apparaît dans la section CV, le CV s'y joint.
        page.evaluate("""() => { const i = document.createElement('input'); i.type = 'file'; i.id = 'cv-input';
                                 document.querySelector('section').appendChild(i); }""")
        navigateur._remplir_page(page, {}, {"cv": cv}, set())
        dans_cv = page.evaluate("() => document.getElementById('cv-input').files.length")
        b.close()
    assert autres == 0 and dans_cv == 1
    assert lettre.startswith("Lausanne, le 26 octobre 2026") and "**" not in lettre


def test_word_d_abord_pdf_si_le_champ_l_exige():
    formats = {"docx": "cv.docx", "pdf": "cv.pdf"}
    assert navigateur.choisir_format(formats, "") == "cv.docx"
    assert navigateur.choisir_format(formats, ".pdf,.doc,.docx") == "cv.docx"
    assert navigateur.choisir_format(formats, "application/pdf") == "cv.pdf"
    assert navigateur.choisir_format({"pdf": "cv.pdf"}, "") == "cv.pdf"


def test_rien_sur_une_page_de_connexion():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content('<form><input type="email" name="email"><input type="password" name="pw"></form>')
        r = navigateur._remplir_page(page, {"email": "j@example.ch"}, {}, set())
        assert page.evaluate("() => document.forms[0].email.value") == "" and r["remplis"] == [] and r["mdp"]
        b.close()


@pytest.mark.parametrize("url,connexion", [
    ("https://www.linkedin.com/checkpoint/challenge/AQEpiBrH3z53", True),
    ("https://www.linkedin.com/checkpoint/challengesV2/AQHZBKW", True),
    ("https://www.linkedin.com/checkpoint/challenge/verify", True),
    ("https://www.linkedin.com/flagship-web/login?challengeId=x", True),
    ("https://www.jobup.ch/fr/emplois/detail/17a6492a/", False),
    ("https://jobs.example.ch/apply/123", False),
])
def test_verification_de_securite_traitee_comme_connexion(url, connexion):
    assert bool(navigateur._CONNEXION.search(url)) is connexion


def test_pdf_par_chrome(tmp_path):
    from jobsearch import apercu, dossier
    docx = tmp_path / "Camille-Exemple-CV-Test.docx"
    dossier.rendre_docx("# Camille Exemple\n## Product Manager\n- Une puce\n", docx, "cv")
    pdf = apercu.en_pdf_chrome(docx)
    assert pdf and pdf.read_bytes().startswith(b"%PDF")


LISTE_JOBROOM = """<!doctype html><html><body><table>
<tr><td>18.06.2026</td><td>Association des Communes Genevoises (ACG)</td><td>Responsable du Pôle Projets et Innovation</td></tr>
<tr><td>20.10.2026</td><td>Banque Pictet &amp; Cie SA</td><td>Transformation Manager</td></tr>
</table></body></html>"""


def test_doublon_meme_employeur_autre_poste():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(LISTE_JOBROOM)
        autre_poste = navigateur.doublon(page, ["genevoises"],
                                         navigateur.jetons_poste("Directeur-trice adjoint-e, « Projets et de l'innovation »"))
        meme_poste = navigateur.doublon(page, ["pictet"], navigateur.jetons_poste("Transformation Manager"))
        b.close()
    assert autre_poste is None
    assert meme_poste and "Transformation Manager" in meme_poste


FORM_JOBROOM = """<!doctype html><html><body>
<input placeholder="Professions, catégories" id="recherche-entete">
<main><h1>Preuves de recherches personnelles d'emploi</h1>
<form onsubmit="return false">
  <h3>Quand avez-vous postulé ?</h3><input placeholder="Date" formcontrolname="date" required>
  <h3>Comment avez-vous postulé ?</h3>
  <label><input type="checkbox" id="elec"> Par voie électronique</label>
  <label><input type="checkbox" id="lettre"> Par lettre</label>
  <h3>Auprès de quelle(s) entreprise(s) avez-vous postulé ?</h3>
  <input placeholder="Entreprise" formcontrolname="name" required><input placeholder="Rue" formcontrolname="street">
  <input placeholder="N°" formcontrolname="houseNumber"><input placeholder="NPA" formcontrolname="postalCode">
  <h3>Pour quel poste ?</h3><input placeholder="Désignation du poste" formcontrolname="occupation" required>
  <input placeholder="Lien de l'annonce" formcontrolname="url">
  <h3>Le poste était-il à temps partiel ?</h3>
  <label><input type="radio" name="partiel" id="partiel-oui"> Oui</label><label><input type="radio" name="partiel" id="partiel-non"> Non</label>
  <h3>Avez-vous été assigné(e) par l'ORP ?</h3>
  <label><input type="radio" name="orp" id="orp-oui"> Oui</label><label><input type="radio" name="orp" id="orp-non"> Non</label>
  <h3>Taux d'occupation</h3><label><input type="checkbox" id="plein"> Plein temps</label>
  <h3>Résultat</h3><label><input type="radio" name="res" id="suspens"> En suspens</label>
  <button type="button" onclick="window.sauve = true">Sauvegarder</button>
</form></main></body></html>"""


def test_saisie_jobroom_dans_le_formulaire_seulement():
    from playwright.sync_api import sync_playwright
    v = {"date": "26.10.2026", "entreprise": "Association des Communes Genevoises (ACG)", "rue": "Esplanade de Pont-Rouge",
         "numero": "3", "poste": "Directeur-trice adjoint-e", "lien": "https://www.jobup.ch/x"}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(FORM_JOBROOM)
        faits, restants = navigateur._remplir_jobroom(page, v)
        lu = page.evaluate("""() => ({entete: document.getElementById('recherche-entete').value,
            date: document.querySelector('[formcontrolname=date]').value, ent: document.querySelector('[formcontrolname=name]').value,
            no: document.querySelector('[formcontrolname=houseNumber]').value, poste: document.querySelector('[formcontrolname=occupation]').value,
            elec: document.getElementById('elec').checked, lettre: document.getElementById('lettre').checked,
            orp_non: document.getElementById('orp-non').checked, orp_oui: document.getElementById('orp-oui').checked,
            plein: document.getElementById('plein').checked, suspens: document.getElementById('suspens').checked,
            partiel_non: document.getElementById('partiel-non').checked})""")
        erreurs = navigateur.erreurs_formulaire(page)
        navigateur._sauvegarder(page)
        sauve = page.evaluate("() => !!window.sauve")
        b.close()
    assert lu["entete"] == ""                                        # jamais la recherche du site
    assert lu["date"] == "26.10.2026" and lu["ent"].startswith("Association") and lu["no"] == "3"
    assert lu["poste"] == "Directeur-trice adjoint-e"
    assert lu["elec"] and not lu["lettre"] and lu["orp_non"] and not lu["orp_oui"] and lu["plein"] and lu["suspens"]
    assert not lu["partiel_non"]                                     # le « Non » de l'ORP, pas un autre
    assert erreurs == [] and sauve


LISTE_PUIS_FORMULAIRE = """<!doctype html><html><body><main id="app">
<h1>Preuves de recherches personnelles d'emploi</h1>
<input placeholder="Filtrer">
<ul><li>Losinger Marazzi · Coach IA
  <label><input type="radio" name="r1" id="existant-refus" checked> Réponse négative</label>
  <label><input type="radio" name="r1" id="existant-suspens"> En suspens</label></li></ul>
</main>
<script>setTimeout(() => { document.getElementById('app').innerHTML = `__FORM__`; }, 1500);</script>
</body></html>"""


def test_ne_touche_jamais_la_liste_affichee_avant_le_formulaire():
    from playwright.sync_api import sync_playwright
    form = FORM_JOBROOM.split("<main>")[1].split("</main>")[0].replace("`", "")
    html = LISTE_PUIS_FORMULAIRE.replace("__FORM__", form)
    v = {"date": "26.10.2026", "entreprise": "ACG", "poste": "Directeur"}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page()
        page.set_content(html)
        assert page.evaluate(navigateur._JS_CHAMPS_FORM)["trouve"] is False      # la liste n'est pas le formulaire
        faits, restants = navigateur._remplir_jobroom(page, v)
        assert faits == [] and page.evaluate("() => document.getElementById('existant-refus').checked")
        page.wait_for_timeout(1800)
        assert page.evaluate(navigateur._JS_CHAMPS_FORM)["trouve"] is True
        faits, _ = navigateur._remplir_jobroom(page, v)
        b.close()
    assert {"date", "entreprise", "poste", "canal électronique", "assignation ORP non"} <= set(faits)
