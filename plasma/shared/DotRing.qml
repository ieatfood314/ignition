import QtQuick
import org.kde.kirigami as Kirigami
import "Style.js" as Style

// A ring of dots, lit clockwise from 12 o'clock for `fraction` of the way round. The last lit dot is the
// ember: it can glow red while the rest stay in the theme's colour.
Item {
    id: ring

    property int count: 60
    property real fraction: 0
    property real dotSize: Math.max(2, width / 40)
    property color litColor: Kirigami.Theme.textColor
    property color emberColor: litColor
    property real dimAlpha: Style.dim

    readonly property int lit: Math.max(0, Math.min(count, Math.ceil(fraction * count - 0.001)))

    Repeater {
        model: ring.count
        Rectangle {
            readonly property real angle: -Math.PI / 2 + 2 * Math.PI * index / ring.count
            readonly property real r: Math.min(ring.width, ring.height) / 2 - ring.dotSize / 2
            readonly property bool on: index < ring.lit
            width: ring.dotSize
            height: ring.dotSize
            radius: width / 2
            x: ring.width / 2 + r * Math.cos(angle) - width / 2
            y: ring.height / 2 + r * Math.sin(angle) - height / 2
            color: !on ? Style.alpha(ring.litColor, ring.dimAlpha) : index === ring.lit - 1 ? ring.emberColor : ring.litColor
            Behavior on color {
                ColorAnimation { duration: 400 }
            }
        }
    }
}
