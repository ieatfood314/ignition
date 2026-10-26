import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "Style.js" as Style
import "Fmt.js" as Fmt

// Time burned today and this week against the goals in Settings, and the last seven days as dot columns.
// A goal you've hit turns red.
PlasmoidItem {
    id: root

    readonly property var st: client.stats
    readonly property int daily: st && st.goals ? st.goals.daily : 0
    readonly property int weekly: st && st.goals ? st.goals.weekly : 0
    readonly property bool inPanel: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        || Plasmoid.formFactor === PlasmaCore.Types.Horizontal

    Plasmoid.icon: "ca.heeney.ignition"
    Plasmoid.backgroundHints: PlasmaCore.Types.ShadowBackground | PlasmaCore.Types.ConfigurableBackground
    preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation

    toolTipMainText: "Burn goals"
    toolTipSubText: !client.online ? `Can't reach ${client.host}` : !st ? ""
        : `Today ${Fmt.duration(st.today).toLowerCase()}${daily ? " of " + Fmt.estimate(daily).toLowerCase() : ""}`
            + ` · this week ${Fmt.duration(st.week).toLowerCase()}${weekly ? " of " + Fmt.estimate(weekly).toLowerCase() : ""}`

    Client {
        id: client
        url: Plasmoid.configuration.serverUrl
    }

    // In a panel: today's goal as a ring, minutes burned in the middle.
    compactRepresentation: MouseArea {
        id: compact
        readonly property real side: Math.min(width, height)
        readonly property bool met: root.daily > 0 && root.st && root.st.today >= root.daily * 60
        Layout.minimumWidth: Plasmoid.formFactor === PlasmaCore.Types.Horizontal ? height : Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Plasmoid.formFactor === PlasmaCore.Types.Vertical ? width : Kirigami.Units.iconSizes.small
        onClicked: root.expanded = !root.expanded

        DotRing {
            anchors.centerIn: parent
            width: compact.side * 0.92
            height: width
            count: 24
            dotSize: Math.max(2, width / 13)
            fraction: root.st && root.daily ? root.st.today / (root.daily * 60) : 0
            litColor: compact.met ? Style.red : Kirigami.Theme.textColor
        }
        Text {
            anchors.centerIn: parent
            readonly property int mins: root.st ? Math.floor(root.st.today / 60) : 0
            text: !root.st ? "–" : mins < 100 ? mins : Math.floor(mins / 60) + "H"
            color: compact.met ? Style.red : Kirigami.Theme.textColor
            font.family: Style.dot
            font.pixelSize: compact.side * (text.length > 2 ? 0.27 : 0.36)
        }
    }

    fullRepresentation: Item {
        id: full

        // Drawn for a 300 x 220 widget and scaled to fit.
        readonly property real u: Math.max(0.6, Math.min(width / 300, height / 220))
        readonly property color ink: Kirigami.Theme.textColor
        readonly property color muted: Style.alpha(ink, Style.secondary)

        Layout.minimumWidth: 200
        Layout.minimumHeight: 150
        Layout.preferredWidth: Kirigami.Units.gridUnit * 16
        Layout.preferredHeight: Kirigami.Units.gridUnit * 12

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14 * full.u
            spacing: 12 * full.u

            Header {
                Layout.fillWidth: true
                title: "BURN GOALS"
                size: 13 * full.u
                online: client.online
                note: root.daily || root.weekly ? "" : "SET GOALS ›"
                TapHandler {
                    onTapped: Qt.openUrlExternally(client.url)
                }
            }

            Repeater {
                model: [
                    { label: "TODAY", sec: root.st ? root.st.today : 0, goal: root.daily },
                    { label: "WEEK", sec: root.st ? root.st.week : 0, goal: root.weekly },
                ]
                ColumnLayout {
                    id: goal
                    required property var modelData
                    readonly property bool met: modelData.goal > 0 && modelData.sec >= modelData.goal * 60
                    Layout.fillWidth: true
                    visible: !!root.st
                    spacing: 5 * full.u
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: goal.modelData.label
                            color: full.muted
                            font.family: Style.mono
                            font.pixelSize: 9 * full.u
                            font.letterSpacing: 1 * full.u
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: Fmt.duration(goal.modelData.sec) + (goal.modelData.goal ? "/" + Fmt.estimate(goal.modelData.goal) : "")
                            color: goal.met ? Style.red : full.ink
                            font.family: Style.dot
                            font.pixelSize: 15 * full.u
                        }
                    }
                    DotBar {
                        Layout.fillWidth: true
                        visible: goal.modelData.goal > 0
                        rows: 2
                        dotSize: 3.2 * full.u
                        fraction: goal.modelData.goal ? goal.modelData.sec / (goal.modelData.goal * 60) : 0
                        litColor: goal.met ? Style.red : full.ink
                    }
                }
            }

            // The last seven days, a column of dots each, filled against the daily goal (or the best day).
            Item {
                id: week
                readonly property var days: root.st ? root.st.days || [] : []
                readonly property real scale: root.daily > 0 ? root.daily * 60
                    : Math.max(3600, ...days.map((d) => d.seconds))
                readonly property int tall: Math.max(3, Math.floor((height - 14 * full.u) / (7 * full.u)))
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 40 * full.u

                Row {
                    anchors.fill: parent
                    Repeater {
                        model: week.days
                        Item {
                            id: day
                            required property var modelData
                            required property int index
                            readonly property bool today: index === week.days.length - 1
                            readonly property bool met: root.daily > 0 && modelData.seconds >= root.daily * 60
                            readonly property int lit: Math.min(week.tall, Math.ceil(modelData.seconds / week.scale * week.tall - 0.001))
                            width: week.width / 7
                            height: week.height

                            Column {
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: label.top
                                anchors.bottomMargin: 5 * full.u
                                spacing: 3 * full.u
                                Repeater {
                                    model: week.tall
                                    Rectangle {
                                        required property int index
                                        // Index 0 is the top dot; light from the bottom up.
                                        readonly property bool on: week.tall - index <= day.lit
                                        width: 4 * full.u
                                        height: width
                                        radius: width / 2
                                        color: !on ? Style.alpha(full.ink, Style.dim) : day.met ? Style.red : full.ink
                                    }
                                }
                            }
                            Text {
                                id: label
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: parent.bottom
                                text: Qt.formatDate(Fmt.parseDate(day.modelData.date), "ddd").charAt(0).toUpperCase()
                                color: day.today ? full.ink : full.muted
                                font.family: Style.mono
                                font.weight: day.today ? Font.Bold : Font.Normal
                                font.pixelSize: 8.5 * full.u
                            }
                        }
                    }
                }
            }
        }
    }
}
