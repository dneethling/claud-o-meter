import SwiftUI

struct ContentView: View {
    @AppStorage("relayHost") private var relayHost = ""
    @AppStorage("relayPort") private var relayPort = 8787
    @AppStorage("relaySecret") private var relaySecret = ""

    @StateObject private var controller = LiveActivityController()
    @State private var health: RelayHealth?
    @State private var checking = false

    private var relay: RelayClient? {
        RelayClient(host: relayHost, port: relayPort,
                    secret: relaySecret.isEmpty ? nil : relaySecret)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Relay") {
                    TextField("Host or IP (e.g. 192.168.1.20)", text: $relayHost)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    HStack {
                        Text("Port")
                        Spacer()
                        TextField("8787", value: $relayPort, format: .number.grouping(.never))
                            .keyboardType(.numberPad)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 90)
                    }
                    SecureField("Shared secret (optional)", text: $relaySecret)
                    Button {
                        Task { await checkHealth() }
                    } label: {
                        HStack {
                            Text("Test connection")
                            if checking { Spacer(); ProgressView() }
                        }
                    }
                    if let health {
                        HStack {
                            Image(systemName: health.push_enabled == true
                                  ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(health.push_enabled == true ? .green : .orange)
                            Text(health.push_enabled == true
                                 ? "Connected · \(health.tokens) device(s) · push on"
                                 : "Connected, but APNs isn't configured on the relay")
                                .font(.footnote)
                        }
                    }
                }

                Section("Live Activity") {
                    if !controller.activitiesEnabled {
                        Label("Live Activities are off in iOS Settings", systemImage: "bell.slash")
                            .foregroundStyle(.orange)
                    }
                    if controller.isRunning {
                        Button(role: .destructive) {
                            controller.stop(relay: relay)
                        } label: {
                            Label("Stop the meter", systemImage: "stop.circle")
                        }
                    } else {
                        Button {
                            controller.start(relay: relay)
                        } label: {
                            Label("Start the meter", systemImage: "gauge.with.dots.needle.33percent")
                        }
                        .disabled(relayHost.isEmpty)
                    }

                    if let token = controller.pushToken {
                        LabeledContent("Push token") {
                            Text("…\(token.suffix(12))").font(.footnote.monospaced())
                        }
                    }
                    if let err = controller.lastError {
                        Text(err).font(.footnote).foregroundStyle(.red)
                    }
                }

                Section {
                    Text("The meter shows in the Dynamic Island and on the Lock Screen. "
                         + "Keep the relay running on your Mac/PC on the same Wi-Fi; it pushes "
                         + "live updates even when this app is closed.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Claude Meter")
            .onAppear { controller.syncExisting(relay: relay) }
        }
    }

    private func checkHealth() async {
        checking = true
        defer { checking = false }
        health = await relay?.health()
    }
}

#Preview {
    ContentView()
}
