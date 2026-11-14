"""Le contrôle de longueur du CV lit le PDF avec pypdf (aucun outil extérieur)."""

from jobsearch import dossier


def pdf_de_texte(pages):
    """Un PDF minimal : une liste de pages, chacune une liste de lignes (Helvetica)."""
    objets = [b"<< /Type /Catalog /Pages 2 0 R >>", None,
              b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    refs = []
    for lignes in pages:
        flux = "BT /F1 10 Tf 12 TL 40 780 Td " + " ".join("(%s) Tj T*" % l for l in lignes) + " ET"
        objets.append(("<< /Length %d >>\nstream\n%s\nendstream" % (len(flux), flux)).encode())
        objets.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                       "/Resources << /Font << /F1 3 0 R >> >> >>" % len(objets)).encode())
        refs.append("%d 0 R" % (len(objets)))
    objets[1] = ("<< /Type /Pages /Count %d /Kids [%s] >>" % (len(pages), " ".join(refs))).encode()
    sortie, positions = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objets, 1):
        positions.append(len(sortie))
        sortie += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(sortie)
    sortie += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objets) + 1)
    for p in positions:
        sortie += b"%010d 00000 n \n" % p
    sortie += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objets) + 1, xref)
    return bytes(sortie)


def test_lignes_par_page_avec_pypdf(tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(pdf_de_texte([["Ligne %d" % i for i in range(5)], ["Une", "Deux"]]))
    assert dossier.lignes_par_page(pdf) == [5, 2]


def test_lignes_par_page_sans_pdf_ou_pdf_illisible(tmp_path):
    assert dossier.lignes_par_page(tmp_path / "absent.pdf") is None
    assert dossier.lignes_par_page(None) is None
    faux = tmp_path / "faux.pdf"
    faux.write_bytes(b"pas un pdf")
    assert dossier.lignes_par_page(faux) is None
