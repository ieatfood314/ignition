package ca.heeney.ignition

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

/** The same cases as tests/test_limits.py, so the phone and the hub agree. */
class LimitsTest {
    private val zone = ZoneId.of("America/Vancouver")
    // 2026-03-09 is a Monday.
    private fun at(day: Int, h: Int, m: Int = 0) =
        LocalDate.of(2026, 3, day).atTime(h, m).atZone(zone).toInstant().toEpochMilli()
    private fun lim(vararg rules: Rule) = Limit("l-test", "YouTube", rules.toList(), Unlock(5, 3, 10, null),
        setOf("com.google.android.youtube"), setOf("m.youtube.com"), listOf("youtube"), emptyList())
    private fun eval(l: Limit, use: List<Iv>, now: Long) = Limits.evaluate(l, use, now, zone)
    private val min = 60_000L

    @Test fun dayAllowance() {
        val l = lim(Rule("day", minutes = 60))
        val use = mutableListOf(Iv(at(9, 9), at(9, 9, 40)))
        eval(l, use, at(9, 12)).let { assertFalse(it.blocked); assertEquals(20 * min, it.left) }
        use.add(Iv(at(9, 13), at(9, 13, 20)))
        eval(l, use, at(9, 13, 20)).let { assertTrue(it.blocked); assertEquals(at(10, 4), it.until) }
    }

    @Test fun dayCountsFromFour() {
        val l = lim(Rule("day", minutes = 30))
        val use = listOf(Iv(at(9, 23), at(10, 3)))
        assertTrue(eval(l, use, at(10, 3)).blocked)
        assertFalse(eval(l, use, at(10, 5)).blocked)
    }

    @Test fun dayOnlyOnItsDays() {
        val l = lim(Rule("day", minutes = 0, days = setOf(5, 6)))
        assertFalse(eval(l, emptyList(), at(9, 12)).blocked)
        assertTrue(eval(l, emptyList(), at(14, 12)).blocked)
    }

    @Test fun rollingWindow() {
        val l = lim(Rule("every", minutes = 30, hours = 4))
        val use = listOf(Iv(at(9, 8), at(9, 8, 20)), Iv(at(9, 10), at(9, 10, 10)))
        eval(l, use, at(9, 10, 10)).let { assertTrue(it.blocked); assertEquals(at(9, 12, 5), it.until) }
        assertFalse(eval(l, use, at(9, 12, 6)).blocked)
    }

    @Test fun closedHoursAcrossMidnight() {
        val l = lim(Rule("block", from = "22:00", to = "07:00"))
        eval(l, emptyList(), at(9, 23)).let { assertTrue(it.blocked); assertEquals(at(10, 7), it.until) }
        assertTrue(eval(l, emptyList(), at(10, 6, 59)).blocked)
        eval(l, emptyList(), at(9, 21)).let { assertFalse(it.blocked); assertEquals(60 * min, it.left) }
    }

    @Test fun closedBelongsToTheDayItStarts() {
        val l = lim(Rule("block", from = "22:00", to = "07:00", days = setOf(4)))
        assertTrue(eval(l, emptyList(), at(14, 2)).blocked)
        assertFalse(eval(l, emptyList(), at(15, 2)).blocked)
    }

    @Test fun windowAllowance() {
        val l = lim(Rule("window", minutes = 20, from = "09:00", to = "17:00", days = setOf(0, 1, 2, 3, 4)))
        val use = listOf(Iv(at(9, 8), at(9, 9, 10)), Iv(at(9, 12), at(9, 12, 10)))
        eval(l, use, at(9, 12, 10)).let { assertTrue(it.blocked); assertEquals(at(9, 17), it.until) }
        assertFalse(eval(l, use, at(9, 17, 30)).blocked)
    }

    @Test fun stretchNeedsARealBreak() {
        val l = lim(Rule("stretch", minutes = 20, rest = 30))
        val use = listOf(Iv(at(9, 9), at(9, 9, 15)), Iv(at(9, 9, 25), at(9, 9, 30)))
        eval(l, use, at(9, 9, 30)).let { assertTrue(it.blocked); assertEquals(at(9, 10), it.until) }
        eval(l, use, at(9, 10)).let { assertFalse(it.blocked); assertEquals(20 * min, it.left) }
    }

    @Test fun tightestRuleWins() {
        val l = lim(Rule("day", minutes = 120), Rule("every", minutes = 30, hours = 2))
        assertEquals(20 * min, eval(l, listOf(Iv(at(9, 9), at(9, 9, 10))), at(9, 9, 10)).left)
    }

    @Test fun whatALimitCovers() {
        val l = lim(Rule("day", minutes = 60))
        assertTrue(Limits.covers(l, "com.google.android.youtube", ""))
        assertTrue(Limits.covers(l, "org.mozilla.firefox", "m.youtube.com"))
        assertTrue(Limits.covers(l, "org.mozilla.firefox", "youtube.com"))
        assertFalse(Limits.covers(l, "org.mozilla.firefox", ""))
        assertFalse(Limits.covers(l, "org.mozilla.firefox", "example.com"))
    }

    @Test fun ownTimeAndOtherDevicesCountOnce() {
        val l = lim(Rule("day", minutes = 60)).copy(others = listOf(Iv(at(9, 9), at(9, 9, 30))))
        val own = listOf(Span(at(9, 9, 20), at(9, 9, 50), "com.google.android.youtube"), Span(at(9, 10), at(9, 11), "org.mozilla.firefox"))
        assertEquals(50 * min, Limits.total(Limits.usage(l, own)))
    }

    @Test fun sameFromAndToIsAllDay() {
        val l = lim(Rule("block", from = "00:00", to = "00:00", days = setOf(0)))
        eval(l, emptyList(), at(9, 15)).let { assertTrue(it.blocked); assertEquals(at(10, 0), it.until) }
        assertFalse(eval(l, emptyList(), at(10, 1)).blocked)
    }

    @Test fun twoWholeDaysAreOneClosure() {
        val l = lim(Rule("block", from = "00:00", to = "00:00", days = setOf(0, 1)))
        eval(l, emptyList(), at(9, 15)).let { assertTrue(it.blocked); assertEquals(at(11, 0), it.until) }
    }

    @Test fun closedAllWeekHasNoOpening() {
        val l = lim(Rule("block", from = "00:00", to = "00:00"))
        eval(l, emptyList(), at(9, 15)).let { assertTrue(it.blocked); assertEquals(null, it.until) }
    }

    @Test fun readsTheServersReport() {
        val o = org.json.JSONObject("""{"limits":[{"label":"Play","limit":{"id":"l-1","targets":["c:play"],
            "rules":[{"kind":"block","from":"01:00","to":"23:00","days":[0,1,2,3,4,5,6]}],
            "unlock":{"hold":5,"wait":120,"grant":10,"per_day":3}},
            "unlock":{"open_until":1700000000000},
            "match":{"apps":["com.mojang.minecraftpe"],"sites":["chess.com"],"names":[]},"others":[[1,2]]}],
            "settings":{"guard":true,"changes_wait":15}}""")
        val snap = LimitSnapshot.parse(o, 5, setOf("com.new.game"))
        val l = snap.limits.single()
        assertTrue(snap.guard)
        assertEquals(setOf("com.mojang.minecraftpe", "com.new.game"), l.apps)
        assertEquals(1700000000000L, l.openUntil)
        assertEquals(Unlock(5, 120, 10, 3), l.unlock)
        assertEquals(listOf(Iv(1, 2)), l.others)
        assertTrue(Limits.covers(l, "com.new.game", ""))
    }
}
