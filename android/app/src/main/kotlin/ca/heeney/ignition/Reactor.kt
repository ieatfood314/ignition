package ca.heeney.ignition

import android.app.AlarmManager
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import org.json.JSONObject

/**
 * Turns the current state into side effects. Called after every change (UI, sync, alarms, boot):
 * fires due launch schedules, detects phase transitions (Tasker broadcasts, Do Not Disturb, liftoff/MECO
 * alerts), keeps the ongoing notification, exact alarms and widgets in step.
 */
object Reactor {
    const val ACTION_EDGE = "ca.heeney.ignition.internal.EDGE"
    const val ACTION_FIRE = "ca.heeney.ignition.internal.FIRE"
    const val ACTION_WARN = "ca.heeney.ignition.internal.WARN"
    const val ACTION_TICK = "ca.heeney.ignition.internal.TICK"
    const val ACTION_WINDOW = "ca.heeney.ignition.internal.WINDOW"

    // Broadcasts for Tasker & friends.
    const val BROADCAST_COUNTDOWN = "ca.heeney.ignition.COUNTDOWN"
    const val BROADCAST_LIFTOFF = "ca.heeney.ignition.LIFTOFF"
    const val BROADCAST_MECO = "ca.heeney.ignition.MECO"

    fun prefs(context: Context) = context.applicationContext.getSharedPreferences("ignition", Context.MODE_PRIVATE)

    @Synchronized
    fun update(context: Context) {
        val app = context.applicationContext
        val store = Store.get(app)
        val t = Logic.now()
        val cur: Logic.Current
        val next: JSONObject?
        var created = false
        val syncing: Boolean
        synchronized(store) {
            if (Logic.runSchedules(store, t)) {
                store.save()
                created = true
            }
            cur = Logic.current(store, t)
            next = Logic.nextSchedule(store, t)
            syncing = store.serverUrl.isNotEmpty()
        }
        if (created) Sync.kick(app)
        transitions(app, cur, t)
        Notifier.ongoing(app, cur)
        alarms(app, cur, next, t)
        Widgets.updateAll(app)
        Sync.schedulePeriodic(app, syncing)
        LaunchWindow.tick(app)
        if (created) UiBus.changed()
    }

    private fun transitions(app: Context, cur: Logic.Current, t: Long) {
        val p = prefs(app)
        val lastPhase = p.getString("last_phase", "idle")
        val lastId = p.getString("last_session", "") ?: ""
        val s = cur.session
        val id = s?.optString("id") ?: ""
        val edit = p.edit()
        if (cur.phase != lastPhase || id != lastId) {
            if (lastPhase == "burning") {
                val minutes = p.getInt("last_minutes", 0)
                val natural = t >= p.getLong("last_ends_at", 0) - 5
                broadcast(app, BROADCAST_MECO, minutes, p.getLong("last_starts_at", 0), p.getLong("last_ends_at", 0), "")
                dnd(app, false)
                Notifier.cancelRing(app)
                if (natural && minutes > 0) Notifier.alert(app, "Main engine cutoff", "$minutes min burned. Nicely done.")
            }
            // Any new session clears a standing nudge.
            if (s != null && cur.phase != "idle") Notifier.cancelLaunchWindow(app)
            if (s != null && cur.phase == "countdown") {
                broadcast(app, BROADCAST_COUNTDOWN, s.optInt("minutes"), s.optLong("starts_at"), s.optLong("ends_at"), s.optString("source"))
            }
            if (s != null && cur.phase == "burning") {
                broadcast(app, BROADCAST_LIFTOFF, s.optInt("minutes"), s.optLong("starts_at"), s.optLong("ends_at"), s.optString("source"))
                if (lastPhase == "countdown" || s.optString("source") == "schedule") {
                    Notifier.alert(app, "Liftoff", "${s.optInt("minutes")} min burn. Locked until ${Notifier.clock(app, s.optLong("ends_at"))}.")
                    if (p.getBoolean("ring_enabled", false)) Notifier.ring(app, s.optInt("minutes"))
                }
                dnd(app, true)
            }
            edit.putString("last_phase", cur.phase).putString("last_session", id)
        }
        if (s != null && cur.phase == "burning") {
            edit.putInt("last_minutes", s.optInt("minutes")).putLong("last_starts_at", s.optLong("starts_at"))
                .putLong("last_ends_at", s.optLong("ends_at"))
        }
        edit.apply()
    }

    private fun broadcast(app: Context, action: String, minutes: Int, startsAt: Long, endsAt: Long, source: String) {
        app.sendBroadcast(
            Intent(action)
                .putExtra("minutes", minutes)
                .putExtra("starts_at", startsAt)
                .putExtra("ends_at", endsAt)
                .putExtra("source", source),
        )
    }

    // ---- Do Not Disturb

    fun dndGranted(app: Context) = app.getSystemService(NotificationManager::class.java).isNotificationPolicyAccessGranted

    private fun dnd(app: Context, on: Boolean) {
        val p = prefs(app)
        val nm = app.getSystemService(NotificationManager::class.java)
        if (!nm.isNotificationPolicyAccessGranted) return
        if (on) {
            if (p.getBoolean("dnd_enabled", false) && nm.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_ALL) {
                nm.setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_PRIORITY)
                p.edit().putBoolean("dnd_applied", true).apply()
            }
        } else if (p.getBoolean("dnd_applied", false)) {
            // Only undo what we did: leave DND alone if the user changed it meanwhile.
            if (nm.currentInterruptionFilter == NotificationManager.INTERRUPTION_FILTER_PRIORITY) {
                nm.setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_ALL)
            }
            p.edit().putBoolean("dnd_applied", false).apply()
        }
    }

    // ---- alarms

    private fun pending(app: Context, action: String, code: Int): PendingIntent = PendingIntent.getBroadcast(
        app, code, Intent(app, EventReceiver::class.java).setAction(action),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun alarms(app: Context, cur: Logic.Current, next: JSONObject?, t: Long) {
        val am = app.getSystemService(AlarmManager::class.java)
        val edge = pending(app, ACTION_EDGE, 1)
        val fire = pending(app, ACTION_FIRE, 2)
        val warn = pending(app, ACTION_WARN, 3)
        val tick = pending(app, ACTION_TICK, 4)

        val s = cur.session
        when (cur.phase) {
            "countdown" -> exact(am, s!!.optLong("starts_at") * 1000, edge)
            "burning" -> exact(am, s!!.optLong("ends_at") * 1000, edge)
            else -> am.cancel(edge)
        }
        if (next != null) {
            val at = next.optLong("at")
            exact(am, at * 1000, fire)
            val warnAt = at - next.optInt("lead") * 60L
            if (next.optInt("lead") > 0 && warnAt > t) exact(am, warnAt * 1000, warn) else am.cancel(warn)
        } else {
            am.cancel(fire)
            am.cancel(warn)
        }
        // Minute ticks keep widgets current while something is happening; non-wakeup so they cost nothing asleep.
        if (cur.phase != "idle") {
            am.setRepeating(AlarmManager.RTC, System.currentTimeMillis() + 60_000, 60_000, tick)
        } else {
            am.cancel(tick)
        }

        // Launch window: when it's open and nothing is running, poll every few minutes to catch "home long
        // enough"; otherwise wake once when it next opens. tick() does the actual nudging.
        val window = pending(app, ACTION_WINDOW, 5)
        val store = Store.get(app)
        val cfg = synchronized(store) { LaunchWindow.config(store) }
        val next5 = t + 5 * 60
        val desired = when {
            !cfg.optBoolean("enabled") -> null
            cur.phase == "idle" && LaunchWindow.isOpen(cfg, t) && !LaunchWindow.nudgedToday(app, t) -> next5
            else -> LaunchWindow.opensAt(cfg, t)
        }
        if (desired != null) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, desired * 1000, window)
        } else {
            am.cancel(window)
        }
    }

    private fun exact(am: AlarmManager, atMs: Long, pi: PendingIntent) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !am.canScheduleExactAlarms()) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        } else {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        }
    }

    /** The T-minus warning before a scheduled launch window. */
    fun warn(app: Context) {
        val store = Store.get(app)
        val next = synchronized(store) { Logic.nextSchedule(store) } ?: return
        val left = next.optLong("at") - Logic.now()
        if (left <= 0 || left > next.optInt("lead") * 60L + 60) return
        Notifier.warning(app, next)
    }
}
