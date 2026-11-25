// Piston : fenêtre du tableau de bord, icône de la barre des menus, connexion à Claude.

import AppKit
import SwiftUI

final class Delegue: NSObject, NSApplicationDelegate {
    /// L'état de l'application, unique : créé et démarré au lancement, même sans fenêtre
    /// (ouverture à la connexion) ; tableau de bord et échéances n'attendent pas la fenêtre.
    @MainActor static let etat = Etat()
    func applicationDidFinishLaunching(_ notification: Notification) {
        MainActor.assumeIsolated { Delegue.etat.demarrer() }
    }
    // La fenêtre se ferme, l'application reste dans la barre des menus.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    /// Quitter éteint d'abord la machine virtuelle (écran d'arrêt) ; la fermeture suit (Etat.swift).
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        MainActor.assumeIsolated { Delegue.etat.quitterEnEteignantLaVM() ? .terminateLater : .terminateNow }
    }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { Delegue.etat.quitter() }
    }
}

@main
struct RechercheEmploiApp: App {
    @NSApplicationDelegateAdaptor(Delegue.self) var delegue
    @StateObject private var etat = Delegue.etat

    var body: some Scene {
        // Une seule fenêtre (donc une seule vue web et une seule dictée), rouverte par le menu.
        Window("Piston", id: "tableau") {
            Tableau()
                .environmentObject(etat)
                .frame(minWidth: 900, minHeight: 640)
        }
        .defaultSize(width: 1180, height: 820)
        .commands {
            CommandGroup(after: .appInfo) { BoutonConnexion().environmentObject(etat) }
            SidebarCommands()
            CommandesAller(pilote: Pilote.partage)
        }

        MenuBarExtra {
            Menu_().environmentObject(etat)
        } label: {
            Image(systemName: etat.niveau == "ok" ? "briefcase" : "briefcase.fill")
        }
    }

}

/// « Se connecter à <IA>… » : la modale de connexion du tableau de bord, dans la fenêtre principale.
struct BoutonConnexion: View {
    @EnvironmentObject var etat: Etat
    @Environment(\.openWindow) private var ouvrir
    var body: some View {
        Button("Se connecter à \(etat.iaNom)…") {
            etat.connexionDemandee = true
            ouvrir(id: "tableau")
            NSApp.activate(ignoringOtherApps: true)
        }
    }
}

/// Le menu de la barre des menus : l'état d'abord, puis les actions.
struct Menu_: View {
    @EnvironmentObject var etat: Etat
    @Environment(\.openWindow) private var ouvrir

    var body: some View {
        Text(etat.passage)
        if let n = etat.aTrancher {
            Text(n == 0 ? "Rien à trancher" : "\(n) annonce(s) à trancher")
        }
        Text(etat.claudeConnecte ? "\(etat.iaNom) : connecté" : "\(etat.iaNom) : pas connecté")
        if etat.mode == "distant" {
            Text("Machine virtuelle : \(libelleVM)")
        }
        if let o = etat.occupe { Text("En cours : \(etat.etapeVM ?? o)…") }
        if let m = etat.dernierMessage { Text(m) }
        Divider()
        Button("Ouvrir le tableau de bord") {
            ouvrir(id: "tableau")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("Navigateur de la machine virtuelle") {
            etat.pageDemandee = "navigateur"
            ouvrir(id: "tableau")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("Lancer un passage") { etat.lancerPassage() }
            .disabled(etat.occupe != nil)
        Button(etat.claudeConnecte ? "Se reconnecter à \(etat.iaNom)…" : "Se connecter à \(etat.iaNom)…") {
            etat.connexionDemandee = true
            ouvrir(id: "tableau")
            NSApp.activate(ignoringOtherApps: true)
        }
        if etat.mode == "distant" {
            Divider()
            if etat.vmStatut == "Running" {
                Button("Arrêter la machine virtuelle") { etat.arreterVM() }.disabled(etat.occupe != nil)
            } else {
                Button("Démarrer la machine virtuelle") { etat.demarrerVM() }.disabled(etat.occupe != nil)
            }
        }
        Divider()
        Toggle("Ouvrir à la connexion", isOn: Binding(get: { etat.ouvertureAuto },
                                                       set: { _ in etat.basculerOuvertureAuto() }))
        Divider()
        Button("Quitter Piston") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }

    private var libelleVM: String {
        switch etat.vmStatut {
        case "Running": return "démarrée"
        case "Stopped": return "arrêtée"
        case nil, "absente": return "pas encore créée"
        default: return etat.vmStatut ?? "?"
        }
    }
}

/// La fenêtre principale : le tableau de bord, ou ce qui l'empêche de s'afficher.
struct Tableau: View {
    @EnvironmentObject var etat: Etat
    @Environment(\.openWindow) private var ouvrir

    var body: some View {
        Group {
            if etat.arretEnCours {
                EcranArret()
            } else if etat.tableauPret {
                // Le démarrage de la machine virtuelle se montre dans la page (/demarrage, Etat.swift) ;
                // autour, le cadre natif : barre latérale, barre d'outils (Cadre.swift).
                CadreNatif()
            } else if let e = etat.erreurTableau {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle").font(.largeTitle)
                    Text(e).multilineTextAlignment(.center)
                    Button("Réessayer") { Task { await etat.reessayer() } }
                }.padding(40)
            } else {
                EcranAttente(titre: "Ouverture de l'application", detail: "Démarrage du tableau de bord")
            }
        }
    }
}

/// Écran d'attente natif (ouverture du tableau de bord, fermeture) : un indicateur, ce qui se passe,
/// l'étape en cours, et au besoin une action secondaire. Sobre, aux couleurs du système (clair et sombre).
struct EcranAttente: View {
    let titre: String
    let detail: String
    var note: String? = nil
    var action: (String, () -> Void)? = nil

    var body: some View {
        VStack(spacing: 14) {
            ProgressView().controlSize(.large)
            Text(titre).font(.title3.weight(.semibold))
            Text(detail + "…").foregroundStyle(.secondary).multilineTextAlignment(.center)
                .accessibilityAddTraits(.updatesFrequently)
            if let note {
                Text(note).font(.callout).foregroundStyle(.tertiary).multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
            }
            if let action {
                Button(action.0, action: action.1).buttonStyle(.link).padding(.top, 4)
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// La fermeture : la machine virtuelle s'éteint avant que l'application parte.
struct EcranArret: View {
    @EnvironmentObject var etat: Etat
    var body: some View {
        EcranAttente(titre: "Fermeture de l'application", detail: "Arrêt de la machine virtuelle",
                     note: etat.passageInterrompu
                        ? "Le passage en cours est interrompu : il reprendra au prochain lancement."
                        : "Cela prend quelques secondes, deux minutes au plus.")
    }
}
