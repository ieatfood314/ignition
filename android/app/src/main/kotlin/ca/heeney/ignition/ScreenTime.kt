package ca.heeney.ignition

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.ApplicationInfo
import android.os.Build
import android.os.Process
import android.provider.Settings
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.SecureRandom
import kotlin.concurrent.thread

/**
 * Screen time and limits on the phone. Android keeps a usage log whether or not this app runs; [Collector]
 * reads it, [buildSpans] turns it into spans, and [ScreenSync] sends them to the server, which counts every
 * device's time together. The server hands back the limits ([LimitStore]), which [BlockerService] keeps.
 * All of it needs a server: without one the phone only does burns.
 */
object Collector {
    /** How far back to look the first time. Android keeps roughly this much. */
    const val FIRST_REACH = 10L * 24 * 3600 * 1000

    fun hasAccess(ctx: Context): Boolean {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName)
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private val wanted = setOf(
        Ev.RESUMED, Ev.PAUSED, Ev.SCREEN_ON, Ev.SCREEN_OFF, Ev.KEYGUARD_HIDDEN, Ev.SHUTDOWN, Ev.STARTUP,
    )

    fun read(ctx: Context, from: Long, to: Long): List<Event> {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val events = usm.queryEvents(from, to) ?: return emptyList()
        val out = ArrayList<Event>()
        val e = UsageEvents.Event()
        while (events.getNextEvent(e)) {
            if (e.eventType in wanted) out.add(Event(e.timeStamp, e.eventType, e.packageName ?: "", e.className ?: ""))
        }
        return out
    }

    /** An app's proper name, and a first guess at what kind of thing it is. The server's own rules come first. */
    fun describe(ctx: Context, pkg: String): Pair<String, String> {
        val pm = ctx.packageManager
        val info = runCatching { pm.getApplicationInfo(pkg, 0) }.getOrNull() ?: return "" to ""
        val label = runCatching { pm.getApplicationLabel(info).toString() }.getOrDefault("")
        @Suppress("DEPRECATION")
        val game = info.flags and ApplicationInfo.FLAG_IS_GAME != 0
        val hint = when {
            game || info.category == ApplicationInfo.CATEGORY_GAME -> "play"
            info.category == ApplicationInfo.CATEGORY_VIDEO || info.category == ApplicationInfo.CATEGORY_AUDIO ||
                info.category == ApplicationInfo.CATEGORY_NEWS -> "watch"
            info.category == ApplicationInfo.CATEGORY_SOCIAL -> "talk"
            info.category == ApplicationInfo.CATEGORY_PRODUCTIVITY -> "make"
            else -> ""
        }
        return label to hint
    }
}

/** The site marks, one a line: time, package, domain. Kept on the phone only as long as a sync could need them. */
object SiteLog {
    private val lock = Any()
    private fun file(ctx: Context) = File(ctx.applicationContext.filesDir, "sites.tsv")

    fun add(ctx: Context, m: Mark) = synchronized(lock) {
        file(ctx).appendText("${m.time}\t${m.pkg}\t${m.site}\n")
    }

    private fun all(ctx: Context): List<Mark> = synchronized(lock) {
        val f = file(ctx)
        if (!f.exists()) return emptyList()
        f.readLines().mapNotNull { line ->
            val p = line.split('\t')
            val t = p.getOrNull(0)?.toLongOrNull()
            if (p.size == 3 && t != null) Mark(t, p[1], p[2]) else null
        }
    }

    /** Marks from [from] on, plus each browser's last one before it: the tab a browser was left on. */
    fun since(ctx: Context, from: Long): List<Mark> {
        val marks = all(ctx)
        val before = marks.filter { it.time < from }.groupBy { it.pkg }.values.map { it.maxBy { m -> m.time } }
        return before + marks.filter { it.time >= from }
    }

    /** Forgets what no sync can need any more. */
    fun prune(ctx: Context, before: Long) {
        synchronized(lock) {
            val keep = since(ctx, before)
            if (keep.size == all(ctx).size) return
            val f = file(ctx)
            val tmp = File(f.path + ".tmp")
            tmp.writeText(keep.sortedBy { it.time }.joinToString("") { "${it.time}\t${it.pkg}\t${it.site}\n" })
            tmp.renameTo(f)
        }
    }
}

/** Sends the phone's spans to the server. Sending the same thing twice is harmless: spans are keyed by their start. */
object ScreenSync {
    private const val CHUNK = 4000
    /** 2: an app that opens behind a dark screen doesn't count until the screen comes on. */
    private const val RULES = 2
    /** Spans go at most this often; limits are fetched with them. */
    private const val EVERY = 5 * 60_000L
    private val lock = Any()

    fun prefs(ctx: Context): SharedPreferences = ctx.applicationContext.getSharedPreferences("screen", Context.MODE_PRIVATE)

    /** Send soon if it has been a while, off the calling thread. */
    fun kick(ctx: Context, force: Boolean = false) {
        val app = ctx.applicationContext
        if (!force && System.currentTimeMillis() - prefs(app).getLong("last_ingest", 0) < EVERY) return
        thread(name = "ignition-screen") { run(app) }
    }

    /** Blocking. Returns an error message, or null. */
    fun run(ctx: Context): String? = synchronized(lock) {
        val url = Store.get(ctx).let { synchronized(it) { it.serverUrl } }
        if (url.isEmpty()) return null
        val error = runCatching { push(ctx, url) }.exceptionOrNull()?.let { it.message ?: it.javaClass.simpleName }
        prefs(ctx).edit().putLong("last_ingest", System.currentTimeMillis()).putString("last_error", error ?: "").apply()
        LimitStore.fetch(ctx)
        if (error == null) Today.send(ctx, url)
        error
    }

    /**
     * Made once; the server knows this phone by it. If the server already has a phone of this model (lightcurve
     * counted it before ignition did), it carries on as that one, so its history stays in one place. Throws if
     * the server can't be asked, so a phone that was offline the first time doesn't become a second phone.
     */
    @Synchronized
    fun deviceId(ctx: Context, url: String): String {
        val p = prefs(ctx)
        p.getString("device_id", null)?.let { return it }
        val slug = Build.MODEL.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-').take(30).ifEmpty { "phone" }
        val devs = Http.get("$url/api/devices").optJSONArray("devices") ?: JSONArray()
        val known = (0 until devs.length()).map { devs.getJSONObject(it) }
            .filter { it.optString("platform").startsWith("Android") && it.optString("id").startsWith("$slug-") }
            .maxByOrNull { it.optLong("last_seen") }?.optString("id")
        val edit = p.edit()
        if (known != null) {
            // The server already has this phone's past, with the sites lightcurve saw: send only what's new,
            // rather than rebuilding (and replacing) it from a log that never noted a site.
            edit.putInt("rules", RULES).putLong("checkpoint", System.currentTimeMillis())
        }
        val id = known ?: (slug + "-" + ByteArray(4).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) })
        edit.putString("device_id", id).apply()
        return id
    }

    private fun name(ctx: Context): String =
        runCatching { Settings.Global.getString(ctx.contentResolver, Settings.Global.DEVICE_NAME) }.getOrNull()
            ?.takeIf { it.isNotBlank() } ?: Build.MODEL

    private fun push(ctx: Context, url: String) {
        if (!Collector.hasAccess(ctx)) throw IllegalStateException("Usage access is off")
        val p = prefs(ctx)
        val id = deviceId(ctx, url)
        val now = System.currentTimeMillis()
        // When the counting rules change, everything the phone still remembers is rebuilt and replaces the server's copy.
        val redo = p.getInt("rules", 0) < RULES
        val checkpoint = p.getLong("checkpoint", 0)
        val from = if (checkpoint > 0 && !redo) checkpoint else now - Collector.FIRST_REACH
        val built = buildSpans(Collector.read(ctx, from, now), now)
        SiteLog.prune(ctx, now - Collector.FIRST_REACH - 2 * 24 * 3600 * 1000L)
        val spans = splitBySite(built.spans, SiteLog.since(ctx, from))

        val names = JSONObject()
        for (pkg in spans.map { it.pkg }.toSet()) {
            val (label, hint) = Collector.describe(ctx, pkg)
            if (label.isNotEmpty() || hint.isNotEmpty()) names.put(pkg, JSONObject().put("label", label).put("hint", hint))
        }
        val tablet = ctx.resources.configuration.smallestScreenWidthDp >= 600
        val version = runCatching { ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName }.getOrNull() ?: ""
        val device = JSONObject()
            .put("id", id).put("name", name(ctx)).put("kind", if (tablet) "tablet" else "phone")
            .put("platform", "Android " + Build.VERSION.RELEASE).put("version", "ignition $version")

        val chunks = spans.chunked(CHUNK).ifEmpty { listOf(emptyList()) }
        for ((i, chunk) in chunks.withIndex()) {
            val final = i == chunks.lastIndex
            val body = JSONObject().put("device", device)
                .put("spans", JSONArray().also { arr ->
                    for (s in chunk) arr.put(JSONArray().put(s.start).put(s.end).put(s.pkg).put(s.site))
                })
                .put("complete", final)
                .put("active", built.active)
            if (final) body.put("pickups", JSONArray(built.pickups)).put("names", names)
            if (redo && i == 0 && spans.isNotEmpty()) body.put("replace_from", from)
            Http.send("POST", "$url/api/ingest", body.toString())
        }
        p.edit().apply {
            built.checkpoint?.let { putLong("checkpoint", it) }
            putInt("rules", RULES)
        }.apply()
    }
}

/**
 * The limits as the server last gave them, kept on the phone so they hold with no server in reach. Unlocks
 * live on the server (one opens a limit on every device), so the phone learns of one at the next fetch.
 */
object LimitStore {
    /** The guard stops standing in front of the off switches when the server hasn't been heard from in this long. */
    private const val GUARD_STALE = 3L * 86_400_000

    private val lock = Any()
    @Volatile private var cached: LimitSnapshot? = null
    @Volatile private var lastFetch = 0L

    private fun file(ctx: Context) = File(ctx.applicationContext.filesDir, "limits.json")

    fun get(ctx: Context): LimitSnapshot {
        cached?.let { return it }
        synchronized(lock) {
            cached?.let { return it }
            val f = file(ctx)
            val snap = runCatching {
                if (f.exists()) LimitSnapshot.parse(JSONObject(f.readText()), f.lastModified(), BlockerService.gamePackages(ctx)) else null
            }.getOrNull() ?: LimitSnapshot.EMPTY
            cached = snap
            return snap
        }
    }

    fun forget(ctx: Context) = synchronized(lock) {
        file(ctx).delete()
        cached = LimitSnapshot.EMPTY
        BlockerService.recheck()
    }

    /** Fetch soon, unless that was done in the last [minGapMs]. */
    fun fetchSoon(ctx: Context, minGapMs: Long = 60_000) {
        if (System.currentTimeMillis() - lastFetch < minGapMs) return
        lastFetch = System.currentTimeMillis()
        val app = ctx.applicationContext
        thread(name = "ignition-limits") { fetch(app) }
    }

    /** Asks the server for the limits. Blocking. Keeps the old ones if it can't. */
    fun fetch(ctx: Context): Boolean {
        val url = Store.get(ctx).let { synchronized(it) { it.serverUrl } }
        if (url.isEmpty()) return false
        lastFetch = System.currentTimeMillis()
        return runCatching {
            val id = URLEncoder.encode(ScreenSync.deviceId(ctx, url), "UTF-8")
            val text = Http.send("GET", "$url/api/limits?device=$id&app=ignition", null)
            val snap = LimitSnapshot.parse(JSONObject(text), System.currentTimeMillis(), BlockerService.gamePackages(ctx))
            synchronized(lock) {
                val f = file(ctx)
                val tmp = File(f.path + ".tmp")
                tmp.writeText(text)
                tmp.renameTo(f)
                cached = snap
            }
            BlockerService.recheck()
            true
        }.getOrDefault(false)
    }

    /** Whether the blocker stands in front of the pages that would switch it off. */
    fun guarded(ctx: Context): Boolean {
        val s = get(ctx)
        return s.guard && s.limits.isNotEmpty() && System.currentTimeMillis() - s.fetched < GUARD_STALE
    }

    // ---- the phone's own time

    @Volatile private var ownAt = 0L
    @Volatile private var own: List<Span> = emptyList()

    /** This phone's spans over the last [HORIZON], read at most every few seconds. */
    fun ownSpans(ctx: Context): List<Span> {
        val now = System.currentTimeMillis()
        if (now - ownAt < 5_000) return own
        if (!Collector.hasAccess(ctx)) return emptyList()
        val from = now - HORIZON
        // From an hour earlier, so an app that was already open when the horizon begins is still seen.
        val built = buildSpans(Collector.read(ctx, from - 3600_000L, now), now)
        own = splitBySite(built.spans, SiteLog.since(ctx, from - 3600_000L)).filter { it.end > from }
        ownAt = now
        return own
    }
}

/** Plain HTTP for screen time, limits and the in-app pages that only the server can answer. */
object Http {
    class Failed(val status: Int, message: String) : Exception(message)

    fun get(url: String): JSONObject = JSONObject(send("GET", url, null))

    /** Returns the body. Throws [Failed] with the server's own error message on a 4xx/5xx. Blocking. */
    fun send(method: String, url: String, body: String?): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8_000
            conn.readTimeout = 20_000
            conn.setRequestProperty("Accept", "application/json")
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code >= 400) {
                val said = runCatching { JSONObject(text).optString("error") }.getOrNull()
                throw Failed(code, if (!said.isNullOrEmpty()) said else "HTTP $code")
            }
            return text
        } finally {
            conn.disconnect()
        }
    }
}

/**
 * Hands today's screen time (every screen, two at once counted once) to doppler, whose 2x2 widget shows it.
 * Sent after every good sync, so it's never newer than what the server was just told.
 */
object Today {
    private const val DOPPLER = "ca.heeney.doppler"
    const val TODAY = "ca.heeney.ignition.TODAY"
    const val REFRESH = "ca.heeney.ignition.REFRESH"

    /** Blocking. Does nothing if doppler isn't installed. */
    fun send(ctx: Context, url: String) {
        if (url.isEmpty()) return
        if (runCatching { ctx.packageManager.getPackageInfo(DOPPLER, 0) }.isFailure) return
        runCatching {
            val day = Http.get("$url/api/day")
            val date = day.optString("date")
            if (date.isEmpty() || !day.has("lit")) return
            ctx.sendBroadcast(Intent(TODAY).setPackage(DOPPLER).putExtra("lit", day.optDouble("lit").toLong()).putExtra("date", date))
        }
    }
}

/** doppler asks for a fresh number when its widget comes back into view: sync now, then answer. */
class RefreshReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Today.REFRESH) return
        val app = ctx.applicationContext
        val pending = goAsync()
        thread(name = "ignition-refresh") {
            try {
                if (System.currentTimeMillis() - ScreenSync.prefs(app).getLong("last_ingest", 0) > 30_000) ScreenSync.run(app)
                else Today.send(app, Store.get(app).let { synchronized(it) { it.serverUrl } })
            } finally {
                pending.finish()
            }
        }
    }
}
