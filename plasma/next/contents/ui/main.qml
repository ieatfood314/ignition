import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami as Kirigami
import "Style.js" as Style
import "Fmt.js" as Fmt

// The top of the Need list, in the website's Up next order. Click the dot to check one off, or add one.
PlasmoidItem {
    id: root

    readonly property color accent: Style.accent(Plasmoid.configuration, Kirigami.Theme.focusColor)
    readonly property var open: Fmt.upNext(client.items)
    readonly property bool hot: open.some((i) => i.due && Fmt.due(i.due).hot)
    readonly property bool inPanel: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        || Plasmoid.formFactor === PlasmaCore.Types.Horizontal

    Plasmoid.icon: "ca.heeney.ignition"
    Plasmoid.backgroundHints: PlasmaCore.Types.ShadowBackground | PlasmaCore.Types.ConfigurableBackground
    preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation

    toolTipMainText: "Next up"
    toolTipSubText: !client.online ? `Can't reach ${client.host}`
        : open.length ? open.slice(0, 3).map((i) => i.title).join("\n") : "All clear"

    Client {
        id: client
        url: Plasmoid.configuration.serverUrl
        watchItems: true
        eager: root.expanded
    }

    // In a panel: how many things are left, with the accent dot when something's due.
    compactRepresentation: MouseArea {
        id: compact
        readonly property real side: Math.min(width, height)
        Layout.minimumWidth: Plasmoid.formFactor === PlasmaCore.Types.Horizontal ? height : Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Plasmoid.formFactor === PlasmaCore.Types.Vertical ? width : Kirigami.Units.iconSizes.small
        onClicked: root.expanded = !root.expanded

        Text {
            anchors.centerIn: parent
            text: client.itemsLoaded ? root.open.length : "–"
            color: Kirigami.Theme.textColor
            font.family: Style.dot
            font.pixelSize: compact.side * 0.5
        }
        Rectangle {
            visible: root.hot
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: compact.side * 0.12
            width: compact.side * 0.16
            height: width
            radius: width / 2
            color: root.accent
        }
    }

    fullRepresentation: Item {
        id: full

        // Drawn for a 300-wide widget and scaled to fit.
        readonly property real u: Math.max(0.6, Math.min(2, width / 300))
        readonly property color ink: Kirigami.Theme.textColor
        readonly property color muted: Style.alpha(ink, Style.secondary)
        readonly property real rowH: 44 * u
        readonly property int fits: Math.max(1, Math.floor((list.height + 6 * u) / rowH))
        // Ids being checked off, so they fade out before the server confirms.
        property var leaving: ({})

        Layout.minimumWidth: 200
        Layout.minimumHeight: 160
        Layout.preferredWidth: Kirigami.Units.gridUnit * 16
        Layout.preferredHeight: Kirigami.Units.gridUnit * 15

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 14 * full.u
            spacing: 14 * full.u

            Header {
                Layout.fillWidth: true
                title: "NEXT UP"
                accent: root.accent
                size: 13 * full.u
                online: client.online
                note: root.open.length > full.fits ? `+${root.open.length - full.fits}` : ""
                TapHandler {
                    onTapped: Qt.openUrlExternally(client.base() + "/#need")
                }
            }

            Item {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                ColumnLayout {
                    visible: client.itemsLoaded && root.open.length === 0
                    spacing: 4 * full.u
                    Text {
                        text: "ALL CLEAR."
                        color: full.ink
                        font.family: Style.dotCaps
                        font.pixelSize: 16 * full.u
                    }
                    Text {
                        text: "NOTHING ON THE NEED LIST."
                        color: full.muted
                        font.family: Style.mono
                        font.pixelSize: 9 * full.u
                    }
                }

                Column {
                    width: parent.width
                    Repeater {
                        model: root.open.slice(0, full.fits)
                        Item {
                            id: row
                            required property var modelData
                            readonly property bool gone: !!full.leaving[modelData.id]
                            readonly property var due: modelData.due ? Fmt.due(modelData.due) : null
                            width: parent.width
                            height: full.rowH
                            opacity: gone ? 0.25 : 1
                            Behavior on opacity {
                                NumberAnimation { duration: 300 }
                            }

                            // The check: a hollow dot that fills with the accent, then the item leaves the list.
                            MouseArea {
                                id: check
                                x: 0
                                y: 2 * full.u
                                width: 16 * full.u
                                height: width
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                enabled: !row.gone
                                onClicked: {
                                    const id = row.modelData.id;
                                    full.leaving = Object.assign({}, full.leaving, { [id]: true });
                                    client.patchItem(id, { done: true }, (ok) => {
                                        if (!ok) {
                                            const left = Object.assign({}, full.leaving);
                                            delete left[id];
                                            full.leaving = left;
                                        }
                                    });
                                }
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 11 * full.u
                                    height: width
                                    radius: width / 2
                                    color: row.gone ? root.accent : check.containsMouse ? Style.alpha(root.accent, 0.35) : "transparent"
                                    border.width: Math.max(1, 1.2 * full.u)
                                    border.color: row.gone || check.containsMouse ? root.accent : full.muted
                                }
                            }

                            Column {
                                x: 24 * full.u
                                width: parent.width - x
                                spacing: 4 * full.u
                                Text {
                                    width: parent.width
                                    text: row.modelData.title
                                    elide: Text.ElideRight
                                    color: full.ink
                                    font.family: Style.mono
                                    font.weight: Font.Medium
                                    font.pixelSize: 11 * full.u
                                    font.strikeout: row.gone
                                }
                                Row {
                                    spacing: 8 * full.u
                                    // Priority as one to three accent ticks.
                                    Row {
                                        visible: row.modelData.priority > 0
                                        anchors.verticalCenter: parent.verticalCenter
                                        spacing: 1.5 * full.u
                                        Repeater {
                                            model: 3
                                            Rectangle {
                                                required property int index
                                                width: 1.8 * full.u
                                                height: 7 * full.u
                                                color: index < row.modelData.priority ? root.accent : Style.alpha(full.ink, Style.dim)
                                            }
                                        }
                                    }
                                    Text {
                                        visible: row.modelData.estimate_min != null
                                        text: Fmt.estimate(row.modelData.estimate_min)
                                        color: full.muted
                                        font.family: Style.mono
                                        font.pixelSize: 8.5 * full.u
                                    }
                                    Text {
                                        visible: !!row.due
                                        text: row.due ? row.due.text : ""
                                        color: row.due && row.due.hot ? root.accent : full.muted
                                        font.family: Style.mono
                                        font.pixelSize: 8.5 * full.u
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Adding: a + that turns into a one-line field. Enter adds to the Need list, Esc gives up.
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 22 * full.u
                visible: client.online

                Pill {
                    visible: !field.visible
                    anchors.verticalCenter: parent.verticalCenter
                    text: "+ Add"
                    fontSize: 8.5 * full.u
                    onActivated: {
                        field.visible = true;
                        input.forceActiveFocus();
                    }
                }
                Item {
                    id: field
                    visible: false
                    anchors.fill: parent
                    TextInput {
                        id: input
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        color: full.ink
                        selectionColor: root.accent
                        font.family: Style.mono
                        font.pixelSize: 11 * full.u
                        clip: true
                        onAccepted: {
                            const title = text.trim();
                            if (title)
                                client.addItem({ title, list: "need" });
                            text = "";
                            field.visible = false;
                        }
                        Keys.onEscapePressed: {
                            text = "";
                            field.visible = false;
                        }
                        onActiveFocusChanged: if (!activeFocus && !text)
                            field.visible = false
                        Text {
                            visible: !input.text
                            text: "WHAT NEEDS DOING?"
                            color: Style.alpha(full.ink, Style.dim * 2)
                            font: input.font
                        }
                    }
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        height: 1
                        color: Style.alpha(full.ink, 0.3)
                    }
                }
            }
        }

        Connections {
            target: client
            // Forget checked-off ids once the server's list no longer has them.
            function onItemsChanged() {
                const ids = new Set(root.open.map((i) => i.id));
                const left = {};
                for (const id in full.leaving)
                    if (ids.has(id))
                        left[id] = true;
                full.leaving = left;
            }
        }
    }
}
