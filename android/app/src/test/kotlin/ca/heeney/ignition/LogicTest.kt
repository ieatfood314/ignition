package ca.heeney.ignition

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

class LogicTest {
    private fun store(): Store = Store.open(File.createTempFile("ignition", ".json").also { it.delete() })

    private fun hhmm(t: Long) = DateTimeFormatter.ofPattern("HH:mm").format(Instant.ofEpochSecond(t).atZone(ZoneId.systemDefault()))

    @Test
    fun scheduleFiresOnceWithDeterministicId() {
        val s = store()
        val t = Logic.now()
        s.putSettings(Logic.cleanSettings(JSONObject().put("schedules", JSONArray().put(JSONObject().put("id", "daily").put("time", hhmm(t)).put("minutes", 20)))))
        assertTrue(Logic.runSchedules(s, t + 30))
        assertFalse(Logic.runSchedules(s, t + 40))
        val cur = Logic.current(s, t + 40)
        assertEquals("burning", cur.phase)
        assertTrue(cur.session!!.getString("id").startsWith("sched-daily-"))
        assertEquals("schedule", cur.session!!.getString("source"))
        assertEquals(1, s.sessions.size)
    }

    @Test
    fun skipMovesNextScheduleToTheFollowingDay() {
        val s = store()
        val t = Logic.now()
        s.putSettings(Logic.cleanSettings(JSONObject().put("schedules", JSONArray().put(JSONObject().put("id", "x").put("time", hhmm(t + 3600)).put("minutes", 30)))))
        val first = Logic.nextSchedule(s, t)!!
        Logic.skip(s, "x", first.getString("date"))
        val second = Logic.nextSchedule(s, t)!!
        assertNotEquals(first.getString("date"), second.getString("date"))
        assertTrue(kotlin.math.abs(second.getLong("at") - first.getLong("at") - 86400) <= 3600)
        // The tombstone must not count as a burn or as a live session.
        assertEquals("idle", Logic.current(s, t).phase)
    }

    @Test
    fun countdownThenBurningAndEarlyStopUsesPlannedLength() {
        val s = store()
        val t = Logic.now()
        val sess = Logic.newSession("a", 30, t + 300, "manual")
        s.putSession(sess)
        assertEquals("countdown", Logic.current(s, t).phase)
        assertEquals("burning", Logic.current(s, t + 301).phase)
        // Extending to 80 min doesn't unlock "end early": planned stays 30.
        s.putSession(JSONObject(sess.toString()).put("minutes", 80))
        assertFalse(Logic.canStop(s.sessions["a"]!!))
        assertTrue(Logic.canStop(Logic.newSession("b", 90, t, "manual")))
    }

    @Test
    fun statsCountOnlyBurnedTime() {
        val s = store()
        val t = Logic.now()
        s.putSession(Logic.newSession("a", 50, t - 600, "manual")) // burning for 10 min
        s.putSession(Logic.newSession("b", 50, t + 600, "manual")) // future countdown: nothing burned
        s.putSession(Logic.newSession("c", 1, t - 100, "schedule").put("deleted", true))
        val st = Logic.stats(s, t)
        assertEquals(600L, st.getLong("week"))
        assertEquals(7, st.getJSONArray("days").length())
    }

    @Test
    fun cleanSettingsClampsAndDefaults() {
        val out = Logic.cleanSettings(
            JSONObject().put("goals", JSONObject().put("daily", -5).put("weekly", 999999))
                .put("schedules", JSONArray().put(JSONObject().put("time", "25:99").put("days", JSONArray().put(9)).put("minutes", 0))),
        )
        assertEquals(0, out.getJSONObject("goals").getInt("daily"))
        assertEquals(24 * 60 * 7, out.getJSONObject("goals").getInt("weekly"))
        val sc = out.getJSONArray("schedules").getJSONObject(0)
        assertEquals("23:59", sc.getString("time"))
        assertEquals(7, sc.getJSONArray("days").length())
        assertEquals(1, sc.getInt("minutes"))
        assertEquals(5, sc.getInt("lead"))
        assertTrue(sc.getString("id").isNotEmpty())
    }

    @Test
    fun storeRoundTripsThroughDisk() {
        val f = File.createTempFile("ignition", ".json").also { it.delete() }
        val a = Store.open(f)
        a.putItem(JSONObject().put("id", "i1").put("list", "need").put("title", "Essay").put("due", JSONObject.NULL).put("created_at", 1))
        a.serverUrl = "http://x"
        a.save()
        val b = Store.open(f)
        assertEquals("Essay", b.items["i1"]!!.getString("title"))
        assertTrue(b.items["i1"]!!.isNull("due"))
        assertTrue("i1" in b.dirtyItems)
        assertEquals("http://x", b.serverUrl)
        assertNull(b.lastSyncError)
        assertNotNull(b.settings.optJSONObject("goals"))
    }
}
