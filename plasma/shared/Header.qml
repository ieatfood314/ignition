import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "Style.js" as Style

// The accent dot and dot-matrix title every widget starts with, plus a note on the right. The dot pulses
// while `live`, and the note reads NO SIGNAL when the server can't be reached.
RowLayout {
    id: header

    property string title
    property string note
    property bool online: true
    property bool live: false
    property color accent: Style.red
    property real size: Kirigami.Units.gridUnit * 0.75

    spacing: size * 0.6

    Rectangle {
        id: dot
        Layout.alignment: Qt.AlignVCenter
        implicitWidth: header.size * 0.45
        implicitHeight: implicitWidth
        radius: width / 2
        color: header.online ? header.accent : Style.alpha(Kirigami.Theme.textColor, Style.dim)
        opacity: 1

        SequentialAnimation on opacity {
            running: header.live && header.online
            loops: Animation.Infinite
            onStopped: dot.opacity = 1
            NumberAnimation { to: 0.2; duration: 1000; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1; duration: 1000; easing.type: Easing.InOutSine }
        }
    }
    Text {
        text: header.title
        color: Kirigami.Theme.textColor
        font.family: Style.dotCaps
        font.pixelSize: header.size
    }
    Item { Layout.fillWidth: true }
    Text {
        text: header.online ? header.note : "NO SIGNAL"
        color: Style.alpha(Kirigami.Theme.textColor, Style.secondary)
        font.family: Style.mono
        font.pixelSize: header.size * 0.75
    }
}
