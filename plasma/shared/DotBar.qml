import QtQuick
import org.kde.kirigami as Kirigami
import "Style.js" as Style

// A dot-matrix progress bar that fills its width with as many dots as fit.
Item {
    id: bar

    property real fraction: 0
    property int rows: 2
    property real dotSize: 3
    property real pitch: dotSize * 1.6
    property color litColor: Kirigami.Theme.textColor
    property color dimColor: Style.alpha(Kirigami.Theme.textColor, Style.dim)

    readonly property int count: Math.max(2, Math.floor((width - dotSize) / pitch) + 1)
    readonly property real step: (width - dotSize) / (count - 1)
    readonly property int lit: Math.floor(Math.max(0, Math.min(1, fraction)) * count)

    implicitHeight: rows * dotSize + (rows - 1) * (pitch - dotSize)

    Repeater {
        model: bar.count * bar.rows
        Rectangle {
            readonly property int col: index % bar.count
            width: bar.dotSize
            height: bar.dotSize
            radius: width / 2
            x: col * bar.step
            y: Math.floor(index / bar.count) * bar.pitch
            color: col < bar.lit ? bar.litColor : bar.dimColor
            Behavior on color {
                ColorAnimation { duration: 400 }
            }
        }
    }
}
