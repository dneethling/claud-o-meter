import Foundation
import CommonCrypto

/// Reads Claude.ai cookies directly from the user's installed Chromium browser
/// (Arc, Chrome, Brave, Chromium). Decrypts using the browser's Safe Storage key
/// from macOS Keychain. No Python dependency — pure Swift + system SQLite3 + CommonCrypto.
@MainActor
class BrowserCookieImporter {

    struct BrowserInfo {
        let name: String
        let keychainService: String
        let cookieDBPaths: [String]
    }

    private static let browsers: [BrowserInfo] = {
        let home = NSHomeDirectory()
        return [
            BrowserInfo(
                name: "Arc",
                keychainService: "Arc Safe Storage",
                cookieDBPaths: [
                    "\(home)/Library/Application Support/Arc/User Data/Default/Cookies",
                ] + ((try? FileManager.default.contentsOfDirectory(
                    atPath: "\(home)/Library/Application Support/Arc/User Data/"
                ).filter { $0.hasPrefix("Profile ") }.map {
                    "\(home)/Library/Application Support/Arc/User Data/\($0)/Cookies"
                }) ?? [])
            ),
            BrowserInfo(
                name: "Chrome",
                keychainService: "Chrome Safe Storage",
                cookieDBPaths: [
                    "\(home)/Library/Application Support/Google/Chrome/Default/Cookies",
                ] + ((try? FileManager.default.contentsOfDirectory(
                    atPath: "\(home)/Library/Application Support/Google/Chrome/"
                ).filter { $0.hasPrefix("Profile ") }.map {
                    "\(home)/Library/Application Support/Google/Chrome/\($0)/Cookies"
                }) ?? [])
            ),
            BrowserInfo(
                name: "Brave",
                keychainService: "Brave Safe Storage",
                cookieDBPaths: [
                    "\(home)/Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
                ]
            ),
            BrowserInfo(
                name: "Chromium",
                keychainService: "Chromium Safe Storage",
                cookieDBPaths: [
                    "\(home)/Library/Application Support/Chromium/Default/Cookies",
                ]
            ),
        ]
    }()

    struct CookieResult {
        let browserName: String
        let cookies: [HTTPCookie]
        let orgId: String?
    }

    enum ImportError: Error, LocalizedError {
        case noBrowserFound
        case keychainFailed(String)
        case noCookiesFound
        case decryptionFailed

        var errorDescription: String? {
            switch self {
            case .noBrowserFound: return "No supported browser found (Chrome, Arc, Brave)"
            case .keychainFailed(let msg): return "Keychain access failed: \(msg)"
            case .noCookiesFound: return "No Claude cookies found. Log into claude.ai in your browser first."
            case .decryptionFailed: return "Failed to decrypt browser cookies"
            }
        }
    }

    /// Try to import Claude cookies from the most recently modified browser profile.
    static func importCookies() throws -> CookieResult {
        // Find all existing cookie DB files, sorted by most recently modified
        var candidates: [(BrowserInfo, String, Date)] = []
        let fm = FileManager.default

        for browser in browsers {
            for path in browser.cookieDBPaths {
                if fm.fileExists(atPath: path),
                   let attrs = try? fm.attributesOfItem(atPath: path),
                   let modified = attrs[.modificationDate] as? Date {
                    candidates.append((browser, path, modified))
                }
            }
        }

        candidates.sort { $0.2 > $1.2 } // newest first

        guard !candidates.isEmpty else {
            throw ImportError.noBrowserFound
        }

        // Try each candidate until we find Claude cookies
        for (browser, cookiePath, _) in candidates {
            guard let password = getKeychainPassword(service: browser.keychainService) else {
                continue
            }

            let derivedKey = deriveKey(password: password)

            if let cookies = readAndDecryptCookies(dbPath: cookiePath, key: derivedKey) {
                let sessionCookie = cookies.first { $0.name == "sessionKey" }
                let orgCookie = cookies.first { $0.name == "lastActiveOrg" }

                if sessionCookie != nil {
                    return CookieResult(
                        browserName: browser.name,
                        cookies: cookies,
                        orgId: orgCookie?.value
                    )
                }
            }
        }

        throw ImportError.noCookiesFound
    }

    // MARK: - Keychain

    private static func getKeychainPassword(service: String) -> String? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = ["find-generic-password", "-w", "-s", service]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()

        do {
            try process.run()
            process.waitUntilExit()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            return nil
        }
    }

    // MARK: - Key Derivation (PBKDF2)

    private static func deriveKey(password: String) -> Data {
        let passwordData = password.data(using: .utf8)!
        let salt = "saltysalt".data(using: .utf8)!
        let iterations: UInt32 = 1003
        let keyLength = 16 // AES-128

        var derivedKey = Data(count: keyLength)
        derivedKey.withUnsafeMutableBytes { derivedBytes in
            passwordData.withUnsafeBytes { passwordBytes in
                salt.withUnsafeBytes { saltBytes in
                    CCKeyDerivationPBKDF(
                        CCPBKDFAlgorithm(kCCPBKDF2),
                        passwordBytes.baseAddress?.assumingMemoryBound(to: Int8.self),
                        passwordData.count,
                        saltBytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                        salt.count,
                        CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA1),
                        iterations,
                        derivedBytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                        keyLength
                    )
                }
            }
        }
        return derivedKey
    }

    // MARK: - SQLite Cookie Reading

    private static func readAndDecryptCookies(dbPath: String, key: Data) -> [HTTPCookie]? {
        // Copy the DB to a temp file (browser may have it locked)
        let tempPath = NSTemporaryDirectory() + "claudometer_cookies_\(UUID().uuidString).db"
        let fm = FileManager.default

        do {
            try fm.copyItem(atPath: dbPath, toPath: tempPath)
        } catch {
            return nil
        }
        defer { try? fm.removeItem(atPath: tempPath) }

        // Use the sqlite3 command line tool to query (avoids linking C SQLite)
        let query = """
        SELECT name, hex(encrypted_value), host_key, path, is_secure, is_httponly, expires_utc
        FROM cookies
        WHERE host_key LIKE '%claude.ai%'
        AND name IN ('sessionKey', 'lastActiveOrg', 'anthropic-device-id', 'cf_clearance', '__cf_bm');
        """

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/sqlite3")
        process.arguments = [tempPath, query]

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()

        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            return nil
        }

        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""

        var cookies: [HTTPCookie] = []

        for line in output.split(separator: "\n") {
            let parts = line.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            guard parts.count >= 7 else { continue }

            let name = parts[0]
            let encryptedHex = parts[1]
            let host = parts[2]
            let path = parts[3]
            let isSecure = parts[4] == "1"
            let _ = parts[5] == "1" // isHttpOnly

            // Decrypt the value
            guard let encryptedData = Data(hexString: encryptedHex) else { continue }
            guard let decrypted = decryptCookieValue(encryptedData, key: key) else { continue }

            var properties: [HTTPCookiePropertyKey: Any] = [
                .name: name,
                .value: decrypted,
                .domain: host,
                .path: path,
            ]
            if isSecure { properties[.secure] = "TRUE" }

            if let cookie = HTTPCookie(properties: properties) {
                cookies.append(cookie)
            }
        }

        return cookies.isEmpty ? nil : cookies
    }

    // MARK: - AES Decryption

    private static func decryptCookieValue(_ encrypted: Data, key: Data) -> String? {
        // Chrome cookie format: "v10" or "v11" prefix + AES-128-CBC encrypted data
        guard encrypted.count > 3 else { return nil }

        let prefix = String(data: encrypted[0..<3], encoding: .utf8)
        guard prefix == "v10" || prefix == "v11" else {
            // Not encrypted — try plain text
            return String(data: encrypted, encoding: .utf8)
        }

        let ciphertext = encrypted[3...]
        let iv = Data(repeating: 0x20, count: 16) // Chrome uses space-filled IV on macOS

        let bufferSize = ciphertext.count + kCCBlockSizeAES128
        var decryptedBuffer = [UInt8](repeating: 0, count: bufferSize)
        var numBytesDecrypted: size_t = 0

        let status = key.withUnsafeBytes { keyBytes in
            ciphertext.withUnsafeBytes { cipherBytes in
                iv.withUnsafeBytes { ivBytes in
                    CCCrypt(
                        CCOperation(kCCDecrypt),
                        CCAlgorithm(kCCAlgorithmAES128),
                        CCOptions(kCCOptionPKCS7Padding),
                        keyBytes.baseAddress, key.count,
                        ivBytes.baseAddress,
                        cipherBytes.baseAddress, ciphertext.count,
                        &decryptedBuffer, bufferSize,
                        &numBytesDecrypted
                    )
                }
            }
        }

        guard status == kCCSuccess else { return nil }
        let decryptedData = Data(decryptedBuffer[..<numBytesDecrypted])

        // Cookie DB v24+: Chrome prepends a 32-byte SHA-256 hash to the plaintext.
        // Skip it to get the actual cookie value.
        if decryptedData.count > 32 {
            if let value = String(data: decryptedData[32...], encoding: .utf8) {
                return value
            }
        }
        // Older format or short values — try the whole thing
        return String(data: decryptedData, encoding: .utf8)
    }
}

// MARK: - Data hex helper

extension Data {
    init?(hexString: String) {
        let hex = hexString
        guard hex.count % 2 == 0 else { return nil }

        var data = Data(capacity: hex.count / 2)
        var index = hex.startIndex

        for _ in 0..<hex.count / 2 {
            let nextIndex = hex.index(index, offsetBy: 2)
            guard let byte = UInt8(hex[index..<nextIndex], radix: 16) else { return nil }
            data.append(byte)
            index = nextIndex
        }

        self = data
    }
}
