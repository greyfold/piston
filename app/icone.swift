// Dessine l'icône de l'application (porte-documents blanc sur fond bleu-vert) dans un .iconset.
// Usage : swift app/icone.swift <dossier.iconset>

import AppKit

let dossier = URL(fileURLWithPath: CommandLine.arguments[1])
try? FileManager.default.createDirectory(at: dossier, withIntermediateDirectories: true)

func dessiner(_ cote: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: cote, pixelsHigh: cote, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let c = CGFloat(cote)
    let marge = c * 0.1
    let cadre = NSRect(x: marge, y: marge, width: c - 2 * marge, height: c - 2 * marge)
    let forme = NSBezierPath(roundedRect: cadre, xRadius: c * 0.18, yRadius: c * 0.18)
    NSGradient(starting: NSColor(red: 0.12, green: 0.37, blue: 0.66, alpha: 1),
               ending: NSColor(red: 0.05, green: 0.48, blue: 0.42, alpha: 1))!.draw(in: forme, angle: -90)
    let config = NSImage.SymbolConfiguration(pointSize: c * 0.42, weight: .semibold)
        .applying(.init(paletteColors: [.white]))
    if let symbole = NSImage(systemSymbolName: "briefcase.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(config) {
        let s = symbole.size
        symbole.draw(in: NSRect(x: (c - s.width) / 2, y: (c - s.height) / 2 - c * 0.01, width: s.width, height: s.height))
    }
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try! dessiner(base).write(to: dossier.appendingPathComponent("icon_\(base)x\(base).png"))
    try! dessiner(base * 2).write(to: dossier.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
