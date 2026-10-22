package ca.heeney.ignition

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

fun JSONObject.longOrNull(key: String): Long? = if (!has(key) || isNull(key)) null else optLong(key)
fun JSONObject.intOrNull(key: String): Int? = if (!has(key) || isNull(key)) null else optInt(key)
fun JSONObject.stringOrNull(key: String): String? = if (!has(key) || isNull(key)) null else optString(key)

/**
 * Session, schedule and stats rules, mirroring server/ignition.py so the phone behaves the same with or
 * without a server. All functions expect the caller to hold the store's monitor. Times are unix seconds.
 */
object Logic {
    const val EARLY_STOP_OVER_MINUTES = 60
    const val MAX_MINUTES = 12 * 60
    const val MAX_DELAY = 24 * 60
    private const val SCHEDULE_WINDOW = 120L
    private val ymd = DateTimeFormatter.ofPattern("yyyyMMdd")

    fun now(): Long = System.currentTimeMillis() / 1000

    class Current(val phase: String, val session: JSONObject?)

    fun live(store: Store, t: Long): List<JSONObject> = store.sessions.values.filter {
        !it.optBoolean("deleted") && it.isNull("stopped_at") && it.optLong("ends_at") > t
    }.sortedBy { it.optLong("starts_at") }

    fun current(store: Store, t: Long = now()): Current {
        val live = live(store, t)
        val burning = live.filter { it.optLong("starts_at") <= t }
        if (burning.isNotEmpty()) return Current("burning", burning.last())
        if (live.isNotEmpty()) return Current("countdown", live.first())
        return Current("idle", null)
    }

    fun planned(s: JSONObject): Int = s.intOrNull("planned") ?: s.optInt("minutes")
    fun canStop(s: JSONObject): Boolean = planned(s) > EARLY_STOP_OVER_MINUTES

    fun newSession(id: String, minutes: Int, startsAt: Long, source: String): JSONObject = JSONObject()
        .put("id", id).put("minutes", minutes).put("planned", minutes).put("created_at", now())
        .put("starts_at", startsAt).put("ends_at", startsAt + minutes * 60L)
        .put("stopped_at", JSONObject.NULL).put("source", source).put("deleted", false)

    // ---- schedules

    fun occurrences(sched: JSONObject, startTs: Long, daysAhead: Int): List<Pair<Long, String>> {
        val zone = ZoneId.systemDefault()
        val time = runCatching { LocalTime.parse(sched.optString("time", "09:00")) }.getOrDefault(LocalTime.of(9, 0))
        val days = sched.optJSONArray("days")?.let { a -> (0 until a.length()).map { a.getInt(it) }.toSet() } ?: (1..7).toSet()
        val base = Instant.ofEpochSecond(startTs).atZone(zone).toLocalDate()
        val out = ArrayList<Pair<Long, String>>()
        for (n in 0..daysAhead) {
            val day: LocalDate = base.plusDays(n.toLong())
            if (day.dayOfWeek.value in days) {
                out.add(day.atTime(time).atZone(zone).toEpochSecond() to day.format(ymd))
            }
        }
        return out
    }

    fun scheduleUid(schedId: String, date: String) = "sched-$schedId-$date"

    private fun schedules(store: Store): List<JSONObject> {
        val arr = store.settings.optJSONArray("schedules") ?: return emptyList()
        return (0 until arr.length()).map { arr.getJSONObject(it) }.filter { it.optBoolean("enabled", true) }
    }

    /** Fire any schedule whose time has just come. Returns true if a session was created. */
    fun runSchedules(store: Store, t: Long = now()): Boolean {
        var created = false
        for (sc in schedules(store)) {
            for ((at, date) in occurrences(sc, t - SCHEDULE_WINDOW, 1)) {
                if (t < at || t >= at + SCHEDULE_WINDOW) continue
                val uid = scheduleUid(sc.optString("id"), date)
                if (store.sessions.containsKey(uid) || current(store, t).phase != "idle") continue
                store.putSession(newSession(uid, sc.optInt("minutes", 50), at, "schedule"))
                created = true
            }
        }
        return created
    }

    fun nextSchedule(store: Store, t: Long = now()): JSONObject? {
        var best: JSONObject? = null
        for (sc in schedules(store)) {
            for ((at, date) in occurrences(sc, t, 7)) {
                if (at <= t || store.sessions.containsKey(scheduleUid(sc.optString("id"), date))) continue
                if (best == null || at < best.optLong("at")) {
                    best = JSONObject().put("at", at).put("date", date).put("schedule_id", sc.optString("id"))
                        .put("minutes", sc.optInt("minutes", 50)).put("lead", sc.optInt("lead", 5))
                }
                break
            }
        }
        return best
    }

    /** Write a tombstone for one occurrence of a schedule so neither the phone nor the server fires it. */
    fun skip(store: Store, schedId: String, date: String) {
        val uid = scheduleUid(schedId, date)
        val existing = store.sessions[uid]
        if (existing != null) {
            if (existing.optLong("starts_at") > now()) store.putSession(JSONObject(existing.toString()).put("stopped_at", now()))
        } else {
            store.putSession(newSession(uid, 1, now(), "schedule").put("deleted", true))
        }
    }

    // ---- stats

    fun stats(store: Store, t: Long = now()): JSONObject {
        val zone = ZoneId.systemDefault()
        val today = Instant.ofEpochSecond(t).atZone(zone).toLocalDate()
        fun midnight(d: LocalDate) = d.atStartOfDay(zone).toEpochSecond()
        val weekStart = today.minusDays((today.dayOfWeek.value - 1).toLong())
        val first = minOf(midnight(weekStart), midnight(today.minusDays(6)))
        val burns = store.sessions.values.filter { !it.optBoolean("deleted") && it.optLong("starts_at") < t && it.optLong("ends_at") > first }
            .mapNotNull { s ->
                val end = minOf(s.optLong("ends_at"), s.longOrNull("stopped_at") ?: s.optLong("ends_at"), t)
                if (end > s.optLong("starts_at")) s.optLong("starts_at") to end else null
            }
        fun burned(a: Long, b: Long) = burns.sumOf { (s, e) -> maxOf(0L, minOf(e, b) - maxOf(s, a)) }
        val days = JSONArray()
        for (n in 6 downTo 0) {
            val d = today.minusDays(n.toLong())
            days.put(JSONObject().put("date", d.toString()).put("seconds", burned(midnight(d), midnight(d.plusDays(1)))))
        }
        val goals = store.settings.optJSONObject("goals") ?: JSONObject()
        return JSONObject()
            .put("today", burned(midnight(today), t + 1))
            .put("week", burned(midnight(weekStart), t + 1))
            .put("goals", JSONObject().put("daily", goals.optInt("daily")).put("weekly", goals.optInt("weekly")))
            .put("days", days)
    }

    // ---- state (same shape as GET /api/ignite on the server)

    fun igniteState(store: Store, t: Long = now()): JSONObject {
        val cur = current(store, t)
        val state = JSONObject()
            .put("now", t).put("phase", cur.phase).put("active", cur.phase == "burning")
            .put("enforcers", JSONArray().also { out ->
                for (i in 0 until store.enforcers.length()) {
                    val e = JSONObject(store.enforcers.getJSONObject(i).toString())
                    out.put(e.put("age", t - e.optLong("seen_at", t)))
                }
            })
            .put("session", cur.session ?: JSONObject.NULL)
            .put("next_schedule", nextSchedule(store, t) ?: JSONObject.NULL)
            .put("stats", stats(store, t))
        cur.session?.let { s ->
            state.put("session_id", s.optString("id")).put("minutes", s.optInt("minutes"))
                .put("started_at", s.optLong("starts_at")).put("ends_at", s.optLong("ends_at"))
                .put("can_stop", canStop(s))
        }
        return state
    }

    // ---- settings validation (port of clean_settings)

    fun cleanSettings(body: JSONObject): JSONObject {
        val goals = body.optJSONObject("goals") ?: JSONObject()
        val out = JSONObject().put(
            "goals", JSONObject()
                .put("daily", goals.optInt("daily").coerceIn(0, 24 * 60 * 7))
                .put("weekly", goals.optInt("weekly").coerceIn(0, 24 * 60 * 7)),
        )
        val scheds = JSONArray()
        val arr = body.optJSONArray("schedules") ?: JSONArray()
        for (i in 0 until minOf(arr.length(), 20)) {
            val sc = arr.getJSONObject(i)
            val parts = (sc.optString("time", "09:00") + ":00").split(":")
            val hh = parts[0].toIntOrNull()?.coerceIn(0, 23) ?: 9
            val mm = parts[1].toIntOrNull()?.coerceIn(0, 59) ?: 0
            val days = JSONArray()
            sc.optJSONArray("days")?.let { d -> (0 until d.length()).map { d.optInt(it) }.filter { it in 1..7 }.toSortedSet().forEach { days.put(it) } }
            if (days.length() == 0) (1..7).forEach { days.put(it) }
            scheds.put(
                JSONObject()
                    .put("id", sc.optString("id").ifEmpty { Store.newId() }.take(32))
                    .put("time", "%02d:%02d".format(hh, mm))
                    .put("days", days)
                    .put("minutes", sc.optInt("minutes", 50).coerceIn(1, MAX_MINUTES))
                    .put("lead", (if (sc.has("lead") && !sc.isNull("lead")) sc.optInt("lead") else 5).coerceIn(0, 60))
                    .put("enabled", sc.optBoolean("enabled", true)),
            )
        }
        return out.put("schedules", scheds)
    }
}
