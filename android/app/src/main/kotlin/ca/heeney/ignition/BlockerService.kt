package ca.heeney.ignition

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.text.TextUtils
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Toast
import java.util.concurrent.ConcurrentHashMap

/**
 * The phone's half of the lockout (the enforcer does computers). One accessibility service does it all:
 *
 * - While a burn is active it sends you home from a blocked app, or a blocked site in a browser.
 * - Whenever a limit is closed it stands in front of what that limit covers, with ignition's Limits page.
 * - It notes which site each browser shows (the domain only), so screen time and limits can tell sites apart.
 * - While limits are on, or a burn is, it stands in front of the pages that would switch it off: ignition's
 *   App info, its accessibility and usage access switches, the uninstall dialog and the clock settings.
 *   The usage access page is let through while that access is off, so it can be turned on.
 *
 * It reads the app in front, a browser's own address bar, and the text of those few Settings pages. Only
 * the domains it notes are kept, and they go only to your own server.
 */
class BlockerService : AccessibilityService() {
    private var games: Set<String> = emptySet()
    private var gamesAt = 0L
    private val browsers = HashMap<String, Boolean>()
    private var keyboards: Set<String> = emptySet()
    private var lastKick = 0L
    private var lastKicked = ""
    private val main = Handler(Looper.getMainLooper())
    private val worker = Handler(HandlerThread("ignition-limits").apply { start() }.looper)
    /** The app in front, as far as limits go. */
    private var front = ""
    private val tick = Runnable { checkLimits() }
    /** Looks at which app really is in front; see [watchFront]. */
    private val look = Runnable { watchFront() }
    /** When [look] is due (uptime), or 0. */
    private var lookAt = 0L
    private var lastClosed = ""
    private var lastClosedAt = 0L
    private var lastCovered = ""
    private val warned = HashMap<String, Long>()
    private val lastMark = HashMap<String, String>()
    private var pendingSite: String? = null
    private val lookSite = Runnable { pendingSite?.let { noteSite(it) }; pendingSite = null }
    private var pendingGuard: String? = null
    private val lookGuard = Runnable { pendingGuard?.let { guardCheck(it) }; pendingGuard = null }

    override fun onServiceConnected() {
        instance = this
        browsers.clear()
        keyboards = runCatching {
            (getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager).inputMethodList.map { it.packageName }.toSet()
        }.getOrDefault(emptySet())
        refreshGames(true)
        LimitStore.fetchSoon(this, 0)
        checkForeground()
        lookIn(LOOK_MS)
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        main.removeCallbacksAndMessages(null)
        worker.looper.quitSafely()
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val type = event.eventType
        if (type == AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            // Windows came, went or swapped focus: a quick swipe between apps comes only as this.
            lookIn(100)
            return
        }
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && type != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        if (pkg in GUARDED) {
            if (pendingGuard == null) main.postDelayed(lookGuard, 350)
            pendingGuard = pkg
        }
        // A page fires events all the time while it loads, scrolls or plays: read the address bar once it settles.
        if (isBrowser(pkg)) {
            if (pendingSite == null) main.postDelayed(lookSite, 1000)
            pendingSite = pkg
        }
        val arrived = type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED && pkg != "com.android.systemui" &&
            pkg !in keyboards && pkg != front
        if (arrived) front = pkg
        check(pkg)
        if (arrived) checkLimits()
    }

    /**
     * Checks the app that's in front now. Events only come when something on screen changes, so without
     * this a burn that lifts off while you're already in a blocked app wouldn't kick you out until you
     * touched something.
     */
    private fun checkForeground() {
        foregroundPackage()?.let { takeFront(it) }
    }

    /** [pkg] is in front now: the burn, then the site a browser shows, then the limits. */
    private fun takeFront(pkg: String) {
        front = pkg
        check(pkg) // a burn sends it home, which clears front
        if (front == pkg && isBrowser(pkg)) noteSite(pkg)
        checkLimits()
    }

    /**
     * Swiping along the bottom bar back to an app that was covered or sent home brings it to the front
     * without a window state event, so [onAccessibilityEvent] alone never sees it come back. This reads the
     * focused app window instead: when windows change, and every [LOOK_MS] while a limit or burn could block.
     */
    private fun watchFront() {
        main.removeCallbacks(look)
        lookAt = 0L
        try {
            val power = getSystemService(POWER_SERVICE) as PowerManager
            if (!power.isInteractive) return
            if (LimitStore.get(this).limits.isEmpty() && !burning()) return
            val pkg = foregroundPackage() ?: return
            if (pkg == front || pkg == "com.android.systemui" || pkg in keyboards) return
            // Just covered: the Limits page is still on its way, give it a moment before covering again.
            if (pkg == lastCovered && SystemClock.uptimeMillis() - lastClosedAt < 1_000) return
            takeFront(pkg)
        } finally {
            lookIn(LOOK_MS)
        }
    }

    /** Runs [watchFront] in [ms], unless it's due sooner already: a stream of window changes mustn't put it off. */
    private fun lookIn(ms: Long) {
        val now = SystemClock.uptimeMillis()
        val at = now + ms
        if (lookAt in now..at) return
        main.removeCallbacks(look)
        lookAt = at
        main.postAtTime(look, at)
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

    private fun burning(): Boolean {
        val store = Store.get(this)
        return synchronized(store) { Logic.current(store).phase == "burning" }
    }

    /** The burn's block list. */
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
        // Gone home, or about to be: if it comes back without a window event, the next look sees it again.
        front = ""
        val now = SystemClock.uptimeMillis()
        if (what == lastKicked && now - lastKick < 1500) return // one window change fires several events
        lastKick = now
        lastKicked = what
        performGlobalAction(GLOBAL_ACTION_HOME)
        Toast.makeText(this, "$what is blocked while you're burning.", Toast.LENGTH_SHORT).show()
    }

    // ---- limits

    /** Is what's in front over a limit? If not, look again when the nearest one could run out. */
    private fun checkLimits() {
        main.removeCallbacks(tick)
        val pkg = front
        if (pkg.isEmpty() || pkg == PACKAGE) return
        val snap = LimitStore.get(this)
        if (snap.limits.isEmpty()) return
        LimitStore.fetchSoon(this, 5 * 60_000L)
        val site = showing[pkg] ?: ""
        val covering = snap.limits.filter { Limits.covers(it, pkg, site) }
        if (covering.isEmpty()) return
        // Reading the usage log takes a moment: not on the thread that hears about windows.
        worker.post {
            val now = System.currentTimeMillis()
            val own = LimitStore.ownSpans(this)
            var next = 60_000L
            var over: Limit? = null
            var warn: Limit? = null
            for (limit in covering) {
                val open = (limit.openUntil ?: 0) - now
                if (open > 0) {
                    next = minOf(next, open + 500)
                    continue
                }
                val state = Limits.evaluate(limit, Limits.usage(limit, own), now)
                if (state.blocked) {
                    over = limit
                    break
                }
                val left = state.left ?: continue
                next = minOf(next, left + 500)
                if (left in 1..60_000L && now - (warned[limit.id] ?: 0) > 10 * 60_000L) warn = limit
            }
            main.post {
                if (front != pkg) return@post
                over?.let { closed(it, pkg); return@post }
                warn?.let {
                    warned[it.id] = now
                    Toast.makeText(this, "${it.label}: 1 minute left", Toast.LENGTH_SHORT).show()
                }
                main.removeCallbacks(tick)
                main.postDelayed(tick, next.coerceIn(2_000L, 60_000L))
            }
        }
    }

    /**
     * Stands in front of a closed app with the Limits page, which says why and how to unlock it. Not home
     * first: a video sent home goes on playing in a little window. Covering it stops it.
     */
    private fun closed(limit: Limit, pkg: String) {
        front = ""
        // The site a browser showed is only trusted until it's read again, or the browser would stay
        // closed for whatever tab it opens on next.
        showing.remove(pkg)
        startActivity(
            Intent(this, MainActivity::class.java).putExtra(MainActivity.EXTRA_PAGE, "limits")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT),
        )
        // Swiping back and forth covers it again each time; one toast is enough for that.
        val now = SystemClock.uptimeMillis()
        if (limit.id != lastClosed || now - lastClosedAt > 5_000) {
            Toast.makeText(this, "${limit.label} is closed.", Toast.LENGTH_SHORT).show()
        }
        lastClosed = limit.id
        lastClosedAt = now
        lastCovered = pkg
    }

    /** Notes the site a browser shows, for screen time, and looks at the limits again if it changed. */
    private fun noteSite(pkg: String) {
        val host = addressBarText(pkg)?.let { Sites.hostIn(it) } ?: return
        if (showing.put(pkg, host) != host && pkg == front) checkLimits()
        if (lastMark[pkg] == host) return
        lastMark[pkg] = host
        val app = applicationContext
        val mark = Mark(System.currentTimeMillis(), pkg, host)
        worker.post { SiteLog.add(app, mark) }
    }

    // ---- standing in front of the off switches

    private fun guardCheck(pkg: String) {
        if (!(burning() || LimitStore.guarded(this))) return
        val root = rootInActiveWindow ?: return
        try {
            if (root.packageName?.toString() != pkg) return
            val words = textOf(root).lowercase()
            // The Date & time page doesn't always show its switch to a screen reader; its time zone and
            // 24-hour rows give it away (the System page that links to it has the one, not the other).
            val clock = CLOCK.any { it in words } || ("time zone" in words && "24-hour" in words)
            // The usage access switch is only an escape while it's on. While it's off, that page is where you
            // turn it on, and without it this phone's time doesn't count at all.
            val danger = if (Collector.hasAccess(this)) DANGER else DANGER - USAGE
            if (!clock && ("ignition" !in words || danger.none { it in words })) return
        } finally {
            root.recycle()
        }
        performGlobalAction(GLOBAL_ACTION_HOME)
        Toast.makeText(this, if (burning()) "Locked while you're burning." else "Locked while limits are on.", Toast.LENGTH_SHORT).show()
    }

    private fun textOf(root: AccessibilityNodeInfo): String {
        val sb = StringBuilder()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var seen = 0
        while (queue.isNotEmpty() && seen < 600) {
            val n = queue.removeFirst()
            seen++
            n.text?.let { sb.append(it).append('\n') }
            n.contentDescription?.let { sb.append(it).append('\n') }
            for (i in 0 until n.childCount) n.getChild(i)?.let { queue.add(it) }
        }
        return sb.toString()
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
        private const val LOOK_MS = 1_000L

        @Volatile private var instance: BlockerService? = null
        private val main = Handler(Looper.getMainLooper())

        /** The site each browser showed last, for the limits. */
        val showing = ConcurrentHashMap<String, String>()

        /** Settings, and the dialogs that uninstall an app. */
        private val GUARDED = setOf(
            "com.android.settings", "com.google.android.packageinstaller", "com.android.packageinstaller",
            "com.google.android.permissioncontroller", "com.android.permissioncontroller",
        )
        /** Words on the pages that would switch ignition off (App info, its accessibility and usage switches). */
        private val USAGE = setOf("permit usage", "usage access allows")
        private val DANGER = listOf(
            "uninstall", "force stop", "clear storage", "clear data", "clear cache", "use ignition",
            "ignition shortcut", "deactivate",
        ) + USAGE
        /** The phone's clock: moved, it would open every limit that goes by the time of day, and end a burn. */
        private val CLOCK = listOf("set time automatically", "automatic date", "use network-provided time", "set date")

        /** Re-checks the foreground app, e.g. at liftoff or when the limits change; safe from any thread. */
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
