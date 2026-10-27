import QtQuick
import org.kde.kirigami as Kirigami
import "Style.js" as Style

// An outlined, all-caps mono button in the theme's text colour, or another `ink` such as the accent. With
// holdMs set it only fires after being held that long, filling up while it's held (for ending a long burn
// early).
MouseArea {
    id: pill

    property string text
    property int holdMs: 0
    property real fontSize: Kirigami.Units.gridUnit * 0.55
    property color ink: Kirigami.Theme.textColor
    property real progress: 0

    signal activated()

    implicitWidth: label.implicitWidth + height * 1.1
    implicitHeight: Math.round(fontSize * 2.6)
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    acceptedButtons: Qt.LeftButton

    onClicked: if (!holdMs) activated()
    onPressed: if (holdMs) hold.restart()
    onReleased: if (holdMs) cancelHold()
    onCanceled: cancelHold()
    onExited: if (pressed) cancelHold()

    function cancelHold() {
        hold.stop();
        progress = 0;
    }

    NumberAnimation {
        id: hold
        target: pill
        property: "progress"
        from: 0
        to: 1
        duration: pill.holdMs
        onFinished: if (pill.progress >= 1) {
            pill.progress = 0;
            pill.activated();
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: pill.containsMouse ? Style.alpha(pill.ink, 0.10) : "transparent"
        border.width: 1
        border.color: Style.alpha(pill.ink, pill.containsMouse ? 0.6 : 0.3)
    }
    Rectangle {
        visible: pill.progress > 0
        height: parent.height
        width: Math.max(height, parent.width * pill.progress)
        radius: height / 2
        color: Style.alpha(pill.ink, 0.35)
    }
    Text {
        id: label
        anchors.centerIn: parent
        text: pill.text.toUpperCase()
        color: pill.ink
        font.family: Style.mono
        font.pixelSize: pill.fontSize
        font.weight: Font.Medium
        font.letterSpacing: pill.fontSize * 0.08
    }
}
