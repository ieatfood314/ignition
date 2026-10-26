import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import "Style.js" as Style
import "Fmt.js" as Fmt

// A labelled minutes value with − and +, which the scroll wheel also turns.
ColumnLayout {
    id: stepper

    property string label
    property int value
    property int from: 0
    property int to: 720
    property int step: 5
    property real u: 1

    signal moved(int value)

    function nudge(steps) {
        const v = Math.max(from, Math.min(to, Math.round((value + steps * step) / step) * step));
        if (v !== value)
            moved(v);
    }

    spacing: 2 * u

    Text {
        Layout.alignment: Qt.AlignHCenter
        text: stepper.label
        color: Style.alpha(Kirigami.Theme.textColor, Style.secondary)
        font.family: Style.mono
        font.pixelSize: 8.5 * stepper.u
        font.letterSpacing: 1 * stepper.u
    }
    RowLayout {
        Layout.alignment: Qt.AlignHCenter
        spacing: 6 * stepper.u
        Step {
            glyph: "−"
            u: stepper.u
            onClicked: stepper.nudge(-1)
        }
        Text {
            Layout.minimumWidth: 58 * stepper.u
            horizontalAlignment: Text.AlignHCenter
            text: stepper.value ? Fmt.estimate(stepper.value) : "0M"
            color: Kirigami.Theme.textColor
            font.family: Style.dot
            font.pixelSize: 19 * stepper.u
        }
        Step {
            glyph: "+"
            u: stepper.u
            onClicked: stepper.nudge(1)
        }
    }

    component Step: MouseArea {
        id: btn
        property string glyph
        property real u
        Layout.preferredWidth: 22 * u
        Layout.preferredHeight: 22 * u
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: btn.containsMouse ? Style.alpha(Kirigami.Theme.textColor, 0.1) : "transparent"
            border.width: 1
            border.color: Style.alpha(Kirigami.Theme.textColor, btn.containsMouse ? 0.6 : 0.3)
        }
        Text {
            anchors.centerIn: parent
            text: btn.glyph
            color: Kirigami.Theme.textColor
            font.family: Style.mono
            font.pixelSize: 12 * btn.u
        }
    }

    WheelHandler {
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (e) => stepper.nudge(e.angleDelta.y > 0 ? 1 : e.angleDelta.y < 0 ? -1 : 0)
    }
}
