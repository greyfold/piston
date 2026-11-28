// L'état que l'application montre et les ordres qu'elle donne.
//
// Tout passe par la plateforme Python embarquée (Contents/Resources/python et racine). L'enveloppe ne
// lance qu'un processus, le superviseur (`python3 -m jobsearch superviseur`, src/jobsearch/superviseur.py),
// commun à tous les systèmes : il tient le tableau de bord sur son port (un seul, relancé s'il tombe),
// relit l'état toutes les 30 s, lance les échéances toutes les cinq minutes (passage du matin, alerte,
// surveillance), démarre la machine virtuelle au lancement et l'éteint à la fermeture. Il parle par lignes
// JSON : ses événements sur sa sortie, nos ordres sur son entrée. L'application ne touche jamais la base.
//
// Ce qui reste ici est propre au Mac : la fenêtre, la barre des menus, « Ouvrir à la connexion »
// (SMAppService), l'écran d'arrêt et la réponse à macOS quand la fermeture est finie.

import AppKit
import Foundation
import ServiceManagement
import SwiftUI

/// Un événement du superviseur (une ligne JSON de sa sortie). Seuls les champs lus ici sont décodés.
struct Evenement: Decodable {
    struct Passage: Decodable { let niveau: String; let texte: String }
    struct Claude: Decodable { let connecte: Bool; let message: String; let id: String?; let nom: String? }
    struct VM: Decodable { let existe: Bool?; let statut: String? }
    let evenement: String
    // tableau
    let pret: Bool?
    let erreur: String?
    // etat (le contenu de /api/etat)
    let passage: Passage?
    let a_trancher: Int?     // nul : tri fermé (parcours ou critères pas encore renseignés)
    let claude: Claude?
    let mode: String?
    let vm: VM?
    // occupe, etape, message, page, arret
    let titre: String?
    let texte: String?
    let page: String?
    let passage_interrompu: Bool?
}

@MainActor
final class Etat: ObservableObject {
    @Published var tableauPret = false
    @Published var erreurTableau: String?
    @Published var passage = "Lecture de l'état…"
    @Published var niveau = "ok"
    @Published var aTrancher: Int?
    @Published var claudeConnecte = false
    @Published var claudeMessage = ""
    @Published var iaId = "claude"
    @Published var iaNom = "Claude"
    @Published var mode = "local"
    @Published var vmStatut: String?
    @Published var occupe: String?
    @Published var dernierMessage: String?

    /// « Se connecter à Claude… » du menu : la page ouvre sa modale de connexion dès qu'elle est prête.
    @Published var connexionDemandee = false
    /// Une page du tableau de bord à montrer dans la fenêtre (« navigateur » : l'écran de la VM).
    @Published var pageDemandee: String?
    /// Au lancement, la machine virtuelle démarre pendant que l'accueil s'affiche : une fois qu'elle
    /// répond, l'accueil se recharge pour dire si l'IA est connectée (bandeau de l'accueil).
    @Published var rechargerAccueil = false
    @Published var ouvertureAuto = SMAppService.mainApp.status == .enabled
    /// L'étape en cours, annoncée par le démarrage de la VM ; montrée dans le menu.
    @Published var etapeVM: String?
    /// La fermeture attend que la machine virtuelle soit éteinte.
    @Published var arretEnCours = false
    @Published var passageInterrompu = false

    let racine: String                    // le code embarqué : src, config, pyproject.toml
    let python: String                    // le Python embarqué
    let limactl: String                   // Lima embarqué (machine virtuelle)
    let donnees: String
    let port = 8775
    let base: URL
    private var superviseur: Process?
    private var ordres: FileHandle?
    private var fenetreArret: NSWindow?
    /// Le jeton du tableau de bord (fichier `jeton` du dossier de données, écrit par le serveur avant
    /// d'écouter) : exigé pour toute requête, posé en cookie dans la vue web (PageWeb.swift).
    private(set) var jeton: String?
    private var quitte = false
    private var fermetureRepondue = false

    /// Les lignes de la sortie du superviseur, reconstituées au fil de l'eau (un morceau peut couper une ligne).
    final class Lignes: @unchecked Sendable {
        private var reste = Data()
        private let verrou = NSLock()
        func ajouter(_ d: Data) -> [Data] {
            verrou.lock(); defer { verrou.unlock() }
            reste.append(d)
            var lignes: [Data] = []
            while let i = reste.firstIndex(of: 0x0A) {
                lignes.append(reste[reste.startIndex..<i])
                reste.removeSubrange(reste.startIndex...i)
            }
            if reste.count > 1_048_576 { reste.removeAll() }      // une ligne sans fin : abandonnée
            return lignes
        }
    }

    init() {
        let ressources = Bundle.main.resourcePath ?? ""
        racine = ressources + "/racine"
        python = ressources + "/python/bin/python3"
        limactl = ressources + "/lima/bin/limactl"
        let maison = FileManager.default.homeDirectoryForCurrentUser.path
        donnees = maison + "/Library/Application Support/Recherche emploi"
        base = URL(string: "http://localhost:\(port)/")!
        // Coordonnées, recherche, dossiers : le dossier n'est ouvert qu'au compte de la personne.
        try? FileManager.default.createDirectory(atPath: donnees, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: donnees)
    }

    private var environnement: [String: String] {
        var env = ProcessInfo.processInfo.environment
        // Ce que l'application embarque, puis le système : rien de Homebrew ni de ~/.local/bin
        // (le Mac ne lance jamais `claude` ; Claude Code tourne dans la machine virtuelle).
        let ressources = Bundle.main.resourcePath ?? ""
        env["PATH"] = [ressources + "/python/bin", ressources + "/lima/bin", "/usr/bin", "/bin", "/usr/sbin",
                       "/sbin"].joined(separator: ":")
        env["JOBSEARCH_HOME"] = racine
        env["JOBSEARCH_DATA"] = donnees
        env["JOBSEARCH_PORT"] = String(port)
        env["JOBSEARCH_LIMACTL"] = limactl                     // Claude Code et Chrome : dans la VM
        env["PYTHONNOUSERSITE"] = "1"                          // rien du Python de l'utilisateur
        env["PYTHONDONTWRITEBYTECODE"] = "1"                   // l'application signée reste intacte
        env["PYTHONUNBUFFERED"] = "1"                          // les événements arrivent sans attendre
        for cle in ["JOBSEARCH_DB", "JOBSEARCH_PROFIL", "JOBSEARCH_DEFAUTS", "PYTHONPATH", "PYTHONHOME",
                    "VIRTUAL_ENV", "LIMA_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY"] {
            env.removeValue(forKey: cle)
        }
        return env
    }

    // MARK: - Le superviseur

    func demarrer() {
        guard superviseur == nil, !quitte else { return }
        guard FileManager.default.isExecutableFile(atPath: python) else {
            erreurTableau = "Python embarqué introuvable : \(python). L'application est incomplète : reconstruis-la."
            return
        }
        erreurTableau = nil
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "jobsearch", "superviseur", "--hote", "127.0.0.1", "--port", String(port)]
        p.currentDirectoryURL = URL(fileURLWithPath: donnees)
        p.environment = environnement
        let sortie = Pipe(), entree = Pipe()
        p.standardOutput = sortie
        p.standardInput = entree
        p.standardError = FileHandle.nullDevice
        let lignes = Lignes()
        sortie.fileHandleForReading.readabilityHandler = { h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            for ligne in lignes.ajouter(d) {
                guard let e = try? JSONDecoder().decode(Evenement.self, from: ligne) else { continue }
                Task { @MainActor in self.recevoir(e) }
            }
        }
        p.terminationHandler = { proc in
            Task { @MainActor in self.superviseurArrete(proc) }
        }
        do { try p.run() } catch {
            erreurTableau = "Le superviseur ne démarre pas : \(error.localizedDescription)"
            return
        }
        superviseur = p
        ordres = entree.fileHandleForWriting
    }

    /// Un ordre au superviseur, une ligne JSON.
    private func ordonner(_ ordre: [String: Any]) {
        guard let ordres, let d = try? JSONSerialization.data(withJSONObject: ordre) else { return }
        do { try ordres.write(contentsOf: d + Data([0x0A])) } catch {}
    }

    private func recevoir(_ e: Evenement) {
        switch e.evenement {
        case "tableau":
            if e.pret == true {
                lireJeton()                                    // écrit par le serveur avant qu'il écoute
                erreurTableau = nil
                tableauPret = true
            } else {
                tableauPret = false
                erreurTableau = e.erreur ?? "Le tableau de bord ne répond pas."
            }
        case "etat":
            if let p = e.passage { passage = p.texte; niveau = p.niveau }
            aTrancher = e.a_trancher
            if let c = e.claude {
                claudeConnecte = c.connecte
                claudeMessage = c.message
                if let id = c.id, !id.isEmpty { iaId = id }
                if let nom = c.nom, !nom.isEmpty { iaNom = nom }
            }
            if let m = e.mode { mode = m }
            vmStatut = e.vm.map { $0.statut ?? "absente" }
        case "occupe":
            occupe = e.titre
            if e.titre == nil { etapeVM = nil }
        case "etape":
            etapeVM = e.texte
        case "page":
            pageDemandee = e.page                              // la première étape du démarrage : /demarrage
        case "recharger_accueil":
            rechargerAccueil = true
        case "message":
            if !quitte { dernierMessage = e.texte }
        case "arret":
            passageInterrompu = e.passage_interrompu ?? false
            arretEnCours = true
            montrerFenetreArret()
        case "fin":
            repondreFermeture()
        default:
            break
        }
    }

    /// Le superviseur s'est arrêté : à la fermeture, c'est la fin ; sinon une panne, que « Réessayer » relance.
    private func superviseurArrete(_ p: Process) {
        guard p === superviseur else { return }
        superviseur = nil
        ordres = nil
        if quitte { repondreFermeture(); return }
        tableauPret = false
        occupe = nil
        erreurTableau = "Le superviseur de l'application s'est arrêté (code \(p.terminationStatus)). Réessaie, "
            + "ou reconstruis l'application si cela persiste."
    }

    @discardableResult
    private func lireJeton() -> String? {
        let lu = (try? String(contentsOfFile: donnees + "/jeton", encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let lu, !lu.isEmpty { jeton = lu }
        return jeton
    }

    /// « Réessayer » de la fenêtre.
    func reessayer() async {
        if superviseur == nil { demarrer() } else { ordonner(["ordre": "reessayer"]) }
    }

    /// « Ouvrir à la connexion » : l'application démarre avec la session, pour que le passage du
    /// matin parte à l'heure sans qu'on y pense.
    func basculerOuvertureAuto() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            dernierMessage = "Ouverture à la connexion impossible : \(error.localizedDescription)"
        }
        ouvertureAuto = SMAppService.mainApp.status == .enabled
    }

    // MARK: - Commandes du menu (une à la fois : le superviseur refuse la seconde)

    func lancerPassage() { ordonner(["ordre": "passage"]) }
    func demarrerVM() { ordonner(["ordre": "vm_demarrer"]) }
    func arreterVM() { ordonner(["ordre": "vm_arreter"]) }

    // MARK: - Fin

    func quitter() {
        quitte = true
        superviseur?.terminate()              // s'il tourne encore (fermeture immédiate) : il arrête ce qu'il a lancé
    }

    /// La fermeture : le superviseur arrête ce qui tourne (le passage en cours reprendra au prochain
    /// lancement), éteint la machine virtuelle (il le dit : écran d'arrêt) puis le tableau de bord, et
    /// annonce la fin. Vrai : la fermeture attend (`.terminateLater`) et
    /// `NSApp.reply(toApplicationShouldTerminate:)` la termine. Garde : quatre minutes et demie au plus.
    func quitterEnEteignantLaVM() -> Bool {
        if quitte { return true }                          // déjà en train de fermer
        guard superviseur?.isRunning == true else { return false }
        quitte = true
        ordonner(["ordre": "quitter"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 270) { [weak self] in
            MainActor.assumeIsolated { self?.repondreFermeture() }
        }
        return true
    }

    private func repondreFermeture() {
        guard quitte, !fermetureRepondue else { return }
        fermetureRepondue = true
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    /// L'écran d'arrêt : dans la fenêtre du tableau de bord si elle est ouverte (`Tableau`), sinon
    /// dans une petite fenêtre à lui, l'application n'étant alors que dans la barre des menus.
    private func montrerFenetreArret() {
        NSApp.activate(ignoringOtherApps: true)
        if NSApp.windows.contains(where: { $0.isVisible && $0.identifier?.rawValue.contains("tableau") == true }) { return }
        let f = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 220), styleMask: [.titled],
                         backing: .buffered, defer: false)
        f.title = "Piston"
        f.isReleasedWhenClosed = false
        f.contentView = NSHostingView(rootView: EcranArret().environmentObject(self))
        f.center()
        f.makeKeyAndOrderFront(nil)
        fenetreArret = f
    }
}
