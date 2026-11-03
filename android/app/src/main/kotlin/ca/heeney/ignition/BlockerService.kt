package ca.heeney.ignition

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Toast

/**
 * Sends you back to the home screen when a blocked app comes to the foreground, or a blocked site shows
 * in a browser's address bar, while a burn is active. This is the phone's half of the lockout (the
 * enforcer does computers). It only looks at the foreground package name and, in browsers, the
 * address-bar text; nothing is logged or sent anywhere.
 */
class BlockerService : AccessibilityService() {
    private var games: Set<String> = emptySet()
    private var gamesAt = 0L
    private val browsers = HashMap<String, Boolean>()
    private var lastKick = 0L
    private var lastKicked = ""

    override fun onServiceConnected() {
        instance = this
        browsers.clear()
        refreshGames(true)
        checkForeground()
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val type = event.eventType
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && type != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) return
        check(event.packageName?.toString() ?: return)
    }

    /**
     * Checks the app that's in front now. Events only come when something on screen changes, so without
     * this a burn that lifts off while you're already in a blocked app wouldn't kick you out until you
     * touched something.
     */
    private fun checkForeground() {
        check(foregroundPackage() ?: return)
    }

    private fun foregroundPackage(): String? {
        val apps = windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
        val front = apps.firstOrNull { it.isFocused } ?: apps.firstOrNull { it.isActive } ?: apps.maxByOrNull { it.layer }
        val root = front?.root ?: return null
        return try {
            root.packageName?.toString()
        } finally {
            root.recycle()
        }
    }

    private fun check(pkg: String) {
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
        if (sites.isNotEmpty() && isBrowser(pkg)) {
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

    private fun isBrowser(pkg: String): Boolean = browsers.getOrPut(pkg) { opensWebLinks(this, pkg) }

    /**
     * The URL of the page the browser is showing, or null. Returns null while you're typing in the
     * address bar: autocomplete fills it with suggestions ("you" -> "youtube.com") and the suggestion
     * list is full of URLs, none of which you've opened. Once a page loads the bar loses focus.
     *
     * Only the browser's own toolbar is read, never the page: a news article that mentions reddit.com
     * mustn't kick you out.
     */
    private fun addressBarText(pkg: String): String? {
        val root = rootInActiveWindow ?: return null
        try {
            if (root.packageName?.toString() != pkg) return null
            val typing = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.isEditable == true
            if (typing) return null
            return scanForUrl(root)
        } finally {
            root.recycle()
        }
    }

    /**
     * Walks the browser's own views (not the web page, not list rows such as suggestions or top sites)
     * for an address bar: first anything whose view id says it's one (Firefox's Compose
     * ADDRESSBAR_URL_BOX keeps the URL in its content description, Chrome's url_bar in its text), then
     * any unfocused text field, for browsers that name theirs something else.
     */
    private fun scanForUrl(root: AccessibilityNodeInfo): String? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var seen = 0
        var field: String? = null
        while (queue.isNotEmpty() && seen < 800) {
            val n = queue.removeFirst()
            seen++
            if (n.collectionItemInfo != null || n.className?.contains("WebView") == true) continue
            val text = (n.text?.takeIf { it.isNotBlank() } ?: n.contentDescription)?.toString()
            if (!text.isNullOrBlank() && !n.isFocused && Blocking.hostsIn(text).isNotEmpty()) {
                if (Blocking.isUrlBarId(n.viewIdResourceName)) return text
                if (field == null && (n.isEditable || n.className?.contains("EditText") == true)) field = text
            }
            for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
        }
        return field
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

        @Volatile private var instance: BlockerService? = null
        private val main = Handler(Looper.getMainLooper())

        /** Re-checks the foreground app, e.g. at liftoff; safe from any thread. */
        fun recheck() {
            main.post { instance?.checkForeground() }
        }

        /**
         * Whether [pkg] is a browser: it opens any web link. Apps that only open their own site's links
         * (YouTube, Reddit) don't claim example.com, so they don't count.
         */
        fun opensWebLinks(context: Context, pkg: String): Boolean {
            val probe = Intent(Intent.ACTION_VIEW, Uri.parse("https://example.com"))
                .addCategory(Intent.CATEGORY_BROWSABLE)
                .setPackage(pkg)
            return context.packageManager.queryIntentActivities(probe, PackageManager.MATCH_ALL).isNotEmpty()
        }

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
