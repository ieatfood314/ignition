package ca.heeney.ignition

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.Toast

/**
 * Sends you back to the home screen when a blocked app comes to the foreground, or a blocked site shows
 * in a browser's address bar, while a burn is active. This is the phone's half of the lockout (the
 * enforcer does computers). It only looks at the foreground package name and, in a few known browsers,
 * the address-bar text; nothing is logged or sent anywhere.
 */
class BlockerService : AccessibilityService() {
    private var games: Set<String> = emptySet()
    private var gamesAt = 0L
    private var lastKick = 0L
    private var lastKicked = ""

    override fun onServiceConnected() {
        instanceConnected = true
        refreshGames(true)
    }

    override fun onDestroy() {
        instanceConnected = false
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val type = event.eventType
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && type != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg == PACKAGE || pkg == "com.android.systemui" || pkg.isEmpty()) return

        val store = Store.get(this)
        val cfg: Blocking.Config
        val sites: List<String>
        synchronized(store) {
            if (Logic.current(store).phase != "burning") return
            cfg = Blocking.config(store.settings)
            sites = cfg.sites
        }
        if (cfg.empty) return

        // A blocked app in front: home, every time it's opened.
        if (cfg.apps.containsKey(pkg) || (cfg.games && isGame(pkg))) {
            kick(cfg.apps[pkg] ?: appLabel(pkg))
            return
        }
        // A blocked site in a browser's address bar.
        if (sites.isNotEmpty() && pkg in BROWSERS) {
            val site = addressBarText(pkg)?.let { Blocking.blockedSiteIn(it, sites) } ?: return
            kick(site)
        }
    }

    private fun kick(what: String) {
        val now = SystemClock.uptimeMillis()
        if (what == lastKicked && now - lastKick < 1500) return // one window change fires several events
        lastKick = now
        lastKicked = what
        performGlobalAction(GLOBAL_ACTION_HOME)
        Toast.makeText(this, "$what is blocked while you're burning.", Toast.LENGTH_SHORT).show()
    }

    // ---- browser address bars

    private fun addressBarText(pkg: String): String? {
        val root = rootInActiveWindow ?: return null
        try {
            for (id in URL_BAR_IDS) {
                // Classic Android view ids are "pkg:id/name"; Compose testTags (ADDRESSBAR_URL) are the
                // bare string. Firefox's redesigned toolbar is Compose, so try both forms.
                val forms = if (id.contains(":")) listOf(id) else listOf(id, "$pkg:id/$id")
                for (form in forms) {
                    for (n in root.findAccessibilityNodeInfosByViewId(form)) {
                        val t = n.text ?: n.contentDescription
                        if (!t.isNullOrBlank()) return t.toString()
                    }
                }
            }
            // Fallback for toolbars we don't have an id for: the editable/url-ish node nearest the top.
            return scanForUrl(root)
        } finally {
            root.recycle()
        }
    }

    /** Breadth-first walk for an address-bar-like node (editable, or a url/address/origin view id). */
    private fun scanForUrl(root: AccessibilityNodeInfo): String? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var seen = 0
        while (queue.isNotEmpty() && seen < 500) {
            val n = queue.removeFirst()
            seen++
            val vid = n.viewIdResourceName?.lowercase() ?: ""
            val looksUrl = n.isEditable ||
                (n.className?.contains("EditText") == true) ||
                vid.contains("url") || vid.contains("address") || vid.contains("origin")
            if (looksUrl) {
                val t = n.text ?: n.contentDescription
                if (!t.isNullOrBlank()) return t.toString()
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
        }
        return null
    }

    // ---- "every game"

    private fun isGame(pkg: String): Boolean {
        refreshGames(false)
        return pkg in games
    }

    private fun refreshGames(force: Boolean) {
        if (!force && SystemClock.elapsedRealtime() - gamesAt < 10 * 60_000L) return
        gamesAt = SystemClock.elapsedRealtime()
        games = gamePackages(this)
    }

    private fun appLabel(pkg: String): String = try {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    } catch (e: PackageManager.NameNotFoundException) {
        pkg
    }

    companion object {
        const val PACKAGE = "ca.heeney.ignition"

        @Volatile var instanceConnected = false

        private val BROWSERS = setOf(
            "org.mozilla.firefox", "io.github.forkmaintainers.iceraven", "org.mozilla.fenix",
            "org.torproject.torbrowser", "com.android.chrome", "org.bromite.bromite", "com.brave.browser",
        )
        // Fenix/Firefox (view id + the Compose test tag it exposes as a resource id), then Chromium.
        private val URL_BAR_IDS = listOf(
            "mozac_browser_toolbar_url_view", "ADDRESSBAR_URL", "mozac_browser_toolbar_origin_view",
            "com.android.chrome:id/url_bar", "url_bar",
        )

        /** Whether the accessibility service is both enabled in settings and connected. */
        fun enabled(context: Context): Boolean {
            val flat = Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: return false
            val me = ComponentName(context, BlockerService::class.java)
            val splitter = TextUtils.SimpleStringSplitter(':')
            splitter.setString(flat)
            for (name in splitter) {
                if (ComponentName.unflattenFromString(name) == me) return true
            }
            return false
        }

        fun gamePackages(context: Context): Set<String> {
            val pm = context.packageManager
            val out = HashSet<String>()
            for (app in pm.getInstalledApplications(0)) {
                if (app.packageName == PACKAGE) continue
                val isGame = app.flags and ApplicationInfo.FLAG_IS_GAME != 0 || app.category == ApplicationInfo.CATEGORY_GAME
                if (isGame) out.add(app.packageName)
            }
            return out
        }
    }
}
