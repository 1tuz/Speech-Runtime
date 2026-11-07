#if os(macOS)
import AppKit
import Combine
import SwiftUI

@main
@MainActor
struct SpeechRuntimeApplication {
    static func main() {
        let application = NSApplication.shared
        let delegate = MenuApplicationDelegate()
        application.setActivationPolicy(.accessory)
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}

@MainActor
final class MenuApplicationDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private let model = AppModel()
    private var item: NSStatusItem!
    private var wizardController: SetupWizardController?
    private let popover = NSPopover()
    private var observation: AnyCancellable?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Launch Services usually enforces this; also guard explicit double execution.
        if let id = Bundle.main.bundleIdentifier,
           let other = NSRunningApplication.runningApplications(withBundleIdentifier: id)
              .first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            other.activate(options: [.activateIgnoringOtherApps])
            NSApp.terminate(nil); return
        }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.target = self
        item.button?.action = #selector(statusItemAction(_:))
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        item.button?.toolTip = "Speech Runtime"
        popover.behavior = .transient
        popover.animates = !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        popover.delegate = self
        let hosting = NSHostingController(rootView: RuntimePanel(model: model))
        hosting.sizingOptions = [.intrinsicContentSize, .preferredContentSize]
        popover.contentViewController = hosting
        observation = model.$phase.sink { [weak self] _ in
            DispatchQueue.main.async { self?.updateIcon() }
        }
        let menu = NSMenu()
        let appMenu = NSMenuItem()
        let commands = NSMenu()
        let quit = NSMenuItem(title: L("quit.note"), action: #selector(quitOnlyUI), keyEquivalent: "q")
        quit.target = self
        let setup = NSMenuItem(title: L("menu.setup"), action: #selector(runSetup), keyEquivalent: "")
        setup.target = self
        commands.insertItem(setup, at: 0)
        let separator = NSMenuItem.separator()
        commands.insertItem(separator, at: 1)
        NSApp.mainMenu = menu
        // First run: present setup once; later runs keep the plain panel.
        if UserDefaults.standard.object(forKey: "setupCompleted") == nil { runSetup() }
        model.launch()
    }
    @objc private func runSetup() {
        if wizardController == nil { wizardController = SetupWizardController(model: model) }
        wizardController?.show { [weak self] turnOn in
            guard turnOn else { return }
            self?.model.enable()
        }
    }
    /// Left click toggles the panel; right click opens a full context menu with
    /// the most common actions, so the status icon is usable without the popover.
    @objc private func statusItemAction(_ sender: NSStatusBarButton?) {
        if let event = NSApp.currentEvent, event.type == .rightMouseUp {
            statusMenu(for: sender).popUp(positioning: nil, at: .zero, in: sender)
        } else {
            togglePanel()
        }
    }

    private func statusMenu(for button: NSView?) -> NSMenu {
        let menu = NSMenu()
        let open = NSMenuItem(title: L("menu.openPanel"), action: #selector(togglePanel), keyEquivalent: "")
        open.target = self
        menu.addItem(open)
        menu.addItem(.separator())
        let onOff = NSMenuItem(
            title: model.running ? L("power.off") : L("power.on"),
            action: #selector(powerToggled),
            keyEquivalent: ""
        )
        onOff.target = self
        menu.addItem(onOff)
        let setup = NSMenuItem(title: L("menu.setup"), action: #selector(runSetup), keyEquivalent: "")
        setup.target = self
        menu.addItem(setup)
        let log = NSMenuItem(title: L("log"), action: #selector(openLog), keyEquivalent: "")
        log.target = self
        menu.addItem(log)
        menu.addItem(.separator())
        let check = NSMenuItem(title: L("update.check"), action: #selector(checkForUpdates), keyEquivalent: "")
        check.target = self
        menu.addItem(check)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: L("quit.title"), action: #selector(quitOnlyUI), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        return menu
    }
    @objc private func powerToggled() { model.togglePower() }
    @objc private func openLog() { model.showLog() }
    @objc private func checkForUpdates() { model.checkForUpdates(manual: true) }

    @objc private func togglePanel() {
        if popover.isShown { popover.performClose(nil) }
        else if let button = item.button {
            NSApp.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        }
    }
    @objc private func quitOnlyUI() { model.quitUI() }
    private func updateIcon() {
        let image = NSImage(systemSymbolName: model.statusSymbol, accessibilityDescription: model.statusText)
            ?? NSImage(systemSymbolName: "waveform", accessibilityDescription: model.statusText)
        image?.isTemplate = true
        item.button?.image = image
        item.button?.toolTip = "Speech Runtime \u{2014} " + model.statusText
    }
    func popoverDidShow(_ notification: Notification) { model.visibilityChanged(true) }
    func popoverDidClose(_ notification: Notification) { model.visibilityChanged(false) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !popover.isShown { togglePanel() }
        return true
    }
    // No termination handler stops the LaunchAgent. UI lifetime is intentionally independent.
}
#else
import Foundation
@main struct UnsupportedPlatform {
    static func main() { print("Speech Runtime UI requires macOS 15 or later on Apple Silicon.") }
}
#endif
