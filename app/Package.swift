// swift-tools-version:5.10
// L'application Mac « Piston » : une fenêtre sur le tableau de bord, une icône
// dans la barre des menus, le pilotage de la machine virtuelle et la connexion à Claude.
// Construite par scripts/construire-app.sh (paquet .app ; signé Developer ID et notarisé avec --publier).
// Sparkle : les mises à jour (flux et clé publique dans l'Info.plist, posés par le script).
import PackageDescription

let package = Package(
    name: "RechercheEmploi",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")
    ],
    targets: [
        .executableTarget(name: "RechercheEmploi",
                          dependencies: [.product(name: "Sparkle", package: "Sparkle")],
                          path: "Sources/RechercheEmploi")
    ]
)
