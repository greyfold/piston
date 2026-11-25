// swift-tools-version:5.10
// L'application Mac « Piston » : une fenêtre sur le tableau de bord, une icône
// dans la barre des menus, le pilotage de la machine virtuelle et la connexion à Claude.
// Construite par scripts/construire-app.sh (paquet .app signé localement).
import PackageDescription

let package = Package(
    name: "RechercheEmploi",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "RechercheEmploi", path: "Sources/RechercheEmploi")
    ]
)
