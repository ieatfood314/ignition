import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "Style.js" as Style
import "Fmt.js" as Fmt

// The Ignite button, as a widget. In a panel it's the flame, or a ring of dots burning down with the
// minutes left; click it (or put it on the desktop) for the whole dial and its controls.
PlasmoidItem {
    id: root

    readonly property alias client: client
    readonly property bool inPanel: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        || Plasmoid.formFactor === PlasmaCore.Types.Horizontal

    readonly property string phase: client.phase
    readonly property var session: client.session
    readonly property real remaining: !session ? 0
        : phase === "burning" ? session.ends_at - client.now : session.starts_at - client.now
    // Remaining burn as a fraction, or how far a T-minus has to go.
    readonly property real fraction: !session ? 0
        : phase === "burning" ? remaining / Math.max(1, session.ends_at - session.starts_at)
        : Math.min(1, remaining / Math.max(1, session.starts_at - session.created_at))

    // 3, 2, 1 before ignition; click again to scrub. 0 when not arming.
    property int arming: 0
    property string message

    Plasmoid.icon: "ca.heeney.ignition"
    Plasmoid.backgroundHints: PlasmaCore.Types.ShadowBackground | PlasmaCore.Types.ConfigurableBackground
    preferredRepresentation: inPanel ? compactRepresentation : fullRepresentation

    toolTipMainText: !client.online ? "Ignition: no signal"
        : phase === "burning" ? `Burning · ${Fmt.clock(remaining)} left`
        : phase === "countdown" ? `Ignition in T−${Fmt.clock(remaining)}`
        : "Ignition"
    toolTipSubText: !client.online ? `Can't reach ${client.host}`
        : phase === "burning" ? `Locked until ${Fmt.time(session.ends_at).toLowerCase()}`
        : phase === "countdown" ? `${Fmt.estimate(session.minutes).toLowerCase()} burn at ${Fmt.time(session.starts_at).toLowerCase()}`
        : `Click to ignite a ${Plasmoid.configuration.minutes} min burn`

    compactRepresentation: CompactView {
        app: root
    }
    fullRepresentation: FullView {
        app: root
    }

    Client {
        id: client
        url: Plasmoid.configuration.serverUrl
        eager: root.expanded || root.arming > 0 || !root.inPanel
        onFailed: (m) => root.say(m)
    }

    // The dial: arms a burn when idle, ignites a T-minus now, or scrubs an arming countdown.
    function press() {
        if (phase === "countdown") {
            client.act("/api/ignite/now");
        } else if (phase === "idle") {
            if (arming) {
                armer.stop();
                arming = 0;
            } else {
                arming = 3;
                armer.start();
            }
        }
    }

    // Something lit elsewhere while this one was arming: stand down.
    onPhaseChanged: if (phase !== "idle" && arming) {
        armer.stop();
        arming = 0;
    }

    function say(m) {
        message = String(m).toUpperCase();
        hush.restart();
    }

    Timer {
        id: armer
        interval: 1000
        repeat: true
        onTriggered: {
            root.arming -= 1;
            if (root.arming > 0)
                return;
            stop();
            client.act("/api/ignite", { minutes: Plasmoid.configuration.minutes, delay: Plasmoid.configuration.delay });
        }
    }
    Timer {
        id: hush
        interval: 5000
        onTriggered: root.message = ""
    }
}
