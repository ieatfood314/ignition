import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    property alias cfg_serverUrl: url.text
    property string cfg_serverUrlDefault

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
    }
}
