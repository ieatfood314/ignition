import QtQuick

// Talks to the ignition server, the same JSON API the website uses. It polls /api/ignite (and /api/items
// if asked) and keeps `now` ticking in the server's clock so countdowns stay right on a skewed desktop.
Item {
    id: client

    property string url: "http://127.0.0.1:4580"
    property bool watchItems: false
    // Poll faster while something is happening, or while the widget is open.
    property bool eager: false

    property var ignite: null
    property var items: []
    // Optimistic until the first reply says otherwise.
    property bool online: true
    property bool loaded: false
    property bool itemsLoaded: false
    property real offset: 0
    property real now: Date.now() / 1000

    readonly property string phase: ignite ? (ignite.phase || (ignite.active ? "burning" : "idle")) : "idle"
    readonly property var session: ignite ? ignite.session : null
    readonly property var stats: ignite ? ignite.stats : null
    // "nas:8445", for messages.
    readonly property string host: url.replace(/^[a-z]+:\/\//, "").replace(/\/.*$/, "")

    signal failed(string message)

    function base() {
        return url.replace(/\/+$/, "");
    }

    // done(ok, data or error message). Replies from a server we've since been pointed away from are dropped.
    function request(method, path, body, done) {
        const sent = base();
        const xhr = new XMLHttpRequest();
        const guard = Qt.createQmlObject("import QtQuick; Timer { interval: 8000 }", client);
        guard.triggered.connect(() => xhr.abort());
        guard.start();
        xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            guard.stop();
            guard.destroy();
            if (sent !== base())
                return;
            let data = null;
            try {
                data = JSON.parse(xhr.responseText);
            } catch (e) {}
            const ok = xhr.status >= 200 && xhr.status < 300 && data !== null;
            client.online = xhr.status !== 0;
            if (done)
                done(ok, ok ? data : (data && data.error) || (xhr.status ? `HTTP ${xhr.status}` : "no signal"));
        };
        xhr.open(method, base() + path);
        if (body !== undefined && body !== null) {
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.send(JSON.stringify(body));
        } else {
            xhr.send();
        }
    }

    function setIgnite(data) {
        offset = data.now - Date.now() / 1000;
        ignite = data;
        loaded = true;
        tick();
    }

    property bool _igniteBusy: false
    function refresh() {
        if (_igniteBusy)
            return;
        _igniteBusy = true;
        request("GET", "/api/ignite", null, (ok, data) => {
            _igniteBusy = false;
            if (ok)
                setIgnite(data);
        });
        if (watchItems)
            refreshItems();
    }

    property bool _itemsBusy: false
    function refreshItems() {
        if (_itemsBusy)
            return;
        _itemsBusy = true;
        request("GET", "/api/items", null, (ok, data) => {
            _itemsBusy = false;
            if (ok) {
                items = data;
                itemsLoaded = true;
            }
        });
    }

    // POST an /api/ignite/... action; the reply is the new state.
    function act(path, body) {
        request("POST", path, body || {}, (ok, data) => {
            if (ok)
                setIgnite(data);
            else
                failed(data);
        });
    }

    function patchItem(id, patch, done) {
        request("PATCH", "/api/items/" + encodeURIComponent(id), patch, (ok, data) => {
            if (!ok)
                failed(data);
            if (done)
                done(ok);
            refreshItems();
            refresh();
        });
    }

    function addItem(body) {
        request("POST", "/api/items", body, (ok, data) => {
            if (!ok)
                failed(data);
            refreshItems();
        });
    }

    function tick() {
        now = Date.now() / 1000 + offset;
        // A phase edge (liftoff, cutoff) passed: fetch the new state rather than wait for the next poll.
        const s = session;
        if (s && ((phase === "countdown" && now >= s.starts_at) || (phase === "burning" && now >= s.ends_at)))
            refresh();
    }

    onUrlChanged: {
        _igniteBusy = false;
        _itemsBusy = false;
        online = true;
        ignite = null;
        items = [];
        loaded = false;
        itemsLoaded = false;
        refresh();
    }
    Component.onCompleted: refresh()

    Timer {
        running: true
        repeat: true
        interval: client.eager || client.phase !== "idle" ? 2000 : 5000
        onTriggered: client.refresh()
    }
    Timer {
        running: client.watchItems
        repeat: true
        interval: client.eager ? 5000 : 20000
        onTriggered: client.refreshItems()
    }
    Timer {
        running: client.phase !== "idle"
        repeat: true
        interval: 250
        onTriggered: client.tick()
    }
}
