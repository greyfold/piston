// Le cadre natif autour du tableau de bord : barre latérale (les espaces de travail et Réglages), barre
// d'outils (précédente, suivante, notifications) et menu « Aller » avec ses raccourcis. La page dit où elle en
// est (JSON `#etat-page` de base.html, envoyé par PageWeb.swift) ; le cadre ne lit rien d'autre et n'écrit rien :
// il ne fait que charger des adresses du tableau de bord dans la vue web.

import AppKit
import SwiftUI
import WebKit

/// Ce que la page ouverte dit au cadre (base.html, `etat_coquille` dans web/app.py).
struct EtatPage: Decodable, Equatable {
    struct Espace: Decodable, Equatable, Identifiable {
        let href: String
        let nom: String
        var id: String { href }
    }
    /// « normal » : barre latérale ; « concentre » (tri, entretien) : repliée ; « accueil » : aucune.
    let mode: String
    let section: String?
    let espaces: [Espace]
    let a_completer: Bool
    var notifications: Int
    let niveau: String
}

/// Le pilote de la vue web : ce que le cadre montre, et ce qu'il demande à la vue.
@MainActor
final class Pilote: ObservableObject {
    static let partage = Pilote()

    @Published private(set) var page: EtatPage?
    @Published var titre = "Piston"
    @Published var peutReculer = false
    @Published var peutAvancer = false
    @Published var colonnes: NavigationSplitViewVisibility = .detailOnly

    weak var vue: WKWebView?
    weak var etat: Etat?

    static let reglages = "/reglages"

    /// La page vient de se charger (ou de revenir du cache) : `nil` pour une page sans état (connexion, erreur).
    func recevoir(_ nouvelle: EtatPage?) {
        let avant = page?.mode
        page = nouvelle
        let mode = nouvelle?.mode ?? "accueil"
        // La barre latérale suit le mode de la page quand il change ; entre deux pages du même mode, elle reste
        // comme la personne l'a laissée.
        if mode != avant { colonnes = mode == "normal" ? .all : .detailOnly }
    }

    /// Le menu a cours : hors de l'accueil (assistant, démarrage de la machine virtuelle, connexion).
    var menuDisponible: Bool { page.map { $0.mode != "accueil" } ?? false }
    var espaces: [EtatPage.Espace] { page?.espaces ?? [] }

    /// Une adresse du tableau de bord (« /offres »). Fenêtre fermée : chargée à sa réouverture (PageWeb.swift).
    func aller(_ chemin: String) {
        guard let vue, let base = etat?.base, let url = URL(string: chemin, relativeTo: base) else {
            etat?.pageDemandee = String(chemin.drop(while: { $0 == "/" }))
            return
        }
        vue.load(URLRequest(url: url.absoluteURL))
    }

    func reculer() { vue?.goBack() }
    func avancer() { vue?.goForward() }
    func recharger() { vue?.reload() }

    /// Le volet des notifications de la page ; l'ouvrir les marque comme vues (base.html).
    func ouvrirNotifications() {
        vue?.evaluateJavaScript("window.ouvrirNotifications && window.ouvrirNotifications()")
        page?.notifications = 0
    }

    /// Le titre de l'onglet (« Offres · Piston ») sans le nom de l'application, que la fenêtre porte déjà.
    func titreDeLaPage(_ brut: String?) {
        var t = (brut ?? "").trimmingCharacters(in: .whitespaces)
        if t.hasSuffix(" · Piston") { t = String(t.dropLast(" · Piston".count)) }
        titre = t.isEmpty ? "Piston" : t
    }

    /// L'icône (SF Symbols) d'un espace, d'après son adresse.
    static func symbole(_ href: String) -> String {
        switch href {
        case "/": return "sun.max"
        case "/offres": return "magnifyingglass"
        case "/candidatures": return "paperplane"
        case reglages: return "gearshape"
        default: return "doc"
        }
    }
}

/// La fenêtre du tableau de bord : barre latérale native, la page à droite.
struct CadreNatif: View {
    @EnvironmentObject var etat: Etat
    @ObservedObject var pilote = Pilote.partage

    var body: some View {
        NavigationSplitView(columnVisibility: $pilote.colonnes) {
            BarreLaterale(pilote: pilote)
                .navigationSplitViewColumnWidth(min: 180, ideal: 210, max: 280)
                .toolbar(removing: pilote.menuDisponible ? nil : .sidebarToggle)
        } detail: {
            PageWeb(url: etat.base, etat: etat)
                .navigationTitle(pilote.titre)
                .toolbar { BarreOutils(pilote: pilote) }
        }
        .onAppear { pilote.etat = etat }
    }
}

/// Les espaces de travail, puis Réglages ; l'élément de la page ouverte est sélectionné.
struct BarreLaterale: View {
    @ObservedObject var pilote: Pilote

    private var selection: Binding<String?> {
        Binding(get: { pilote.page?.section },
                set: { if let href = $0, href != pilote.page?.section { pilote.aller(href) } })
    }

    var body: some View {
        List(selection: selection) {
            Section {
                ForEach(pilote.espaces) { e in
                    Label(e.nom, systemImage: Pilote.symbole(e.href)).tag(e.href as String?)
                }
            }
            Section {
                Label {
                    HStack {
                        Text("Réglages")
                        if pilote.page?.a_completer == true {
                            Spacer()
                            Circle().fill(Color.orange).frame(width: 7, height: 7)
                                .help("Profil à compléter")
                                .accessibilityLabel("profil à compléter")
                        }
                    }
                } icon: {
                    Image(systemName: Pilote.symbole(Pilote.reglages))
                }
                .tag(Pilote.reglages as String?)
            }
        }
        .listStyle(.sidebar)
        .disabled(!pilote.menuDisponible)
    }
}

/// Précédente et suivante à gauche, la cloche des notifications à droite.
struct BarreOutils: ToolbarContent {
    @ObservedObject var pilote: Pilote

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Button { pilote.reculer() } label: { Label("Précédente", systemImage: "chevron.left") }
                .disabled(!pilote.peutReculer)
                .help("Page précédente")
            Button { pilote.avancer() } label: { Label("Suivante", systemImage: "chevron.right") }
                .disabled(!pilote.peutAvancer)
                .help("Page suivante")
        }
        ToolbarItem(placement: .primaryAction) {
            if pilote.menuDisponible {
                let n = pilote.page?.notifications ?? 0
                Button { pilote.ouvrirNotifications() } label: {
                    Label("Notifications", systemImage: n > 0 ? "bell.badge" : "bell")
                }
                .help(n == 0 ? "Notifications" : "Notifications : \(n) nouvelle\(n > 1 ? "s" : "")")
                .accessibilityValue(n == 0 ? "" : "\(n) nouvelle\(n > 1 ? "s" : "")")
            }
        }
    }
}

/// Le menu « Aller » et Réglages (⌘,) : les mêmes adresses que la barre latérale. La fenêtre fermée se rouvre.
struct CommandesAller: Commands {
    @ObservedObject var pilote: Pilote
    @Environment(\.openWindow) private var ouvrir

    private func montrer(_ chemin: String) {
        pilote.aller(chemin)
        ouvrir(id: "tableau")
        NSApp.activate(ignoringOtherApps: true)
    }

    var body: some Commands {
        CommandGroup(replacing: .appSettings) {
            Button("Réglages…") { montrer(Pilote.reglages) }
                .keyboardShortcut(",")
                .disabled(!pilote.menuDisponible)
        }
        CommandGroup(after: .sidebar) {
            Button("Recharger la page") { pilote.recharger() }
                .keyboardShortcut("r")
        }
        CommandMenu("Aller") {
            ForEach(Array(pilote.espaces.prefix(9).enumerated()), id: \.element.id) { i, e in
                Button(e.nom) { montrer(e.href) }
                    .keyboardShortcut(KeyEquivalent(Character(String(i + 1))))
            }
            .disabled(!pilote.menuDisponible)
            Divider()
            Button("Page précédente") { pilote.reculer() }
                .keyboardShortcut("[")
                .disabled(!pilote.peutReculer)
            Button("Page suivante") { pilote.avancer() }
                .keyboardShortcut("]")
                .disabled(!pilote.peutAvancer)
            Divider()
            Button("Notifications") { pilote.ouvrirNotifications() }
                .keyboardShortcut("n", modifiers: [.command, .shift])
                .disabled(!pilote.menuDisponible)
        }
    }
}
