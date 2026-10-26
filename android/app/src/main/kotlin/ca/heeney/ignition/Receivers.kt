package ca.heeney.ignition

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.widget.Toast

/** Internal alarms, minute ticks and notification buttons. */
class EventReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        val api = LocalApi(app)
        val error = when (intent.action) {
            Reactor.ACTION_TICK -> { Reactor.tick(app); null }
            Reactor.ACTION_WARN -> { Reactor.warn(app); null }
            Reactor.ACTION_WINDOW -> { LaunchWindow.tick(app); Reactor.update(app); null }
            Notifier.ACTION_NOW -> api.command("now")
            Notifier.ACTION_SCRUB -> api.command("scrub")
            Notifier.ACTION_EXTEND -> api.command("extend", minutes = intent.getIntExtra("minutes", 15))
            Notifier.ACTION_LW_START -> api.command("ignite", minutes = intent.getIntExtra("minutes", 50), delay = intent.getIntExtra("delay", 0))
                .also { if (it == null) Notifier.cancelLaunchWindow(app) }
            Notifier.ACTION_SKIP -> api.command("skip", scheduleId = intent.getStringExtra("schedule_id") ?: "", date = intent.getStringExtra("date") ?: "")
                .also { if (it == null) Notifier.cancelWarning(app) }
            else -> { Reactor.update(app); null } // EDGE, FIRE
        }
        if (error != null) Toast.makeText(app, error, Toast.LENGTH_SHORT).show()
        if (intent.action != Reactor.ACTION_TICK && intent.action != Reactor.ACTION_WINDOW) {
            UiBus.changed()
            Sync.kick(app)
        }
    }
}

/**
 * Lets Tasker (or anything else) drive ignition:
 *   ca.heeney.ignition.IGNITE  extras: minutes (int, default 50), delay (int minutes, default 0)
 *   ca.heeney.ignition.SCRUB   cancels a T-minus countdown
 *   ca.heeney.ignition.EXTEND  extras: minutes
 * Send it as a broadcast with package ca.heeney.ignition.
 */
class CommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val app = context.applicationContext
        fun num(key: String, default: Int): Int = when (val v = intent.extras?.get(key)) {
            is Number -> v.toInt()
            is String -> v.trim().toIntOrNull() ?: default
            else -> default
        }
        val api = LocalApi(app)
        val error = when (intent.action) {
            "ca.heeney.ignition.IGNITE" -> api.command("ignite", minutes = num("minutes", 50), delay = num("delay", 0))
            "ca.heeney.ignition.SCRUB" -> api.command("scrub")
            "ca.heeney.ignition.EXTEND" -> api.command("extend", minutes = num("minutes", 15))
            else -> null
        }
        if (error != null) Toast.makeText(app, "Ignition: $error", Toast.LENGTH_SHORT).show()
        UiBus.changed()
    }
}

/** Alarms don't survive reboots, app updates or clock changes; re-arm everything. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        Reactor.update(context.applicationContext)
        Sync.kick(context.applicationContext)
    }
}
