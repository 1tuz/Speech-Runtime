#if os(macOS)
import SwiftUI
import RuntimeUIModel

struct RuntimePanel: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var languageSelection: AppLanguage = .storedValue()
    @State private var languageVersion = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            power
            updateBanner
            if let message = model.failure {
                Label(message, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
                    .textSelection(.enabled).lineLimit(6)
                    .padding(10).frame(maxWidth: .infinity, alignment: .leading)
                    .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            }
            if model.busy {
                VStack(alignment: .leading, spacing: 8) {
                    if let progress = model.downloadProgress, model.phase == .downloading {
                        ProgressView(value: min(max(progress, 0), 1))
                        Text(String(format: L("download.percent"), progress * 100))
                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                    } else { ProgressView().controlSize(.small) }
                    if model.phase == .downloading || model.phase == .preparing {
                        Button(L("cancel"), action: model.cancelSetup).buttonStyle(.borderless)
                    }
                }
            }
            modelCard
            HStack(spacing: 10) {
                metric(title: "CPU", value: model.cpuPercent.map { String(format: "%.1f %%", $0) } ?? "\u{2014}", icon: "cpu")
                metric(title: "RAM", value: bytes(model.rssBytes), icon: "memorychip")
            }
            VStack(spacing: 9) {
                detail(L("metric.ane"), bytes(model.neuralBytes))
                detail(L("metric.sessions"), model.running ? "\(model.sessions)" : "\u{2014}")
                detail(L("metric.queue"), model.queue.map(String.init) ?? "\u{2014}")
            }
            Text(L("metric.note")).font(.system(size: 10)).foregroundStyle(.tertiary).fixedSize(horizontal: false, vertical: true)
            Divider()
            if model.settingsVisible {
                VStack(alignment: .leading, spacing: 10) {
                    Picker(L("language.title"), selection: languageBinding) {
                        Text(L("language.system")).tag(AppLanguage.system)
                        Text(L("language.english")).tag(AppLanguage.en)
                        Text(L("language.russian")).tag(AppLanguage.ru)
                    }
                    Text(L("language.note")).font(.caption2).foregroundStyle(.secondary)
                    Picker(L("idle.title"), selection: $model.idleMinutes) {
                        ForEach([5, 15, 30, 60], id: \.self) { value in
                            Text(String(format: L("idle.minutes"), value)).tag(value)
                        }
                    }
                    .onChange(of: model.idleMinutes) { _, _ in model.saveIdlePreference() }
                    Text(L("idle.note")).font(.caption2).foregroundStyle(.secondary)
                    Divider()
                    // Permissions: the one OS approval the app participates in.
                    Toggle(isOn: Binding(get: { model.loginEnabled }, set: model.setLogin)) {
                        Text(L("permissions.login.title")).font(.system(size: 12, weight: .medium))
                    }.toggleStyle(.switch).controlSize(.small)
                    HStack {
                        Text(L("permissions.login.note"))
                            .font(.caption2).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer()
                        Button(L("permissions.openSettings"), action: model.openLoginSettings)
                            .font(.caption2).buttonStyle(.link)
                    }
                    Divider()
                    // Updates: silent check at launch when enabled; manual from
                    // the right-click menu at any time.
                    Toggle(isOn: Binding(get: { model.autoUpdate }, set: { model.autoUpdate = $0 })) {
                        Text(L("update.auto")).font(.system(size: 12, weight: .medium))
                    }.toggleStyle(.switch).controlSize(.small)
                    Text(L("update.note")).font(.caption2).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Text(L("capability.timestamps"))
                        Spacer()
                        Text(RuntimeCapabilities.timestamps ? L("capability.value.true") : L("capability.value.false"))
                            .foregroundStyle(RuntimeCapabilities.timestamps ? .green : .secondary)
                    }.font(.system(size: 11))
                    Button(L("files.open"), action: model.revealFiles).buttonStyle(.link)
                    Text("127.0.0.1:8477 \u{00B7} API v1")
                        .font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                }.padding(12).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 12))
            }
            footer
        }
        .padding(20)
        .frame(width: 356)
        .background(reduceTransparency ? Color(nsColor: .windowBackgroundColor) : .clear)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: model.settingsVisible)
        // Re-render all localized strings when the stored language changes.
        .id(languageVersion)
        .onReceive(NotificationCenter.default.publisher(for: .appLanguageChanged)) { _ in
            languageVersion += 1
        }
    }

    private var languageBinding: Binding<AppLanguage> {
        Binding(
            get: { languageSelection },
            set: { newValue in
                languageSelection = newValue
                newValue.store()
                NotificationCenter.default.post(name: .appLanguageChanged, object: nil)
            }
        )
    }

    /// New-version banner: appears only when a newer GitHub release exists.
    private var updateBanner: some View {
        Group {
            if model.updating {
                VStack(alignment: .leading, spacing: 6) {
                    Text(L("update.downloading")).font(.caption).foregroundStyle(.secondary)
                    if let progress = model.updateProgress {
                        ProgressView(value: min(max(progress, 0), 1))
                    } else {
                        ProgressView().controlSize(.small)
                    }
                }
                .padding(10)
                .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            } else if let tag = model.updateAvailableVersion {
                HStack {
                    Label(String(format: L("update.available"), tag), systemImage: "arrow.down.circle")
                        .font(.caption).foregroundStyle(.blue)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button(L("update.install"), action: model.installUpdate)
                        .controlSize(.small)
                }
                .padding(10)
                .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
            } else if let message = model.updateMessage {
                HStack {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("✕") { model.dismissUpdateMessage() }
                        .buttonStyle(.borderless).controlSize(.mini)
                }
                .padding(8)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "waveform").font(.system(size: 19, weight: .medium)).foregroundStyle(Color.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                Text("Speech Runtime").font(.system(size: 16, weight: .semibold, design: .rounded))
                Text(L("app.subtitle")).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
            }
            Spacer()
            Circle().fill(statusColor).frame(width: 7, height: 7)
                .accessibilityLabel(model.statusText)
        }
    }
    private var power: some View {
        Button(action: model.togglePower) {
            HStack(spacing: 13) {
                Image(systemName: "power").font(.system(size: 24, weight: .medium))
                    .frame(width: 34, height: 40)
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.running ? L("power.off") : L("power.on"))
                        .font(.system(size: 17, weight: .semibold, design: .rounded))
                    Text(model.statusText).font(.system(size: 11)).foregroundStyle(.secondary)
                }
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                else { Image(systemName: model.running ? "checkmark.circle.fill" : "chevron.right").font(.system(size: 16)) }
            }
            .padding(.horizontal, 16).padding(.vertical, 14)
            .contentShape(RoundedRectangle(cornerRadius: 18))
            .nativeGlass(active: model.running, reduceTransparency: reduceTransparency)
        }
        .buttonStyle(.plain)
        .disabled(model.busy)
        .accessibilityLabel(model.running ? L("power.off") : L("power.on"))
        .accessibilityValue(model.statusText)
    }
    private var modelCard: some View {
        let deviceLabel: String = {
            guard let device = model.snapshot?.selectedModel?.device else { return "Apple Neural Engine" }
            return device.lowercased().contains("ane") ? "Apple Neural Engine" : device
        }()
        return HStack(alignment: .center, spacing: 10) {
            Image(systemName: "waveform.badge.mic").font(.title3).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text(model.snapshot?.selectedModel?.model ?? "Parakeet Ultra")
                    .font(.system(size: 13, weight: .semibold))
                Text("Q8_0 \u{00B7} \(deviceLabel)")
                    .font(.system(size: 10)).foregroundStyle(.secondary)
            }
            Spacer()
            Text(model.snapshot?.modelLoaded == true ? L("model.loaded") : L("model.idle"))
                .font(.system(size: 9, weight: .semibold))
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(Color.primary.opacity(0.055), in: Capsule())
        }
    }
    private func metric(title: String, value: String, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon).font(.system(size: 10, weight: .medium)).foregroundStyle(.secondary)
            Text(value).font(.system(size: 21, weight: .medium, design: .rounded)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.7)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(13)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 14))
    }
    private func detail(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).monospacedDigit()
        }.font(.system(size: 11))
    }
    private var footer: some View {
        HStack(spacing: 8) {
            Button {
                model.settingsVisible.toggle()
            } label: {
                Label(L("settings"), systemImage: "gearshape")
            }
            .help(L("settings")).accessibilityLabel(L("settings"))
            Button(action: model.showLog) {
                Label(L("log"), systemImage: "doc.text.magnifyingglass")
            }
            .help(L("log")).accessibilityLabel(L("log"))
            Button(action: { model.checkForUpdates(manual: true) }) {
                Label(L("update.check.short"), systemImage: "arrow.down.circle")
            }
            .help(L("update.check")).accessibilityLabel(L("update.check"))
            Spacer()
            // The quit affordance must be readable, not a mystery icon: a labeled
            // button stating what it does (UI quits, service stays).
            Button(action: model.quitUI) {
                Label(L("quit.title"), systemImage: "rectangle.portrait.and.arrow.forward")
            }
            .help(L("quit.note")).accessibilityLabel(L("quit.title"))
        }
        .buttonStyle(.borderless)
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }
    private var statusColor: Color {
        if model.phase == .error { return .orange }
        return model.running ? .green : .secondary
    }
    private func bytes(_ value: UInt64?) -> String {
        guard let value else { return "\u{2014}" }
        return String(format: "%.0f MiB", Double(value) / 1_048_576)
    }
}

private extension View {
    @ViewBuilder
    func nativeGlass(active: Bool, reduceTransparency: Bool) -> some View {
        if reduceTransparency {
            self.background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 18))
        } else {
            #if compiler(>=6.2)
            if #available(macOS 26.0, *) {
                self.glassEffect(.regular.tint(active ? Color.accentColor.opacity(0.16) : .clear).interactive(),
                                 in: RoundedRectangle(cornerRadius: 18))
            } else {
                self.background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
                    .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(Color.primary.opacity(0.08)))
            }
            #else
            self.background(.regularMaterial, in: RoundedRectangle(cornerRadius: 18))
            #endif
        }
    }
}
#endif
