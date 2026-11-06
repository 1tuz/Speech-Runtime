import AppKit
import Foundation

// Original vector icon using a system symbol; no downloaded assets or font files.
guard CommandLine.arguments.count == 2 else { fatalError("usage: make-icon.swift AppIcon.icns") }
let output = URL(fileURLWithPath: CommandLine.arguments[1])
let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".iconset")
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: directory) }
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let side = CGFloat(size * scale)
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(side), pixelsHigh: Int(side),
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let rect = NSRect(x: side * 0.08, y: side * 0.08, width: side * 0.84, height: side * 0.84)
        let shape = NSBezierPath(roundedRect: rect, xRadius: side * 0.19, yRadius: side * 0.19)
        let gradient = NSGradient(colors: [NSColor(calibratedRed: 0.10, green: 0.26, blue: 0.32, alpha: 1),
                                           NSColor(calibratedRed: 0.05, green: 0.10, blue: 0.15, alpha: 1)])!
        gradient.draw(in: shape, angle: 65)
        let configuration = NSImage.SymbolConfiguration(pointSize: side * 0.45, weight: .medium)
            .applying(.init(paletteColors: [.white]))
        if let symbol = NSImage(systemSymbolName: "waveform", accessibilityDescription: nil)?.withSymbolConfiguration(configuration) {
            symbol.draw(in: NSRect(x: side * 0.22, y: side * 0.25, width: side * 0.56, height: side * 0.50))
        }
        NSGraphicsContext.restoreGraphicsState()
        let filename = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(filename))
    }
}
let task = Process()
task.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
task.arguments = ["-c", "icns", directory.path, "-o", output.path]
try task.run(); task.waitUntilExit()
guard task.terminationStatus == 0 else { fatalError("iconutil failed") }
