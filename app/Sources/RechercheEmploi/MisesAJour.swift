// Les mises à jour de Piston, par Sparkle : une vérification par jour auprès du flux publié avec
// l'application (SUFeedURL), une fenêtre native qui montre les nouveautés et propose d'installer.
// L'installation quitte l'application par le chemin habituel (la VM s'éteint d'abord), la remplace
// et la relance ; les données, hors du paquet, ne bougent pas. Une mise à jour n'est acceptée que
// signée par la clé de publication (SUPublicEDKey) et, pour une app Developer ID, par la même équipe.

import Sparkle
import SwiftUI

@MainActor
final class MisesAJour: ObservableObject {
    static let partage = MisesAJour()

    /// Nil quand le paquet n'a ni flux ni clé publique (construction sans `--publier` ni clé) :
    /// les commandes de mise à jour ne s'affichent pas.
    private let controleur: SPUStandardUpdaterController?
    @Published private(set) var peutVerifier = false
    @Published private(set) var automatique = false

    var disponible: Bool { controleur != nil }

    private init() {
        let info = Bundle.main.infoDictionary ?? [:]
        guard let flux = info["SUFeedURL"] as? String, !flux.isEmpty,
              let cle = info["SUPublicEDKey"] as? String, !cle.isEmpty else {
            controleur = nil
            return
        }
        let c = SPUStandardUpdaterController(startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
        controleur = c
        automatique = c.updater.automaticallyChecksForUpdates
        c.updater.publisher(for: \.canCheckForUpdates).assign(to: &$peutVerifier)
    }

    /// « Rechercher les mises à jour… » : la fenêtre de Sparkle dit le résultat, même « à jour ».
    func verifier() { controleur?.checkForUpdates(nil) }

    /// La vérification quotidienne ; coupée, seule la commande du menu interroge le flux.
    func basculerAutomatique() {
        guard let u = controleur?.updater else { return }
        u.automaticallyChecksForUpdates.toggle()
        automatique = u.automaticallyChecksForUpdates
    }
}

/// « Rechercher les mises à jour… », dans le menu Piston et dans celui de la barre des menus.
struct BoutonMisesAJour: View {
    @ObservedObject var maj = MisesAJour.partage
    var body: some View {
        if maj.disponible {
            Button("Rechercher les mises à jour…") { maj.verifier() }
                .disabled(!maj.peutVerifier)
        }
    }
}

/// La vérification quotidienne, à cocher dans le menu de la barre des menus.
struct BasculeMisesAJour: View {
    @ObservedObject var maj = MisesAJour.partage
    var body: some View {
        if maj.disponible {
            Toggle("Vérifier les mises à jour chaque jour",
                   isOn: Binding(get: { maj.automatique }, set: { _ in maj.basculerAutomatique() }))
        }
    }
}
