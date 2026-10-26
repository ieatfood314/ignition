import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami
import "Style.js" as Style
import "Fmt.js" as Fmt

// The dial and its controls: the Ignite page of the website, in dot-matrix.
Item {
    id: full

    required property var app
    readonly property var client: app.client
    readonly property string phase: app.phase
    readonly property var session: app.session
    readonly property var ig: client.ignite || {}
    readonly property bool idle: phase === "idle" && !app.arming
    // A T-minus can be scrubbed until an hour before liftoff, or for a minute after it was set.
    readonly property real scrubLeft: phase === "countdown" ? (ig.scrub_until ?? Infinity) - client.now : 0

    // Everything is drawn for a 300 x 420 widget and scaled to fit.
    readonly property real u: Math.max(0.6, Math.min(width / 300, height / 420))
    readonly property color ink: Kirigami.Theme.textColor
    readonly property color muted: Style.alpha(ink, Style.secondary)

    Layout.minimumWidth: 220
    Layout.minimumHeight: 300
    Layout.preferredWidth: Kirigami.Units.gridUnit * 16
    Layout.preferredHeight: Kirigami.Units.gridUnit * 22

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14 * full.u
        spacing: 12 * full.u

        Header {
            Layout.fillWidth: true
            title: "IGNITION"
            size: 13 * full.u
            online: full.client.online
            live: full.phase === "burning"
            note: full.app.arming ? "ARMING"
                : full.phase === "burning" ? "BURNING"
                : full.phase === "countdown" ? "T-MINUS"
                : "STANDBY"
            TapHandler {
                onTapped: Qt.openUrlExternally(full.client.url)
            }
        }

        // ---- the dial
        Item {
            id: dial
            readonly property real size: Math.min(width, 230 * full.u)
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120 * full.u

            DotRing {
                id: ring
                anchors.centerIn: parent
                width: Math.min(dial.width, dial.height)
                height: width
                count: 60
                dotSize: Math.max(2, width / 50)
                fraction: full.app.arming ? (4 - full.app.arming) / 3 : full.phase === "idle" ? 0 : full.app.fraction
                litColor: full.app.arming ? Style.red : full.phase === "countdown" ? full.muted : full.ink
                emberColor: Style.red
            }

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 4 * full.u

                Flame {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredHeight: ring.width * 0.2
                    Layout.preferredWidth: ring.width * 0.2
                    visible: full.idle
                    color: full.client.online ? Style.red : full.muted
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: full.app.arming ? full.app.arming
                        : full.phase === "burning" ? Fmt.clock(full.app.remaining)
                        : full.phase === "countdown" ? "T-" + Fmt.clock(full.app.remaining)
                        : "IGNITE"
                    color: full.app.arming ? Style.red : full.ink
                    font.family: full.idle ? Style.dotCaps : Style.dot
                    font.pixelSize: ring.width * (full.idle ? 0.13 : full.app.arming ? 0.3 : text.length > 6 ? 0.15 : 0.2)
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: full.app.arming ? "CLICK TO SCRUB"
                        : full.phase === "burning" ? "LEFT"
                        : full.phase === "countdown" ? "CLICK TO IGNITE NOW"
                        : Fmt.estimate(Plasmoid.configuration.minutes) + " BURN"
                            + (Plasmoid.configuration.delay ? " · T-" + Fmt.estimate(Plasmoid.configuration.delay) : "")
                    color: full.muted
                    font.family: Style.mono
                    font.pixelSize: 9 * full.u
                    font.letterSpacing: 1 * full.u
                }
            }

            MouseArea {
                anchors.centerIn: parent
                width: ring.width
                height: width
                enabled: full.phase !== "burning" && (full.client.online || full.phase !== "idle")
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: full.app.press()
            }
        }

        // ---- controls for the phase we're in
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 46 * full.u

            RowLayout {
                anchors.centerIn: parent
                visible: full.idle
                spacing: 18 * full.u
                Stepper {
                    label: "BURN"
                    u: full.u
                    from: 5
                    to: 720
                    value: Plasmoid.configuration.minutes
                    onMoved: (v) => Plasmoid.configuration.minutes = v
                }
                Stepper {
                    label: "T-MINUS"
                    u: full.u
                    from: 0
                    to: 1440
                    value: Plasmoid.configuration.delay
                    onMoved: (v) => Plasmoid.configuration.delay = v
                }
            }

            RowLayout {
                anchors.centerIn: parent
                visible: full.phase === "countdown"
                spacing: 8 * full.u
                Pill {
                    visible: full.scrubLeft > 0
                    text: full.scrubLeft <= 3600 ? "Scrub · " + Fmt.clock(full.scrubLeft) : "Scrub"
                    fontSize: 9 * full.u
                    onActivated: full.client.act("/api/ignite/scrub")
                }
                Pill {
                    text: "Ignite now"
                    accent: true
                    fontSize: 9 * full.u
                    onActivated: full.client.act("/api/ignite/now")
                }
            }

            RowLayout {
                anchors.centerIn: parent
                visible: full.phase === "burning"
                spacing: 6 * full.u
                Repeater {
                    model: [5, 15, 30]
                    Pill {
                        required property int modelData
                        text: "+" + modelData
                        fontSize: 9 * full.u
                        onActivated: full.client.act("/api/ignite/extend", { minutes: modelData })
                    }
                }
                Pill {
                    visible: !!full.ig.can_stop
                    text: "Hold to end"
                    accent: true
                    holdMs: 2000
                    fontSize: 9 * full.u
                    onActivated: full.client.act("/api/ignite/stop")
                }
            }
        }

        // ---- one line of context: errors, the lock, the next launch
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 14 * full.u
            spacing: 8 * full.u
            Text {
                Layout.fillWidth: true
                horizontalAlignment: next.visible ? Text.AlignLeft : Text.AlignHCenter
                elide: Text.ElideRight
                readonly property var ns: full.ig.next_schedule
                text: full.app.message ? full.app.message
                    : !full.client.online ? "NO SIGNAL FROM " + full.client.host.toUpperCase()
                    : full.phase === "burning" ? (full.ig.can_stop ? "LOCKED" : "NO WAY OUT") + " UNTIL " + Fmt.time(full.session.ends_at)
                    : full.phase === "countdown" ? (full.scrubLeft > 0 ? "" : "COMMITTED · ")
                        + Fmt.estimate(full.session.minutes) + " BURN AT " + Fmt.time(full.session.starts_at)
                    : ns ? "LAUNCH " + Fmt.when(ns.at) + " · " + Fmt.estimate(ns.minutes)
                    : ""
                color: full.app.message ? Style.red : full.muted
                font.family: Style.mono
                font.pixelSize: 9 * full.u
                font.letterSpacing: 0.5 * full.u
            }
            Pill {
                id: next
                visible: full.idle && !full.app.message && full.client.online && !!full.ig.next_schedule
                text: "Skip"
                fontSize: 8 * full.u
                onActivated: full.client.act("/api/ignite/skip",
                    { schedule_id: full.ig.next_schedule.schedule_id, date: full.ig.next_schedule.date })
            }
        }

        // ---- today against the daily goal
        ColumnLayout {
            id: today
            readonly property var st: full.client.stats
            readonly property int goal: st && st.goals ? st.goals.daily : 0
            readonly property bool met: goal > 0 && st.today >= goal * 60
            Layout.fillWidth: true
            visible: !!st
            spacing: 5 * full.u
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "TODAY"
                    color: full.muted
                    font.family: Style.mono
                    font.pixelSize: 9 * full.u
                    font.letterSpacing: 1 * full.u
                }
                Item { Layout.fillWidth: true }
                Text {
                    text: !today.st ? "" : today.goal ? Fmt.duration(today.st.today) + "/" + Fmt.estimate(today.goal)
                        : Fmt.duration(today.st.today) + "  ·  WEEK " + Fmt.duration(today.st.week)
                    color: today.met ? Style.red : full.ink
                    font.family: Style.dot
                    font.pixelSize: 12 * full.u
                }
            }
            DotBar {
                Layout.fillWidth: true
                visible: today.goal > 0
                rows: 2
                dotSize: 3 * full.u
                fraction: today.goal ? today.st.today / (today.goal * 60) : 0
                litColor: today.met ? Style.red : full.ink
            }
        }

        // ---- the blockers on each computer
        Flow {
            Layout.fillWidth: true
            visible: (full.ig.enforcers || []).length > 0
            spacing: 12 * full.u
            Repeater {
                model: full.ig.enforcers || []
                Row {
                    id: enf
                    required property var modelData
                    readonly property bool stale: modelData.age > 30
                    readonly property bool armed: !stale && !!modelData.status && !!modelData.status.armed
                    spacing: 5 * full.u
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 5 * full.u
                        height: width
                        radius: width / 2
                        color: enf.armed ? Style.red : enf.stale ? "transparent" : full.muted
                        border.width: enf.stale ? 1 : 0
                        border.color: full.muted
                    }
                    Text {
                        text: enf.modelData.host.toUpperCase() + " "
                            + (enf.stale ? "OFFLINE" : enf.armed ? "LOCKED" : full.phase === "burning" ? "ARMING" : "READY")
                        color: enf.stale ? Style.alpha(full.ink, Style.dim * 2) : full.muted
                        font.family: Style.mono
                        font.pixelSize: 8.5 * full.u
                        font.letterSpacing: 0.5 * full.u
                    }
                }
            }
        }
    }
}
