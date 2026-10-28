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
    fun scrubLocksAnHourOutAfterAMinutesGrace() {
        val t = Logic.now()
        val short = Logic.newSession("a", 50, t + 30 * 60, "manual")
        assertTrue(Logic.canScrub(short, t + 59))
        assertFalse(Logic.canScrub(short, t + 60))
        val long = Logic.newSession("b", 50, t + 3 * 3600, "manual")
        assertTrue(Logic.canScrub(long, t + 2 * 3600 - 1))
        assertFalse(Logic.canScrub(long, t + 2 * 3600))
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
    fun burnsListOnlyTimeThatBurned() {
        val s = store()
        val t = Logic.now()
        s.putSession(Logic.newSession("done", 30, t - 7200, "manual"))
        s.putSession(Logic.newSession("early", 90, t - 3000, "manual").put("stopped_at", t - 2000))
        s.putSession(Logic.newSession("live", 50, t - 600, "schedule"))
        s.putSession(Logic.newSession("future", 50, t + 600, "manual"))
        s.putSession(Logic.newSession("scrubbed", 50, t - 100, "manual").put("stopped_at", t - 200))
        s.putSession(Logic.newSession("skip", 1, t - 100, "schedule").put("deleted", true))
        val burns = Logic.burns(s, t).getJSONArray("burns")
        assertEquals(listOf("done", "early", "live"), (0 until burns.length()).map { burns.getJSONObject(it).getString("id") })
        val done = burns.getJSONObject(0)
        assertEquals(t - 7200 + 1800, done.getLong("end"))
        assertFalse(done.getBoolean("early") || done.getBoolean("live"))
        assertEquals(t - 2000, burns.getJSONObject(1).getLong("end"))
        assertTrue(burns.getJSONObject(1).getBoolean("early"))
        assertEquals(t, burns.getJSONObject(2).getLong("end"))
        assertTrue(burns.getJSONObject(2).getBoolean("live"))
    }

    @Test
    fun tminusSaysTheTimeLeft() {
        val edge = 100_000L
        assertEquals("T-10 min", Logic.tminus(edge, edge - 600))
        assertEquals("T-10 min", Logic.tminus(edge, edge - 541)) // rounds up, like the widget
        assertEquals("T-9 min", Logic.tminus(edge, edge - 540))
        assertEquals("T-1 min", Logic.tminus(edge, edge - 1))
        assertEquals("T-1 min", Logic.tminus(edge, edge)) // never T-0 before liftoff
        assertEquals("T-1 h", Logic.tminus(edge, edge - 3600))
        assertEquals("T-1 h 30 min", Logic.tminus(edge, edge - 5400))
    }

    @Test
    fun ticksLandWhenTheMinuteChanges() {
        val edge = 100_000L
        assertEquals(edge - 540, Logic.nextMinute(edge, edge - 600))
        assertEquals(edge - 600, Logic.nextMinute(edge, edge - 601))
        assertEquals(edge - 60, Logic.nextMinute(edge, edge - 61))
        assertNull(Logic.nextMinute(edge, edge - 60)) // the last minute ends at liftoff, which has its own alarm
        assertNull(Logic.nextMinute(edge, edge + 5))
        var t = edge - 300
        val seen = mutableListOf(Logic.tminus(edge, t))
        while (true) { t = Logic.nextMinute(edge, t) ?: break; seen += Logic.tminus(edge, t) }
        assertEquals(listOf("T-5 min", "T-4 min", "T-3 min", "T-2 min", "T-1 min"), seen)
    }

    @Test
    fun warnsOnlyInsideTheLeadTime() {
        val next = JSONObject().put("at", 100_000L).put("lead", 5)
        assertFalse(Logic.warns(next, 100_000L - 361))
        assertTrue(Logic.warns(next, 100_000L - 300))
        assertTrue(Logic.warns(next, 100_000L - 1))
        assertFalse(Logic.warns(next, 100_000L))
        assertFalse(Logic.warns(JSONObject(next.toString()).put("lead", 0), 100_000L - 30))
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
