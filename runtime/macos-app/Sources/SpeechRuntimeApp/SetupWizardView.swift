#if os(macOS)
import AppKit
import RuntimeUIModel
import ServiceManagement
import SwiftUI

/// First-run setup wizard hosted in its own centered window. Runs independently of
/// the status popover, so the menu remains usable while setup is open.
struct SetupWizardView: View {
    @ObservedObject var model: AppModel
    let onFinish: (Bool) -> Void  // true = "Turn on now", false = skip
    @State private var state = SetupWizardState()
    @State private var languageSelection: AppLanguage = .storedValue()
    @State private var loginToggle = false
    @State private var idleChoice = 15
    @State private var languageVersion = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Brand header: the same waveform + rounded type the panel uses,
            // so the wizard reads as the same product, not a stray utility.
            HStack(spacing: 12) {
                Image(systemName: "waveform")
                    .font(.system(size: 24, weight: .medium))
                    .foregroundStyle(Color.accentColor)
                    .frame(width: 40, height: 40)
                    .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Speech Runtime")
                        .font(.system(size: 16, weight: .semibold, design: .rounded))
                    Text(L("app.subtitle"))
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                stepDots
            }
            .padding(.bottom, 18)

            Group {
                switch state.step {
                case .welcome: welcomeStep
                case .login: loginStep
                case .idle: idleStep
                case .capabilities: capabilitiesStep
                case .ready: readyStep
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)

            Divider().padding(.top, 12)
            controls.padding(.top, 12)
        }
        .padding(28)
        .frame(width: 500, height: 420)
        .id(languageVersion)
        .onReceive(NotificationCenter.default.publisher(for: .appLanguageChanged)) { _ in
            languageVersion += 1
        }
        .onAppear { loginToggle = model.loginEnabled }
    }

    /// Step dots: compact progress that needs no localization.
    private var stepDots: some View {
        HStack(spacing: 6) {
            ForEach(SetupWizardState.Step.allCases, id: \.rawValue) { step in
                Capsule()
                    .fill(step == state.step ? Color.accentColor : Color.secondary.opacity(0.3))
                    .frame(width: step == state.step ? 18 : 6, height: 6)
            }
        }
        .accessibilityLabel("\(state.stepNumber)/\(state.totalSteps)")
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: state.step)
    }

    private var welcomeStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("wizard.welcome.title")).font(.system(size: 20, weight: .semibold))
            Text(L("wizard.welcome.body")).font(.system(size: 13)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            // The picker label is a header line, not an inline form label: the
            // segmented control gets the full width so long localizations
            // ("Как в системе") never truncate and the label never wraps mid-word.
            Text(L("wizard.language.title"))
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
            Picker(L("wizard.language.title"), selection: languageBinding) {
                Text(L("language.system")).tag(AppLanguage.system)
                Text(L("language.english")).tag(AppLanguage.en)
                Text(L("language.russian")).tag(AppLanguage.ru)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            Text(L("language.note")).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var loginStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("wizard.login.title")).font(.system(size: 20, weight: .semibold))
            Text(L("wizard.login.body")).font(.system(size: 13)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Toggle(L("wizard.login.toggle"), isOn: $loginToggle)
                .toggleStyle(.switch).controlSize(.small)
                .onChange(of: loginToggle) { _, value in model.setLogin(value) }
            Text(loginStatusText).font(.caption).foregroundStyle(.secondary)
            Button(L("wizard.login.openSettings"), action: model.openLoginSettings)
                .buttonStyle(.link)
        }
    }

    private var loginStatusText: String {
        if model.loginEnabled { return L("wizard.login.status.enabled") }
        if model.loginNeedsApproval { return L("wizard.login.status.approval") }
        return L("wizard.login.status.disabled")
    }

    private var idleStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("wizard.idle.title")).font(.system(size: 20, weight: .semibold))
            Text(L("wizard.idle.body")).font(.system(size: 13)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(L("idle.title"))
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
            Picker(L("idle.title"), selection: $idleChoice) {
                ForEach([5, 15, 30, 60], id: \.self) { value in
                    Text(String(format: L("idle.minutes"), value)).tag(value)
                }
            }
            .labelsHidden()
            .onChange(of: idleChoice) { _, _ in
                model.idleMinutes = idleChoice
                model.saveIdlePreference()
            }
        }
    }

    private var capabilitiesStep: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(L("wizard.capabilities.title")).font(.system(size: 20, weight: .semibold))
            Text(L("wizard.capabilities.body")).font(.system(size: 13)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            capabilityRow(L("capability.streaming"), RuntimeCapabilities.streaming)
            capabilityRow(L("capability.interim"), RuntimeCapabilities.interim)
            capabilityRow(L("capability.timestamps"), RuntimeCapabilities.timestamps)
            capabilityRow(L("capability.diarization"), RuntimeCapabilities.diarization)
        }
    }

    private func capabilityRow(_ label: String, _ value: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: value ? "checkmark.circle" : "minus.circle")
                .foregroundStyle(value ? .green : .secondary)
            Text(label).font(.system(size: 13))
            Spacer()
            Text(value ? L("capability.value.true") : L("capability.value.false"))
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
    }

    private var readyStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("wizard.ready.title")).font(.system(size: 20, weight: .semibold))
            Text(L("wizard.ready.body")).font(.system(size: 13)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var controls: some View {
        HStack {
            Button(L("wizard.back")) { state.back() }
                .disabled(state.isFirst)
            Spacer()
            // Esc cancels the wizard: same action as the Skip button (cancelAction).
            Button(L("wizard.skip")) { finish(turnOn: false) }
                .keyboardShortcut(.cancelAction)
            if state.isLast {
                Button(L("wizard.turnOnNow")) { finish(turnOn: true) }
                    .buttonStyle(.borderedProminent)
            } else {
                Button(state.step == .capabilities ? L("wizard.finish") : L("wizard.next")) { state.next() }
                    .buttonStyle(.borderedProminent)
            }
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

    private func finish(turnOn: Bool) {
        UserDefaults.standard.set(true, forKey: "setupCompleted")
        onFinish(turnOn)
    }
}

/// Owns the wizard NSWindow. Esc (cancelAction on Skip) and the close button end the
/// wizard as a skip; the app and the status panel keep running either way.
@MainActor
final class SetupWizardController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let model: AppModel
    private var finish: ((Bool) -> Void)?

    init(model: AppModel) { self.model = model }

    func show(turnOnNow: @escaping (Bool) -> Void) {
        if let window { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        finish = turnOnNow
        let controller = SetupWizardView(model: model) { [weak self] turnOn in
            self?.complete(turnOn: turnOn)
        }
        let hosting = NSHostingController(rootView: controller)
        let window = NSWindow(contentViewController: hosting)
        window.title = "Speech Runtime Setup"
        window.styleMask = [.titled, .closable]
        window.titlebarAppearsTransparent = true
        window.backgroundColor = .windowBackgroundColor
        window.isMovableByWindowBackground = true
        window.center()
        window.isReleasedWhenClosed = false
        window.level = .floating
        window.delegate = self
        self.window = window
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        // Close box without finishing: treat as skip so setupCompleted is still set.
        if window != nil { complete(turnOn: false) }
    }

    private func complete(turnOn: Bool) {
        let handler = finish
        finish = nil
        window?.orderOut(nil)
        window = nil
        handler?(turnOn)
    }
}
#endif
