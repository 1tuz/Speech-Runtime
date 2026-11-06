# UI implementation sources and component choice

Reviewed 2026-11-06. User runtime basis:
https://github.com/1tuz/Speech-Runtime/tree/7c689817cdb79ea48821bf32f9acbd3a55031f8b/runtime

## Used: system APIs, no extra UI framework

- SwiftUI Liquid Glass: https://developer.apple.com/documentation/swiftui/applying-liquid-glass-to-custom-views
- Platform adoption and accessibility: https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass
- `glassEffect`: https://developer.apple.com/documentation/swiftui/view/glasseffect(_:in:)
- AppKit NSPopover: https://developer.apple.com/documentation/appkit/nspopover
- AppKit NSStatusItem: https://developer.apple.com/documentation/appkit/nsstatusitem
- Hosting sizing: https://developer.apple.com/documentation/swiftui/nshostingcontroller/sizingoptions
- Login status/registration: https://developer.apple.com/documentation/servicemanagement/smappservice
- Main-app login registration explanation: https://developer.apple.com/forums/thread/777520
- Agent registration semantics: https://developer.apple.com/documentation/servicemanagement/smappservice/register()
- XNU memory/CPU structures: https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/resource.h
- wavo preparation API: https://github.com/vladkens/wavo/blob/main/src/lib.rs

The standard AppKit popover supplies anchoring, keyboard/window behavior and outside-click dismissal.
SwiftUI supplies controls, appearance and the primary button's actual system glass material. AppKit's
popover delegate gives an explicit visibility lifecycle, so a retained SwiftUI view cannot accidentally
keep a high-frequency resource timer running while hidden.

## Examined, not introduced as dependencies

**MenuBarKit**
https://github.com/gabriarceus/MenuBarKit
MIT; reusable borderless panel, Liquid Glass chrome and meter components. It fits this task, but its
own README distinguishes tested runtime from older-system fallback. This patch uses system popovers
instead of adding a dependency just for window placement and design tokens. No package code is copied.

**MenuBarExtraAccess**
https://github.com/orchetect/MenuBarExtraAccess
Useful when staying entirely within MenuBarExtra and needing presentation bindings. Not needed with
the small AppKit delegate used here; no private SwiftUI introspection is needed.

**LaunchAtLogin-Modern / SMAppService**
https://github.com/sindresorhus/LaunchAtLogin-Modern
The convenience wrapper is unnecessary at the macOS 15 deployment floor: the patch directly uses
`SMAppService.mainApp` and exposes approval/error states rather than persisting an independent bool.

**Keepresso**
https://github.com/gyorgysh/keepresso
Visual reference only: compact native menu panel, one dominant action, quiet status/controls.
The repository states GPLv3; this patch does not copy its implementation or assets. The UI and
symbol-based icon generator here are new code and use the project's existing license policy.

## Model provenance

The downloaded GGUF is pinned, not selected by an unversioned latest-model search:
https://huggingface.co/handy-computer/parakeet-ultra-gguf/tree/a1539be1b849ea7463b88343e5277b373d0c4ea9

Source model:
https://huggingface.co/moondream/parakeet-ultra

Model weights are not embedded in the patch or application bundle. Preserve model attribution and
review the original model/conversion terms before redistributing weight files. UI/library licensing
does not determine the weight license.
