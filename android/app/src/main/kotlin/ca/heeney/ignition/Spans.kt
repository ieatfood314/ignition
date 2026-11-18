package ca.heeney.ignition

/**
 * Turns the phone's usage log into spans: unbroken stretches of one app in front of you.
 *
 * Plain Kotlin with no Android in it, so the rules can be tested on a desk.
 */

/** One line of the usage log, reduced to what matters here. Types are UsageEvents.Event's own numbers. */
data class Event(val time: Long, val type: Int, val pkg: String = "", val cls: String = "")

object Ev {
    const val RESUMED = 1
    const val PAUSED = 2
    const val SCREEN_ON = 15
    const val SCREEN_OFF = 16
    const val KEYGUARD_HIDDEN = 18
    const val SHUTDOWN = 26
    const val STARTUP = 27
}

/** [site] is the domain a browser showed, or "" for anything else. */
data class Span(val start: Long, val end: Long, val pkg: String, val site: String = "")

class Built(
    val spans: List<Span>,
    /** Each unlock. */
    val pickups: List<Long>,
    /** The last moment the screen went dark with nothing open: everything before it is settled for good. */
    val checkpoint: Long?,
    /** Something was in front when the log ran out, so its span ends at "now" and will grow. */
    val active: Boolean,
)

/** Shorter than this and it was a screen passing by on the way to another. */
const val MIN_SPAN = 1000L

/** One app's screens hand over to each other with a pause and a resume; this close together is one visit. */
const val HANDOVER = 2500L

fun buildSpans(events: List<Event>, now: Long): Built {
    val spans = ArrayList<Span>()
    val pickups = ArrayList<Long>()
    var pkg: String? = null
    var cls = ""
    var start = 0L
    var pausedAt = 0L  // 0 while the app is in front
    var last = 0L
    var checkpoint: Long? = null
    var screenOn = true
    // An app that came to the front behind a dark screen (an alarm, a blocker): nobody is looking yet.
    var waiting: Event? = null

    fun close(at: Long) {
        val p = pkg ?: return
        val end = if (pausedAt > 0) pausedAt else at
        if (end - start >= MIN_SPAN) spans.add(Span(start, end, p))
        pkg = null
        pausedAt = 0
    }

    fun lightUp(at: Long) {
        screenOn = true
        waiting?.let {
            close(at)
            pkg = it.pkg
            cls = it.cls
            start = at
        }
        waiting = null
    }

    for (e in events) {
        when (e.type) {
            Ev.RESUMED -> if (!screenOn) waiting = e else {
                if (pkg == e.pkg && (pausedAt == 0L || e.time - pausedAt <= HANDOVER)) {
                    pausedAt = 0
                } else {
                    close(e.time)
                    pkg = e.pkg
                    start = e.time
                }
                cls = e.cls
            }
            Ev.PAUSED -> if (!screenOn) {
                if (waiting?.pkg == e.pkg && waiting?.cls == e.cls) waiting = null
            } else if (pkg == e.pkg && cls == e.cls && pausedAt == 0L) pausedAt = e.time
            Ev.SCREEN_OFF, Ev.SHUTDOWN -> {
                close(e.time)
                checkpoint = e.time
                screenOn = false
                waiting = null
            }
            Ev.SCREEN_ON -> lightUp(e.time)
            // The phone came up without having said it went down: whatever was open ended when the log went quiet.
            Ev.STARTUP -> {
                close(last)
                screenOn = true
                waiting = null
            }
            Ev.KEYGUARD_HIDDEN -> {
                pickups.add(e.time)
                lightUp(e.time)
            }
        }
        last = e.time
    }

    val active = pkg != null && pausedAt == 0L
    val open = pkg
    if (open != null) {
        if (pausedAt > 0) close(pausedAt)
        else if (now - start >= MIN_SPAN) spans.add(Span(start, now, open))
    }
    return Built(spans, pickups, checkpoint, active)
}
