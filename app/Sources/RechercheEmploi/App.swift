// Recherche d'emploi : fenêtre du tableau de bord, icône de la barre des menus, connexion à Claude.

import AppKit
import SwiftUI

final class Delegue: NSObject, NSApplicationDelegate {
    var etat: Etat?
    // La fenêtre se ferme, l'application reste dans la barre des menus.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { etat?.quitter() }
    }
}

@main
struct RechercheEmploiApp: App {
    @NSApplicationDelegateAdaptor(Delegue.self) var delegue
    @StateObject private var etat = Etat()

    var body: some Scene {
        WindowGroup("Recherche d'emploi", id: "tableau") {
            Tableau()
                .environmentObject(etat)
                .frame(minWidth: 900, minHeight: 640)
                .onAppear {
                    delegue.etat = etat
                    etat.demarrer()
                }
        }
        .defaultSize(width: 1180, height: 820)
        .commands {
            CommandGroup(after: .appInfo) { BoutonConnexion().environmentObject(etat) }
        }

        MenuBarExtra {
            Menu_().environmentObject(etat)
        } label: {
            Image(systemName: etat.niveau == "ok" ? "briefcase" : "briefcase.fill")
        }
    }

}

/// « Se connecter à Claude… » : la modale de connexion du tableau de bord, dans la fenêtre principale.
struct BoutonConnexion: View {
    @EnvironmentObject var etat: Etat
    @Environment(\.openWindow) private var ouvrir
    var body: some View {
        Button("Se connecter à Claude…") {
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
        Text(etat.aTrancher == 0 ? "Rien à trancher" : "\(etat.aTrancher) annonce(s) à trancher")
        Text(etat.claudeConnecte ? "Claude : connecté" : "Claude : pas connecté")
        if etat.mode == "distant" {
            Text("Machine virtuelle : \(libelleVM)")
        }
        if let o = etat.occupe { Text("En cours : \(o)…") }
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
        Button(etat.claudeConnecte ? "Se reconnecter à Claude…" : "Se connecter à Claude…") {
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
        Button("Quitter Recherche d'emploi") { NSApp.terminate(nil) }
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
            if etat.tableauPret {
                PageWeb(url: etat.base, etat: etat)
            } else if let e = etat.erreurTableau {
                VStack(spacing: 12) {
                    Image(systemName: "exclamationmark.triangle").font(.largeTitle)
                    Text(e).multilineTextAlignment(.center)
                    Button("Réessayer") { Task { await etat.assurerTableau() } }
                }.padding(40)
            } else {
                ProgressView("Démarrage du tableau de bord…").padding(40)
            }
        }
    }
}
