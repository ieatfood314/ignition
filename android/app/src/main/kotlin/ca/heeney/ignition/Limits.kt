package ca.heeney.ignition

import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.ZoneId

/**
 * Limits: how much of something is left. The same rules as the server's server/screen/limits.py, which
 * has the long description; keep the two in step. Plain Kotlin with no Android in it, so it can be tested
 * on a desk.
 *
 * Times are milliseconds. Days are 0 for Monday to 6 for Sunday. A "day" rule follows the screen-time day
 * (04:00 to 04:00); blocked hours belong to the weekday they start on. Blocked hours whose from and to are the
 * same time are the whole day.
 */
data class Iv(val a: Long, val b: Long)

data class Rule(
    val kind: String,
    val minutes: Int = 0,
    val from: String = "",
    val to: String = "",
    val days: Set<Int> = ALL_DAYS,
)

/** Hold the button [hold] seconds, then stay on the screen [wait] minutes, for [grant] minutes of use. */
data class Unlock(val hold: Int, val wait: Int, val grant: Int, val perDay: Int?)

data class Limit(
    val id: String,
    val label: String,
    val rules: List<Rule>,
    /** null: no unlocking. */
    val unlock: Unlock?,
    /** Package ids this limit covers when they show no site. */
    val apps: Set<String>,
    /** Domains it covers in a browser. */
    val sites: Set<String>,
    /** The names it covers ("youtube"), for domains the hub hasn't seen yet. */
    val names: List<String>,
    /** Time other devices have spent on it lately, as the server last said. */
    val others: List<Iv>,
    /** An unlock (from any device) has it open until then, by the server's clock. */
    val openUntil: Long? = null,
)

data class RuleState(val blocked: Boolean, val until: Long?, val left: Long?)

/** [until] is when it opens again (null: not before something changes); [left] is how long until it closes. */
data class LimitState(val blocked: Boolean, val until: Long?, val left: Long?)

val ALL_DAYS = setOf(0, 1, 2, 3, 4, 5, 6)
const val DAY_START_HOUR = 4
const val HORIZON = 25L * 3600 * 1000
private const val MIN = 60_000L

object Limits {
    fun merge(ivs: List<Iv>, gap: Long = 0): List<Iv> {
        val out = ArrayList<Iv>()
        for (iv in ivs.sortedBy { it.a }) {
            if (iv.b <= iv.a) continue
            val last = out.lastOrNull()
            if (last != null && iv.a <= last.b + gap) {
                if (iv.b > last.b) out[out.lastIndex] = Iv(last.a, iv.b)
            } else out.add(iv)
        }
        return out
    }

    fun clip(ivs: List<Iv>, lo: Long, hi: Long) = ivs.filter { it.b > lo && it.a < hi }.map { Iv(maxOf(it.a, lo), minOf(it.b, hi)) }

    fun total(ivs: List<Iv>) = ivs.sumOf { it.b - it.a }

    private fun used(usage: List<Iv>, a: Long, b: Long) = total(clip(usage, a, b))

    private fun minutesOf(hhmm: String): Int = hhmm.substringBefore(':').toInt() * 60 + hhmm.substringAfter(':').toInt()

    private fun at(date: LocalDate, hhmm: String, zone: ZoneId): Long =
        LocalDateTime.of(date, LocalTime.of(minutesOf(hhmm) / 60, minutesOf(hhmm) % 60)).atZone(zone).toInstant().toEpochMilli()

    private fun weekday(d: LocalDate) = d.dayOfWeek.value - 1

    /** The screen-time day a moment belongs to. */
    fun dayOf(now: Long, zone: ZoneId): LocalDate =
        LocalDateTime.ofInstant(Instant.ofEpochMilli(now), zone).minusHours(DAY_START_HOUR.toLong()).toLocalDate()

    fun dayStart(date: LocalDate, zone: ZoneId): Long =
        date.atTime(DAY_START_HOUR, 0).atZone(zone).toInstant().toEpochMilli()

    /** The occurrences of blocked hours that start between yesterday and tomorrow. */
    private fun instances(r: Rule, now: Long, zone: ZoneId): List<Iv> {
        val today = LocalDateTime.ofInstant(Instant.ofEpochMilli(now), zone).toLocalDate()
        val out = ArrayList<Iv>()
        for (back in -1L..1L) {
            val d = today.plusDays(back)
            if (weekday(d) !in r.days) continue
            val endDay = if (minutesOf(r.to) > minutesOf(r.from)) d else d.plusDays(1)
            out.add(Iv(at(d, r.from, zone), at(endDay, r.to, zone)))
        }
        // Blocked all day running into the next day is one closure: Monday and Tuesday all day ends Wednesday.
        return merge(out)
    }

    private fun allWeek(r: Rule) = r.from == r.to && r.days.containsAll(ALL_DAYS)

    fun rule(r: Rule, usage: List<Iv>, now: Long, zone: ZoneId): RuleState {
        when (r.kind) {
            "day" -> {
                val date = dayOf(now, zone)
                if (weekday(date) !in r.days) return RuleState(false, null, null)
                val left = r.minutes * MIN - used(usage, dayStart(date, zone), now)
                return if (left <= 0) RuleState(true, dayStart(date.plusDays(1), zone), null) else RuleState(false, null, left)
            }
            "block" -> {
                if (allWeek(r)) return RuleState(true, null, null) // blocked all week: no opening time to give
                var until: Long? = null
                var next: Long? = null
                for (iv in instances(r, now, zone)) {
                    if (now >= iv.a && now < iv.b) until = maxOf(until ?: 0L, iv.b)
                    else if (iv.a > now) next = minOf(next ?: Long.MAX_VALUE, iv.a)
                }
                return if (until != null) RuleState(true, until, null) else RuleState(false, null, next?.let { it - now })
            }
        }
        return RuleState(false, null, null)
    }

    /** [usage] is the merged time on what the limit covers, across devices, over the last [HORIZON]. */
    fun evaluate(limit: Limit, usage: List<Iv>, now: Long, zone: ZoneId = ZoneId.systemDefault()): LimitState {
        val states = limit.rules.map { rule(it, usage, now, zone) }
        val blocking = states.filter { it.blocked }
        if (blocking.isNotEmpty()) {
            val untils = blocking.map { it.until }
            return LimitState(true, if (null in untils) null else untils.maxOf { it!! }, null)
        }
        val left = states.mapNotNull { it.left }.minOrNull()
        return LimitState(false, null, left?.coerceAtLeast(0))
    }

    private val NOT_LETTERS = Regex("[^a-z0-9]")

    /** Does a span of [pkg] showing [site] ("" for none) count towards [limit]? */
    fun covers(limit: Limit, pkg: String, site: String): Boolean {
        if (site.isEmpty()) return pkg in limit.apps
        if (site in limit.sites) return true
        val labels = site.lowercase().split('.').dropLast(1)
        return limit.names.any { n -> NOT_LETTERS.replace(n.lowercase(), "").let { it.isNotEmpty() && it in labels } }
    }

    /** The phone's own spans that count towards [limit], with the other devices' time, merged. */
    fun usage(limit: Limit, own: List<Span>): List<Iv> =
        merge(own.filter { covers(limit, it.pkg, it.site) }.map { Iv(it.start, it.end) } + limit.others)

}

/** The limits as the server last gave them, with its two settings for this phone. */
class LimitSnapshot(val limits: List<Limit>, val guard: Boolean, val fetched: Long) {
    companion object {
        val EMPTY = LimitSnapshot(emptyList(), false, 0)

        /**
         * Reads GET /api/limits?device=... [games] are this phone's games: a limit on the Play category
         * covers them all, so a game the server has never seen can't get round it.
         */
        fun parse(o: JSONObject, fetched: Long, games: Set<String>): LimitSnapshot {
            val out = ArrayList<Limit>()
            val arr = o.optJSONArray("limits") ?: JSONArray()
            for (i in 0 until arr.length()) {
                val item = arr.getJSONObject(i)
                val l = item.getJSONObject("limit")
                val rules = ArrayList<Rule>()
                val rs = l.optJSONArray("rules") ?: JSONArray()
                for (j in 0 until rs.length()) {
                    val r = rs.getJSONObject(j)
                    val days = r.optJSONArray("days")?.let { d -> (0 until d.length()).map { d.getInt(it) }.toSet() } ?: ALL_DAYS
                    rules.add(Rule(r.getString("kind"), r.optInt("minutes"), r.optString("from"), r.optString("to"), days))
                }
                val u = l.optJSONObject("unlock")
                val unlock = u?.let { Unlock(it.optInt("hold", 5), it.optInt("wait", 3), it.optInt("grant", 10),
                    if (it.isNull("per_day")) null else it.optInt("per_day")) }
                val m = item.optJSONObject("match") ?: JSONObject()
                fun strings(a: JSONArray?) = a?.let { x -> (0 until x.length()).map { x.getString(it) } } ?: emptyList()
                val others = item.optJSONArray("others")?.let { x ->
                    (0 until x.length()).map { x.getJSONArray(it).let { p -> Iv(p.getLong(0), p.getLong(1)) } }
                } ?: emptyList()
                val play = "c:play" in strings(l.optJSONArray("targets"))
                val openUntil = item.optJSONObject("unlock")?.let { if (it.isNull("open_until")) null else it.optLong("open_until") }
                out.add(Limit(l.getString("id"), item.optString("label").ifEmpty { "This app" }, rules, unlock,
                    strings(m.optJSONArray("apps")).toSet() + (if (play) games else emptySet()),
                    strings(m.optJSONArray("sites")).toSet(), strings(m.optJSONArray("names")), others, openUntil))
            }
            val s = o.optJSONObject("settings") ?: JSONObject()
            return LimitSnapshot(out, s.optBoolean("guard", true), fetched)
        }
    }
}
