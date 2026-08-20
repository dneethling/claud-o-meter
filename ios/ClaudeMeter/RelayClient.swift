import Foundation

// Talks to the local `ios_relay.py` over your LAN. The phone tells the relay its
// Live Activity push token; the relay then pushes usage updates to that token via
// Apple. All requests are plain JSON POSTs to http://<host>:<port>.
struct RelayHealth: Codable {
    var ok: Bool
    var tokens: Int
    var status: String?
    var updated_epoch: Int?
    var push_enabled: Bool?
}

actor RelayClient {
    private let base: URL
    private let secret: String?

    init?(host: String, port: Int, secret: String?) {
        let trimmed = host.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty,
              let url = URL(string: "http://\(trimmed):\(port)") else { return nil }
        self.base = url
        self.secret = (secret?.isEmpty == false) ? secret : nil
    }

    private func request(_ path: String, method: String, body: [String: Any]?) async throws -> (Int, Data) {
        var req = URLRequest(url: base.appendingPathComponent(path))
        req.httpMethod = method
        req.timeoutInterval = 10
        if let secret { req.setValue(secret, forHTTPHeaderField: "X-Relay-Secret") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        return (code, data)
    }

    @discardableResult
    func register(token: String, activityId: String) async -> Bool {
        do {
            let (code, _) = try await request("register", method: "POST",
                                              body: ["token": token, "activityId": activityId])
            return code == 200
        } catch {
            return false
        }
    }

    @discardableResult
    func unregister(token: String) async -> Bool {
        do {
            let (code, _) = try await request("unregister", method: "POST",
                                              body: ["token": token])
            return code == 200
        } catch {
            return false
        }
    }

    func health() async -> RelayHealth? {
        do {
            let (code, data) = try await request("health", method: "GET", body: nil)
            guard code == 200 else { return nil }
            return try JSONDecoder().decode(RelayHealth.self, from: data)
        } catch {
            return nil
        }
    }
}
