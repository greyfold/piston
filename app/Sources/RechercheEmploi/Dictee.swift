// Dictée des entretiens avec l'IA : le micro de l'ordinateur et la reconnaissance vocale d'Apple,
// exigée « sur l'appareil » (requiresOnDeviceRecognition) : la voix ne quitte jamais le Mac.
// Pont « dictee » de la vue web (compositeur.js) :
//   JS → Swift  {action: "demarrer", langue: "fr-FR"} | {action: "arreter"}
//   Swift → JS  window.dicteeRetour({type: "pret" | "partiel" | "final" | "fin" | "erreur", texte?, message?})

import AVFoundation
import Speech
import WebKit

final class Dictee: NSObject, WKScriptMessageHandler {
    weak var vue: WKWebView?
    private let moteurAudio = AVAudioEngine()
    private var requete: SFSpeechAudioBufferRecognitionRequest?
    private var tache: SFSpeechRecognitionTask?
    private var enCours = false

    func userContentController(_ controleur: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let corps = message.body as? [String: Any], let action = corps["action"] as? String else { return }
        switch action {
        case "demarrer": demarrer(langue: corps["langue"] as? String ?? "fr-FR")
        case "arreter": arreter()
        default: break
        }
    }

    // MARK: - Autorisations, puis écoute

    private func demarrer(langue: String) {
        guard !enCours else { return }
        SFSpeechRecognizer.requestAuthorization { statut in
            DispatchQueue.main.async {
                guard statut == .authorized else {
                    return self.erreur("la reconnaissance vocale n'est pas autorisée (Réglages Système › Confidentialité › Reconnaissance vocale)")
                }
                AVCaptureDevice.requestAccess(for: .audio) { ok in
                    DispatchQueue.main.async {
                        guard ok else {
                            return self.erreur("l'accès au micro est refusé (Réglages Système › Confidentialité › Micro)")
                        }
                        self.ecouter(langue: langue)
                    }
                }
            }
        }
    }

    private func ecouter(langue: String) {
        // Rien ne part vers les serveurs d'Apple : sans modèle local pour cette langue, pas de dictée.
        guard let reconnaisseur = Dictee.reconnaisseurLocal(langue) else {
            return erreur("la dictée sur l'ordinateur n'est pas installée pour cette langue (Réglages Système › Clavier › Dictée)")
        }
        guard reconnaisseur.isAvailable else { return erreur("la reconnaissance vocale est indisponible pour l'instant") }

        let r = SFSpeechAudioBufferRecognitionRequest()
        r.requiresOnDeviceRecognition = true
        r.shouldReportPartialResults = true
        r.addsPunctuation = true
        r.taskHint = .dictation
        requete = r

        let entree = moteurAudio.inputNode
        let format = entree.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { return erreur("aucun micro trouvé") }
        entree.removeTap(onBus: 0)
        entree.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak r] tampon, _ in r?.append(tampon) }

        tache = reconnaisseur.recognitionTask(with: r) { [weak self] resultat, erreur in
            DispatchQueue.main.async {
                guard let self else { return }
                if let resultat {
                    let texte = resultat.bestTranscription.formattedString
                    self.envoyer(["type": resultat.isFinal ? "final" : "partiel", "texte": texte])
                    if resultat.isFinal { self.terminer() }
                } else if let erreur, self.enCours {
                    let code = (erreur as NSError).code
                    // 1110 : rien entendu ; 216 / 301 : arrêt demandé. Pas des erreurs pour la personne.
                    if ![1110, 216, 301].contains(code) { self.erreur(erreur.localizedDescription) } else { self.terminer() }
                }
            }
        }

        do {
            moteurAudio.prepare()
            try moteurAudio.start()
        } catch {
            return erreur("le micro n'a pas pu s'ouvrir (\(error.localizedDescription))")
        }
        enCours = true
        envoyer(["type": "pret"])
    }

    /// La variante de la langue reconnue sur l'ordinateur : celle demandée, puis celle de la région du Mac
    /// (fr-CH en Suisse…), puis n'importe quelle variante de la même langue dont le modèle est installé.
    static func reconnaisseurLocal(_ langue: String) -> SFSpeechRecognizer? {
        let base = String(langue.prefix(2))
        let region = Locale.current.region?.identifier ?? ""
        var candidats = [langue, "\(base)-\(region)"]
        candidats += SFSpeechRecognizer.supportedLocales().map(\.identifier).filter { $0.hasPrefix(base) }.sorted()
        for id in candidats {
            if let r = SFSpeechRecognizer(locale: Locale(identifier: id)), r.supportsOnDeviceRecognition { return r }
        }
        return nil
    }

    func arreter() {
        guard enCours else { return envoyer(["type": "fin"]) }
        // Fin du son : la reconnaissance rend son dernier résultat (isFinal), puis terminer().
        moteurAudio.stop()
        moteurAudio.inputNode.removeTap(onBus: 0)
        requete?.endAudio()
    }

    private func terminer() {
        guard enCours else { return }
        enCours = false
        if moteurAudio.isRunning { moteurAudio.stop() }
        moteurAudio.inputNode.removeTap(onBus: 0)
        requete = nil
        tache = nil
        envoyer(["type": "fin"])
    }

    private func erreur(_ message: String) {
        tache?.cancel()
        if moteurAudio.isRunning { moteurAudio.stop() }
        moteurAudio.inputNode.removeTap(onBus: 0)
        requete = nil
        tache = nil
        enCours = false
        envoyer(["type": "erreur", "message": message])
    }

    private func envoyer(_ m: [String: String]) {
        guard let donnees = try? JSONSerialization.data(withJSONObject: m),
              let json = String(data: donnees, encoding: .utf8) else { return }
        vue?.evaluateJavaScript("window.dicteeRetour && window.dicteeRetour(\(json))")
    }
}
