package ca.heeney.ignition

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId

class LaunchWindowTest {
    private val zone = ZoneId.systemDefault()

    /** Epoch secs for the next time it is [hm] on an enabled weekday. */
    private fun at(hm: String): Long {
        var d = Instant.now().atZone(zone).toLocalDate()
        repeat(8) {
            if (d.dayOfWeek.value in 1..5) return d.atTime(LocalTime.parse(hm)).atZone(zone).toEpochSecond()
            d = d.plusDays(1)
        }
        error("no weekday")
    }

    private fun cfg() = JSONObject()
        .put("enabled", true)
        .put("days", JSONArray().apply { (1..5).forEach { put(it) } })
        .put("start", "15:00").put("end", "17:30")
        .put("trigger", "home").put("after", 20).put("minutes", 50).put("tminus", 10)

    @Test
    fun openOnlyInsideWindow() {
        val c = cfg()
        assertTrue(LaunchWindow.isOpen(c, at("16:00")))
        assertFalse(LaunchWindow.isOpen(c, at("14:59")))
        assertFalse(LaunchWindow.isOpen(c, at("17:30")))
    }

    @Test
    fun disabledIsNeverOpen() {
        assertFalse(LaunchWindow.isOpen(cfg().put("enabled", false), at("16:00")))
    }

    @Test
    fun opensAtReturnsAFutureStart() {
        val next = LaunchWindow.opensAt(cfg(), at("09:00"))
        assertNotNull(next)
        assertTrue(next!! >= at("09:00"))
    }

    @Test
    fun excludedDayIsClosed() {
        val weekendOnly = cfg().put("days", JSONArray().put(6).put(7))
        // at("16:00") is a weekday, so a weekend-only window is closed then.
        assertFalse(LaunchWindow.isOpen(weekendOnly, at("16:00")))
    }
}
