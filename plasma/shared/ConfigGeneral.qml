import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.kquickcontrols as KQuickControls
import "Style.js" as Style

KCM.SimpleKCM {
    id: page

    property alias cfg_serverUrl: url.text
    property string cfg_serverUrlDefault
    property string cfg_accent
    property string cfg_accentDefault
    property color cfg_accentColor
    property color cfg_accentColorDefault

    Kirigami.FormLayout {
        QQC2.TextField {
            id: url
            Kirigami.FormData.label: "Ignition server:"
            placeholderText: "http://127.0.0.1:4580"
            Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        }
        QQC2.Label {
            text: "The address you open ignition at, e.g. http://desk:8445 for one on another computer."
            wrapMode: Text.Wrap
            opacity: 0.7
            Layout.maximumWidth: Kirigami.Units.gridUnit * 20
        }

        Item {
            Kirigami.FormData.isSection: true
        }

        QQC2.ButtonGroup {
            id: accents
        }
        RowLayout {
            Kirigami.FormData.label: "Accent colour:"
            QQC2.RadioButton {
                text: "Nothing red"
                QQC2.ButtonGroup.group: accents
                checked: page.cfg_accent !== "system" && page.cfg_accent !== "custom"
                onToggled: if (checked) page.cfg_accent = "red"
            }
            Swatch {
                color: Style.red
            }
        }
        RowLayout {
            QQC2.RadioButton {
                text: "System accent"
                QQC2.ButtonGroup.group: accents
                checked: page.cfg_accent === "system"
                onToggled: if (checked) page.cfg_accent = "system"
            }
            Swatch {
                color: Kirigami.Theme.focusColor
            }
        }
        RowLayout {
            QQC2.RadioButton {
                id: custom
                text: "Custom:"
                QQC2.ButtonGroup.group: accents
                checked: page.cfg_accent === "custom"
                onToggled: if (checked) page.cfg_accent = "custom"
            }
            KQuickControls.ColorButton {
                color: page.cfg_accentColor
                dialogTitle: "Accent colour"
                showAlphaChannel: false
                onAccepted: (c) => {
                    page.cfg_accentColor = c;
                    page.cfg_accent = "custom";
                }
            }
        }
    }

    component Swatch: Rectangle {
        implicitWidth: Kirigami.Units.gridUnit
        implicitHeight: implicitWidth
        radius: width / 2
    }
}
