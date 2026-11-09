#if os(macOS)
import SwiftUI
import RuntimeUIModel

/// Shared UI for the setup wizard and the Settings panel. A toggle installs the
/// optional weights; a green check means verified on disk, NOT active in the ASR API.
struct OptionalComponentsSection: View {
    @ObservedObject var store: OptionalComponentsStore
    @State private var pendingRemoval: OptionalComponent?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L("optional.section.title"))
                .font(.system(size: 13, weight: .semibold))
            Text(L("optional.section.description"))
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(OptionalComponent.allCases) { component in
                row(component)
            }
            Text(L("optional.section.disclaimer"))
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .confirmationDialog(L("optional.delete.confirm"), isPresented: Binding(
            get: { pendingRemoval != nil },
            set: { if !$0 { pendingRemoval = nil } }
        ), titleVisibility: .visible) {
            if let component = pendingRemoval {
                Button(L("optional.delete"), role: .destructive) {
                    store.remove(component)
                    pendingRemoval = nil
                }
            }
            Button(L("cancel"), role: .cancel) { pendingRemoval = nil }
        }
    }

    private func row(_ component: OptionalComponent) -> some View {
        let status = store.status(component)
        return VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top, spacing: 9) {
                Image(systemName: component.icon)
                    .font(.system(size: 14))
                    .foregroundStyle(Color.accentColor)
                    .frame(width: 22, height: 25)
                VStack(alignment: .leading, spacing: 3) {
                    Text(L(component.titleKey)).font(.system(size: 12, weight: .medium))
                    Text(L(component.detailKey)).font(.system(size: 10))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 3)
                controls(component, status: status)
            }
            HStack(spacing: 6) {
                if let asset = component.asset {
                    Text(ByteCountFormatter.string(fromByteCount: asset.bytes, countStyle: .file))
                } else {
                    Text(L(component.sizeKey))
                }
                Text("·")
                switch status {
                case .unavailable: Text(L("optional.status.planned"))
                case .checking: Text(L("optional.status.verifying"))
                case .absent: Text(L("optional.status.notInstalled"))
                case .downloading(let fraction):
                    Text(String(format: L("optional.status.download"), fraction * 100))
                case .installed: Text(L("optional.status.installed"))
                case .failed: Text(L("optional.status.error"))
                }
            }
            .font(.system(size: 10)).foregroundStyle(.secondary)
            if case .downloading(let fraction) = status {
                ProgressView(value: fraction).controlSize(.small)
            }
            if case .failed(let message) = status {
                Text(message).font(.system(size: 10)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func controls(_ component: OptionalComponent, status: OptionalComponentsStore.Status) -> some View {
        switch status {
        case .unavailable:
            Image(systemName: "lock.circle").foregroundStyle(.secondary)
        case .checking:
            ProgressView().controlSize(.mini)
        case .downloading:
            Button { store.cancel(component) } label: {
                Image(systemName: "xmark.circle")
            }
            .help(L("cancel"))
            .buttonStyle(.borderless)
        case .installed:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                .accessibilityLabel(L("optional.status.installed"))
            Button { pendingRemoval = component } label: {
                Image(systemName: "xmark.circle")
            }
            .help(L("optional.delete"))
            .buttonStyle(.borderless)
        case .absent, .failed:
            Toggle(L("optional.install"), isOn: Binding(
                get: { false },
                set: { if $0 { store.install(component) } }
            ))
            .labelsHidden().toggleStyle(.switch).controlSize(.mini)
            .help(L("optional.install"))
        }
    }
}
#endif
