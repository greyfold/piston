// Le tableau de bord dans une vue web : liens externes dans le navigateur, fichiers du dossier
// (Word, PDF) enregistrés dans Téléchargements, confirmations du tableau de bord en boîtes de dialogue,
// dictée des entretiens par le micro et la reconnaissance vocale de l'ordinateur.

import AppKit
import SwiftUI
import WebKit

struct PageWeb: NSViewRepresentable {
    let url: URL
    @ObservedObject var etat: Etat

    func makeCoordinator() -> Coordinateur { Coordinateur(base: url, etat: etat) }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.applicationNameForUserAgent = "RechercheEmploi"
        // Dictée des entretiens, sur l'ordinateur (Dictee.swift).
        config.userContentController.add(context.coordinator.dictee, name: "dictee")
        let vue = WKWebView(frame: .zero, configuration: config)
        context.coordinator.dictee.vue = vue
        vue.navigationDelegate = context.coordinator
        vue.uiDelegate = context.coordinator
        vue.allowsBackForwardNavigationGestures = true
        vue.load(URLRequest(url: url))
        return vue
    }

    func updateNSView(_ vue: WKWebView, context: Context) {
        // Hors de la mise à jour de la vue : changer un @Published ici est interdit.
        if etat.connexionDemandee, !vue.isLoading { DispatchQueue.main.async { context.coordinator.ouvrirConnexion(vue) } }
        if let page = etat.pageDemandee {
            DispatchQueue.main.async {
                etat.pageDemandee = nil
                vue.load(URLRequest(url: url.appendingPathComponent(page)))
            }
        }
    }

    final class Coordinateur: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        let base: URL
        let etat: Etat
        let dictee = Dictee()
        init(base: URL, etat: Etat) { self.base = base; self.etat = etat }

        /// La modale de connexion à l'IA du tableau de bord (_connexion_ia.html).
        @MainActor func ouvrirConnexion(_ vue: WKWebView) {
            etat.connexionDemandee = false
            vue.evaluateJavaScript("window.ouvrirConnexionIA && window.ouvrirConnexionIA('claude', 'Claude')")
        }

        func webView(_ vue: WKWebView, didFinish navigation: WKNavigation!) {
            MainActor.assumeIsolated { if etat.connexionDemandee { ouvrirConnexion(vue) } }
        }

        private func interne(_ url: URL?) -> Bool {
            guard let url else { return true }
            return url.host == base.host && url.port == base.port
        }

        // Liens vers les annonces, les employeurs… : dans le navigateur de l'utilisateur.
        func webView(_ vue: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
            // Un cadre de la page (l'écran de la machine virtuelle, servi sur 127.0.0.1) reste dans la page.
            let cadre = action.targetFrame.map { !$0.isMainFrame } ?? false
            if let url = action.request.url, !cadre, !interne(url), ["http", "https", "mailto"].contains(url.scheme ?? "") {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }
            decisionHandler(.allow)
        }

        // target="_blank" : même règle.
        func webView(_ vue: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url {
                if interne(url) { vue.load(URLRequest(url: url)) } else { NSWorkspace.shared.open(url) }
            }
            return nil
        }

        // Ce que la vue ne sait pas afficher (Word…) : téléchargé.
        func webView(_ vue: WKWebView, decidePolicyFor reponse: WKNavigationResponse,
                     decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
            let piece = (reponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
            decisionHandler(reponse.canShowMIMEType && !piece.lowercased().hasPrefix("attachment") ? .allow : .download)
        }

        func webView(_ vue: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            download.delegate = self
        }

        func webView(_ vue: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
            download.delegate = self
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                      completionHandler: @escaping @MainActor (URL?) -> Void) {
            let dossier = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
            var cible = dossier.appendingPathComponent(suggestedFilename)
            var n = 1
            while FileManager.default.fileExists(atPath: cible.path) {
                let nom = (suggestedFilename as NSString).deletingPathExtension
                let ext = (suggestedFilename as NSString).pathExtension
                cible = dossier.appendingPathComponent("\(nom) \(n)" + (ext.isEmpty ? "" : ".\(ext)"))
                n += 1
            }
            completionHandler(cible)
        }

        func downloadDidFinish(_ download: WKDownload) {
            if let url = download.progress.fileURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        }

        // <input type="file"> (import du CV, page Ton parcours) : sans ce panneau, la vue web ne fait rien au clic.
        func webView(_ vue: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor ([URL]?) -> Void) {
            let panneau = NSOpenPanel()
            panneau.canChooseFiles = true
            panneau.canChooseDirectories = parameters.allowsDirectories
            panneau.allowsMultipleSelection = parameters.allowsMultipleSelection
            panneau.prompt = "Choisir"
            guard let fenetre = vue.window else {
                completionHandler(panneau.runModal() == .OK ? panneau.urls : nil)
                return
            }
            panneau.beginSheetModal(for: fenetre) { reponse in
                MainActor.assumeIsolated { completionHandler(reponse == .OK ? panneau.urls : nil) }
            }
        }

        // confirm() et alert() du tableau de bord (« Retirer cette source ? »…).
        func webView(_ vue: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor (Bool) -> Void) {
            let a = NSAlert()
            a.messageText = message
            a.addButton(withTitle: "Confirmer")
            a.addButton(withTitle: "Annuler")
            completionHandler(a.runModal() == .alertFirstButtonReturn)
        }

        func webView(_ vue: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor () -> Void) {
            let a = NSAlert()
            a.messageText = message
            a.runModal()
            completionHandler()
        }
    }
}
