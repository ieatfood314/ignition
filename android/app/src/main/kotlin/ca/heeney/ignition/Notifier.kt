package ca.heeney.ignition

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.text.format.DateFormat
import org.json.JSONObject
import java.util.Date

object Notifier {
    private const val CH_BURN = "burn"
    private const val CH_ALERTS = "alerts"
    private const val CH_RING = "ring"
    private const val CH_WINDOW = "window"
    private const val ID_ONGOING = 1
    private const val ID_ALERT = 2
    private const val ID_WARN = 3
    private const val ID_RING = 4
    private const val ID_WINDOW = 5
    const val ACCENT = 0xFFE23A29.toInt()

    const val ACTION_NOW = "ca.heeney.ignition.internal.NOW"
    const val ACTION_SCRUB = "ca.heeney.ignition.internal.SCRUB"
    const val ACTION_EXTEND = "ca.heeney.ignition.internal.EXTEND"
    const val ACTION_SKIP = "ca.heeney.ignition.internal.SKIP"
    const val ACTION_LW_START = "ca.heeney.ignition.internal.LW_START"

    fun channels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_BURN, "Burning", NotificationManager.IMPORTANCE_LOW).apply {
                description = "The countdown while a burn or T-minus is running"
                setShowBadge(false)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_ALERTS, "Liftoff & MECO", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Ignition, end of a burn, and T-minus warnings before scheduled launches"
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_RING, "Liftoff ring", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Rings and lights up the lock screen when a scheduled T-minus lifts off"
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
                enableVibration(true)
                setBypassDnd(true)
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_WINDOW, "Launch window", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "The nudge to start a burn after you get home"
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            },
        )
    }

    fun clock(context: Context, epochSec: Long): String = DateFormat.getTimeFormat(context).format(Date(epochSec * 1000))

    fun openApp(context: Context, page: String, code: Int): PendingIntent = PendingIntent.getActivity(
        context, code,
        Intent(context, MainActivity::class.java).putExtra(MainActivity.EXTRA_PAGE, page)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun action(context: Context, action: String, code: Int, extras: (Intent) -> Unit = {}): PendingIntent =
        PendingIntent.getBroadcast(
            context, code, Intent(context, EventReceiver::class.java).setAction(action).also(extras),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun builder(context: Context, channel: String) = Notification.Builder(context, channel)
        .setSmallIcon(R.drawable.ic_flame)
        .setColor(ACCENT)

    fun ongoing(context: Context, cur: Logic.Current) {
        val nm = context.getSystemService(NotificationManager::class.java)
        val s = cur.session
        if (s == null || cur.phase == "idle") {
            nm.cancel(ID_ONGOING)
            return
        }
        channels(context)
        val b = builder(context, CH_BURN)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(true)
            .setUsesChronometer(true)
            .setChronometerCountDown(true)
            .setCategory(Notification.CATEGORY_PROGRESS)
            .setContentIntent(openApp(context, "ignite", 10))
        if (cur.phase == "countdown") {
            b.setWhen(s.optLong("starts_at") * 1000)
                .setContentTitle("T-minus · ignition at ${clock(context, s.optLong("starts_at"))}")
                .setContentText("${s.optInt("minutes")} min burn. Enjoy the break.")
                .addAction(Notification.Action.Builder(null, "Ignite now", action(context, ACTION_NOW, 11)).build())
                .addAction(Notification.Action.Builder(null, "Scrub", action(context, ACTION_SCRUB, 12)).build())
        } else {
            val total = (s.optLong("ends_at") - s.optLong("starts_at")).coerceAtLeast(1)
            val elapsed = (Logic.now() - s.optLong("starts_at")).coerceIn(0, total)
            b.setWhen(s.optLong("ends_at") * 1000)
                .setContentTitle("Burning · locked until ${clock(context, s.optLong("ends_at"))}")
                .setContentText(upNext(context) ?: "No way out. Get to work.")
                .setProgress(total.toInt(), elapsed.toInt(), false)
                .addAction(Notification.Action.Builder(null, "+15 min", action(context, ACTION_EXTEND, 13) { it.putExtra("minutes", 15) }).build())
        }
        nm.notify(ID_ONGOING, b.build())
    }

    fun alert(context: Context, title: String, text: String) {
        channels(context)
        context.getSystemService(NotificationManager::class.java).notify(
            ID_ALERT,
            builder(context, CH_ALERTS).setContentTitle(title).setContentText(text).setAutoCancel(true)
                .setContentIntent(openApp(context, "ignite", 20)).build(),
        )
    }

    fun warning(context: Context, next: JSONObject) {
        channels(context)
        val at = next.optLong("at")
        val mins = ((at - Logic.now() + 59) / 60).coerceAtLeast(1)
        context.getSystemService(NotificationManager::class.java).notify(
            ID_WARN,
            builder(context, CH_ALERTS)
                .setContentTitle("T-minus $mins")
                .setContentText("Scheduled ignition at ${clock(context, at)} · ${next.optInt("minutes")} min burn")
                .setWhen(at * 1000).setShowWhen(true).setUsesChronometer(true).setChronometerCountDown(true)
                .setTimeoutAfter((at - Logic.now() + 5) * 1000)
                .setAutoCancel(true)
                .setContentIntent(openApp(context, "ignite", 30))
                .addAction(
                    Notification.Action.Builder(
                        null, "Scrub",
                        action(context, ACTION_SKIP, 31) {
                            it.putExtra("schedule_id", next.optString("schedule_id")).putExtra("date", next.optString("date"))
                        },
                    ).build(),
                ).build(),
        )
    }

    fun cancelWarning(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(ID_WARN)

    /** Ring and light up the lock screen at liftoff. The full-screen intent opens [RingActivity]. */
    fun ring(context: Context, minutes: Int) {
        channels(context)
        val full = PendingIntent.getActivity(
            context, 40,
            Intent(context, RingActivity::class.java).putExtra("minutes", minutes)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        context.getSystemService(NotificationManager::class.java).notify(
            ID_RING,
            builder(context, CH_RING)
                .setContentTitle("Liftoff")
                .setContentText(if (minutes > 0) "$minutes min burn. Phone down." else "Get to work.")
                .setCategory(Notification.CATEGORY_ALARM)
                .setAutoCancel(true)
                .setFullScreenIntent(full, true)
                .setContentIntent(full)
                .build(),
        )
    }

    fun cancelRing(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(ID_RING)

    /** The launch-window nudge: a question with a one-tap start. */
    fun launchWindow(context: Context, cfg: JSONObject) {
        channels(context)
        val tminus = cfg.optInt("tminus", 10)
        val minutes = cfg.optInt("minutes", 50)
        val start = action(context, ACTION_LW_START, 50) { it.putExtra("minutes", minutes).putExtra("delay", tminus) }
        val label = if (tminus > 0) "Start T-minus $tminus" else "Ignite now"
        context.getSystemService(NotificationManager::class.java).notify(
            ID_WINDOW,
            builder(context, CH_WINDOW)
                .setContentTitle("Still on your phone?")
                .setContentText("You've been home a while. $label for a $minutes min burn.")
                .setStyle(Notification.BigTextStyle().bigText("You've been home a while. Light it up before the afternoon's gone — $label for a $minutes min burn."))
                .setAutoCancel(true)
                .setContentIntent(openApp(context, "ignite", 51))
                .addAction(Notification.Action.Builder(null, label, start).build())
                .build(),
        )
    }

    fun cancelLaunchWindow(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(ID_WINDOW)

    private fun upNext(context: Context): String? {
        val store = Store.get(context)
        val top = synchronized(store) { Widgets.upNext(store, 1).firstOrNull() } ?: return null
        return "Up next: ${top.optString("title")}"
    }
}
