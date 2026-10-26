import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "Style.js" as Style

// The panel icon: the flame when idle, otherwise a ring of dots with the minutes left in the middle.
MouseArea {
    id: compact

    required property var app
    readonly property string phase: app.phase
    readonly property int mins: Math.ceil(app.remaining / 60)
    readonly property real side: Math.min(width, height)

    Layout.minimumWidth: Plasmoid.formFactor === PlasmaCore.Types.Horizontal ? height : Kirigami.Units.iconSizes.small
    Layout.minimumHeight: Plasmoid.formFactor === PlasmaCore.Types.Vertical ? width : Kirigami.Units.iconSizes.small

    hoverEnabled: true
    onClicked: app.expanded = !app.expanded

    Flame {
        anchors.centerIn: parent
        height: compact.side * 0.7
        width: height
        visible: compact.phase === "idle" && !compact.app.arming
        color: compact.app.client.online ? Style.red : Style.alpha(Kirigami.Theme.textColor, Style.secondary)
        opacity: compact.containsMouse ? 1 : 0.9
    }

    DotRing {
        anchors.centerIn: parent
        width: compact.side * 0.92
        height: width
        visible: !(compact.phase === "idle" && !compact.app.arming)
        count: 24
        dotSize: Math.max(2, width / 13)
        fraction: compact.app.arming ? (4 - compact.app.arming) / 3 : compact.app.fraction
        litColor: compact.app.arming ? Style.red
            : compact.phase === "countdown" ? Style.alpha(Kirigami.Theme.textColor, Style.secondary)
            : Kirigami.Theme.textColor
        emberColor: Style.red
    }

    Text {
        anchors.centerIn: parent
        visible: compact.phase !== "idle" || compact.app.arming
        text: compact.app.arming ? compact.app.arming
            : compact.phase === "countdown" ? "T" + compact.mins
            : compact.mins < 100 ? compact.mins : Math.floor(compact.mins / 60) + "H"
        color: compact.phase === "countdown" || compact.app.arming ? Style.red : Kirigami.Theme.textColor
        font.family: Style.dot
        font.pixelSize: compact.side * (text.length > 2 ? 0.27 : 0.36)
    }
}
