import AVKit
import SwiftUI

private enum BrainStyle {
    static let ink = Color(red: 0.09, green: 0.10, blue: 0.09)
    static let paper = Color(red: 0.96, green: 0.95, blue: 0.91)
    static let muted = Color(red: 0.42, green: 0.44, blue: 0.40)
    static let accent = Color(red: 0.77, green: 0.91, blue: 0.53)
}

private struct BoardDestination: Identifiable {
    let id: String
    let url: URL
}

struct BrainView: View {
    @ObservedObject var model: BrainModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var board: BoardDestination?
    @State private var voiceConsent = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    masthead
                    Picker("Camera source", selection: Binding(get: { model.mode }, set: { next in Task { await model.selectMode(next) } })) {
                        ForEach(CaptureMode.allCases) { mode in Text(mode.rawValue).tag(mode) }
                    }
                    .pickerStyle(.segmented)
                    cameraSurface
                    actions
                    VStack(alignment: .leading, spacing: 7) {
                        Text(model.headline).font(.system(size: 28, weight: .semibold, design: .rounded)).accessibilityAddTraits(.isHeader)
                        Text(model.detail).font(.subheadline).foregroundStyle(BrainStyle.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    if let message = model.errorMessage {
                        Label(message, systemImage: "exclamationmark.circle")
                            .font(.subheadline).foregroundStyle(.red).padding(16)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(.red.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))

                    }
                    resultList
                    voiceControls
                    Text("Only your connected library receives comparison frames. Frames are not saved. Camera stops when you leave the app.")
                        .font(.caption).foregroundStyle(BrainStyle.muted).fixedSize(horizontal: false, vertical: true)
                }
                .padding(22)
                .frame(maxWidth: 680)
                .frame(maxWidth: .infinity)
            }
            .background(BrainStyle.paper)
            .foregroundStyle(BrainStyle.ink)
            .toolbar(.hidden, for: .navigationBar)
            .sheet(isPresented: $model.showConnection) { connectionSheet }
            .sheet(item: $board) { destination in
                if let client = model.client {
                    NavigationStack {
                        PrivateBoardView(client: client, url: destination.url)
                            .navigationTitle("Your board").navigationBarTitleDisplayMode(.inline)
                            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { board = nil } } }
                    }
                }
            }
            .confirmationDialog("Turn on natural voice?", isPresented: $voiceConsent, titleVisibility: .visible) {
                Button("Turn on voice") { model.voiceEnabled = true }
            } message: {
                Text("Spoken image titles and board names go to your library’s configured natural-voice service. Camera frames do not go to that voice service.")
            }
            .onChange(of: model.errorMessage) { _, message in
                if let message { UIAccessibility.post(notification: .announcement, argument: message) }
            }
            .onChange(of: scenePhase) { _, phase in
                if phase == .background { Task { await model.stop() } }
            }
        }
        .tint(BrainStyle.ink)
    }

    private var masthead: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Visual Brain").font(.system(size: 27, weight: .bold, design: .rounded)).tracking(-0.8)
                Text("POINT & REOPEN").font(.system(size: 10, weight: .semibold, design: .monospaced)).tracking(2.2).foregroundStyle(BrainStyle.muted)
            }
            Spacer()
            Button { model.showConnection = true } label: {
                Label(model.client == nil ? "Connect" : "Library", systemImage: model.client == nil ? "link" : "lock.shield")
                    .font(.subheadline.weight(.medium)).padding(.horizontal, 13).padding(.vertical, 11)
                    .background(.white.opacity(0.75), in: Capsule())
            }
            .accessibilityLabel(model.client == nil ? "Connect private library" : "Library settings")
        }
    }

    private var cameraSurface: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 25).fill(BrainStyle.ink)
            if let preview = model.preview {
                Image(uiImage: preview).resizable().scaledToFill()
                    .frame(maxWidth: .infinity).frame(height: 286).clipped()
                    .accessibilityLabel("Current camera preview")
            } else {
                VStack(spacing: 13) {
                    Image(systemName: model.mode == .glasses ? "eyeglasses" : "camera")
                        .font(.system(size: 47, weight: .light)).foregroundStyle(BrainStyle.accent)
                    Text(model.mode == .glasses ? "See something familiar?" : "Bring your saved ideas into view.")
                        .font(.headline).foregroundStyle(.white)
                    Text(model.mode == .glasses ? "Connect through Meta AI. Start when you’re ready." : "Use your iPhone camera with the same private library.")
                        .font(.caption).foregroundStyle(.white.opacity(0.65)).multilineTextAlignment(.center)
                }
                .padding(27)
            }
            VStack {
                HStack {
                    Label(model.sourceState.label, systemImage: model.active ? "circle.fill" : "camera.fill")
                        .font(.caption.weight(.medium)).padding(10)
                        .background(.black.opacity(0.65), in: Capsule()).foregroundStyle(.white)
                    Spacer()
                    if model.matching { ProgressView().tint(.white).padding(10).background(.black.opacity(0.5), in: Circle()) }
                }
                Spacer()
                HStack {
                    Image(systemName: "viewfinder").font(.system(size: 30, weight: .ultraLight))
                    Spacer()
                    Text(model.active ? "LIVE · NOT SAVED" : "CAMERA OFF").font(.system(size: 10, weight: .medium, design: .monospaced)).tracking(1.3)
                }
                .foregroundStyle(.white.opacity(0.8))
            }
            .padding(17)
        }
        .frame(height: 286).clipShape(RoundedRectangle(cornerRadius: 25))
        .accessibilityElement(children: .contain)
    }

    private var actions: some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                Button {
                    if model.active || model.starting { Task { await model.stop() } }
                    else { model.start() }
                } label: {
                    Label(model.starting ? "Cancel" : model.active ? "Stop looking" : "Start looking", systemImage: model.active || model.starting ? "stop.fill" : "viewfinder")
                        .font(.headline).frame(maxWidth: .infinity).padding(.vertical, 17)
                        .background(model.active ? BrainStyle.ink : BrainStyle.accent, in: Capsule())
                        .foregroundStyle(model.active ? .white : BrainStyle.ink)
                }
                .disabled(model.stopping)
                if model.mode == .glasses && !model.active && !model.starting {
                    Button { Task { await model.connectGlasses() } } label: {
                        Image(systemName: "link").font(.title3).frame(width: 54, height: 54)
                            .background(.white, in: Circle())
                    }
                    .accessibilityLabel("Connect Meta glasses through Meta AI")
                }
            }
            HStack {
                Toggle("Compare as I look", isOn: $model.continuous).font(.subheadline)
                if !model.continuous {
                    Button("Compare now") { model.compareLatest() }.font(.subheadline.weight(.semibold)).disabled(!model.canCompare)
                }
            }
            .tint(BrainStyle.muted)
        }
    }

    @ViewBuilder private var resultList: some View {
        if !model.matches.isEmpty {
            VStack(spacing: 10) {
                ForEach(model.matches) { match in
                    Button {
                        Task { await model.stop() }
                        if let url = model.client?.boardURL(for: match) { board = BoardDestination(id: match.id, url: url) }
                    } label: {
                        HStack(spacing: 14) {
                            Group {
                                if let thumbnail = model.thumbnails[match.id] { Image(uiImage: thumbnail).resizable().scaledToFill() }
                                else { ZStack { BrainStyle.paper; Image(systemName: "photo").foregroundStyle(BrainStyle.muted) } }
                            }
                            .frame(width: 64, height: 64).clipShape(RoundedRectangle(cornerRadius: 11)).accessibilityHidden(true)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(match.displayTitle).font(.subheadline.weight(.semibold)).lineLimit(2)
                                Text(match.location).font(.caption).foregroundStyle(BrainStyle.muted).lineLimit(2)
                            }
                            Spacer(minLength: 5)
                            Image(systemName: "arrow.up.right").font(.subheadline)
                        }
                        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                        .background(.white.opacity(0.85), in: RoundedRectangle(cornerRadius: 19))
                    }
                    .buttonStyle(.plain)
                    .disabled(match.boards.isEmpty)
                    .accessibilityLabel("Open \(match.displayTitle), \(match.location)")
                }
            }
        }
    }

    private var voiceControls: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label("Natural voice", systemImage: "waveform").font(.subheadline.weight(.medium))
                Spacer()
                Button { if model.voiceEnabled { model.voiceEnabled = false } else { voiceConsent = true } } label: {
                    Text(model.voiceEnabled ? "On" : "Off").font(.subheadline.weight(.semibold)).padding(.horizontal, 17).padding(.vertical, 9)
                        .background(model.voiceEnabled ? BrainStyle.accent : .white, in: Capsule())
                }
                .disabled(!model.voiceAvailable)
                .accessibilityLabel(model.voiceEnabled ? "Turn natural voice off" : "Turn natural voice on")
                AudioRoutePicker().frame(width: 32, height: 32).accessibilityLabel("Choose audio output")
            }
            Text(model.voiceMessage).font(.caption).foregroundStyle(BrainStyle.muted)
        }
        .padding(17).background(BrainStyle.ink.opacity(0.035), in: RoundedRectangle(cornerRadius: 18))
    }

    private var connectionSheet: some View {
        NavigationStack {
            Form {
                Section("Private library") {
                    TextField("https://your-private-library.example", text: $model.libraryAddress)
                        .keyboardType(.URL).textContentType(.URL).autocorrectionDisabled().textInputAutocapitalization(.never)
                        .accessibilityLabel("Private library HTTPS address")
                    SecureField("Library passphrase", text: $model.passphrase).textContentType(.password)
                    Text("Use your own private hosted library. The shared playground cannot receive camera frames.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    Button { Task { await model.connectLibrary() } } label: {
                        HStack { Text(model.connecting ? "Connecting…" : "Connect securely"); Spacer(); if model.connecting { ProgressView() } }
                    }.disabled(model.connecting)
                    if let error = model.errorMessage { Text(error).font(.caption).foregroundStyle(.red) }
                }
                Section("Your privacy") {
                    Text("Your passphrase and session stay in memory. Closing the app signs this companion out. Only the library address is remembered.")
                        .font(.footnote)
                    Text("When voice is on, spoken titles and board names go to the library’s natural-voice service. Camera frames do not go to that voice service.")
                        .font(.footnote)
                }
                if model.client != nil {
                    Section { Button("Disconnect library", role: .destructive) { Task { await model.signOut(); model.showConnection = false } } }
                }
            }
            .navigationTitle("Connect library").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { model.showConnection = false; model.passphrase = "" } } }
        }
    }
}

private struct AudioRoutePicker: UIViewRepresentable {
    func makeUIView(context: Context) -> AVRoutePickerView {
        let view = AVRoutePickerView()
        view.tintColor = .secondaryLabel
        view.activeTintColor = .label
        return view
    }
    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {}
}
