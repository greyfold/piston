// Dessine l'icône de l'application dans un .iconset : le symbole de la marque (un grand rond pêche et un
// petit rond sauge qui se chevauchent, la zone commune en produit) centré sur un carré arrondi couleur lin.
// Règles de la charte : le symbole seul, sans mot, ombre, dégradé ni contour ; il occupe 62 % de la largeur ;
// coins arrondis à 22 % du côté. Le carré garde la marge des icônes macOS (10 % de chaque côté).
// Usage : swift plateformes/macos/app/icone.swift <dossier.iconset>
// ressources/icone.png (1024 px, la source des icônes des autres systèmes) est la plus grande de ces images.

import AppKit

let dossier = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: dossier, withIntermediateDirectories: true)

func couleur(_ hex: UInt32) -> CGColor {
    CGColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: 1)
}
let lin = couleur(0xFBF8F2), peche = couleur(0xF6C8A8), sauge = couleur(0xA9C2A0)

func dessiner(_ cote: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: cote, pixelsHigh: cote, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext
    let c = CGFloat(cote)

    // Le carré lin.
    let marge = c * 0.1
    let s = c - 2 * marge
    let carre = CGRect(x: marge, y: marge, width: s, height: s)
    ctx.addPath(CGPath(roundedRect: carre, cornerWidth: s * 0.22, cornerHeight: s * 0.22, transform: nil))
    ctx.setFillColor(lin)
    ctx.fillPath()

    // Le symbole, d'après la planche de la charte (carré de 256, symbole de 160 × 124) : grand rond de 124,
    // petit rond de 103 (5/6 du grand) décalé de 57 à droite et 21 vers le bas, posés en produit.
    let u = s / 256
    let largeur = 160 * u, hauteur = 124 * u
    let gauche = marge + (s - largeur) / 2
    let haut = marge + (s - hauteur) / 2                     // depuis le haut du carré
    func rond(_ x: CGFloat, _ y: CGFloat, _ d: CGFloat) -> CGRect {
        CGRect(x: gauche + x * u, y: c - (haut + (y + d) * u), width: d * u, height: d * u)   // repère en bas à gauche
    }
    ctx.setFillColor(peche)
    ctx.fillEllipse(in: rond(0, 0, 124))
    ctx.setBlendMode(.multiply)
    ctx.setFillColor(sauge)
    ctx.fillEllipse(in: rond(57, 21, 103))

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try! dessiner(base).write(to: dossier.appendingPathComponent("icon_\(base)x\(base).png"))
    try! dessiner(base * 2).write(to: dossier.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
