package ca.heeney.ignition

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import org.json.JSONObject
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId

/**
 * The "launch window": a daily stretch when, if you've been home a while and haven't started anything,
 * the app nudges you to begin a T-minus. "Home" is recognised by the current Wi-Fi's gateway address, so
 * it needs no location permission and nothing leaves the phone. All read through [settings]'s launch_window.
 */
object LaunchWindow {
    private const val NUDGED = "lw_nudged_date"     // YYYYMMDD of the last nudge
    private const val HOME_SINCE = "lw_home_since"   // epoch secs we noticed we were home, 0 if away
    private const val GATEWAY = "lw_home_gateway"

    fun config(store: Store): JSONObject = store.settings.optJSONObject("launch_window") ?: JSONObject()

    private fun minutesOfDay(hm: String): Int =
        runCatching { LocalTime.parse(hm) }.getOrDefault(LocalTime.NOON).let { it.hour * 60 + it.minute }

    /** Is [t] inside today's window, on an enabled day? */
    fun isOpen(cfg: JSONObject, t: Long = Logic.now()): Boolean {
        if (!cfg.optBoolean("enabled")) return false
        val d = Instant.ofEpochSecond(t).atZone(ZoneId.systemDefault())
        val days = cfg.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.getInt(it) }.toSet() } ?: emptySet()
        if (d.dayOfWeek.value !in days) return false
        val mins = d.hour * 60 + d.minute
        return mins >= minutesOfDay(cfg.optString("start", "15:00")) && mins < minutesOfDay(cfg.optString("end", "17:30"))
    }

    /** Epoch secs of today's window start (for scheduling the first check), or null if not applicable today. */
    fun opensAt(cfg: JSONObject, t: Long = Logic.now()): Long? {
        if (!cfg.optBoolean("enabled")) return null
        val zone = ZoneId.systemDefault()
        val day = Instant.ofEpochSecond(t).atZone(zone).toLocalDate()
        val days = cfg.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.getInt(it) }.toSet() } ?: emptySet()
        for (n in 0..7) {
            val d = day.plusDays(n.toLong())
            if (d.dayOfWeek.value !in days) continue
            val at = d.atTime(LocalTime.parse(cfg.optString("start", "15:00"))).atZone(zone).toEpochSecond()
            if (at > t) return at
        }
        return null
    }

    private fun today(t: Long): String =
        Instant.ofEpochSecond(t).atZone(ZoneId.systemDefault()).toLocalDate().toString()

    fun homeGateway(context: Context): String? = Reactor.prefs(context).getString(GATEWAY, null)?.ifEmpty { null }

    fun setHomeToCurrent(context: Context): Boolean {
        val gw = currentWifiGateway(context) ?: return false
        Reactor.prefs(context).edit().putString(GATEWAY, gw).apply()
        return true
    }

    /** The default-route gateway of a connected Wi-Fi (e.g. "192.168.68.1"), ignoring any VPN. No location needed. */
    fun currentWifiGateway(context: Context): String? {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        for (net in cm.allNetworks) {
            val caps = cm.getNetworkCapabilities(net) ?: continue
            if (!caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) continue
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue
            val lp = cm.getLinkProperties(net) ?: continue
            lp.routes.firstOrNull { it.isDefaultRoute && it.gateway != null }?.let { return it.gateway!!.hostAddress }
        }
        return null
    }

    private fun atHome(context: Context): Boolean {
        val home = homeGateway(context) ?: return false
        return currentWifiGateway(context) == home
    }

    /**
     * Called periodically while a window may be open. Tracks when we arrived home and, once we've been
     * home long enough with nothing running, posts the nudge (once per day). Returns true if it nudged.
     */
    fun tick(context: Context): Boolean {
        val store = Store.get(context)
        val cfg = config(store)
        if (!cfg.optBoolean("enabled")) return false
        val p = Reactor.prefs(context)
        val t = Logic.now()

        val timeTrigger = cfg.optString("trigger") == "time"
        val home = timeTrigger || atHome(context)
        val since = p.getLong(HOME_SINCE, 0)
        if (home && since == 0L) p.edit().putLong(HOME_SINCE, t).apply()
        if (!home && since != 0L) p.edit().putLong(HOME_SINCE, 0).apply()

        if (!isOpen(cfg, t)) return false
        if (p.getString(NUDGED, "") == today(t)) return false
        if (synchronized(store) { Logic.current(store, t).phase } != "idle") return false
        if (!home) return false
        val ref = if (timeTrigger) (opensAtOrStartToday(cfg, t) ?: t) else p.getLong(HOME_SINCE, t)
        if (t - ref < cfg.optInt("after", 20) * 60L) return false

        p.edit().putString(NUDGED, today(t)).apply()
        Notifier.launchWindow(context, cfg)
        return true
    }

    /** Today's window start (used as the reference when the trigger is "the window opens"). */
    private fun opensAtOrStartToday(cfg: JSONObject, t: Long): Long? {
        val zone = ZoneId.systemDefault()
        val day = Instant.ofEpochSecond(t).atZone(zone).toLocalDate()
        return day.atTime(LocalTime.parse(cfg.optString("start", "15:00"))).atZone(zone).toEpochSecond()
    }

    /** Was the nudge already given today? (Lets the UI/Reactor avoid re-arming needlessly.) */
    fun nudgedToday(context: Context, t: Long = Logic.now()): Boolean =
        Reactor.prefs(context).getString(NUDGED, "") == today(t)
}
