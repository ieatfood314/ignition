package ca.heeney.ignition

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.io.File

/** Round-trips against a real ignition server. Run with IGNITION_TEST_SERVER=http://127.0.0.1:PORT (a throwaway DB!). */
class SyncTest {
    private val server = System.getenv("IGNITION_TEST_SERVER") ?: ""

    @Before
    fun needsServer() = assumeTrue("set IGNITION_TEST_SERVER to run", server.isNotEmpty())

    private fun http(method: String, path: String, body: JSONObject? = null): String {
        val c = java.net.URL(server + path).openConnection() as java.net.HttpURLConnection
        c.requestMethod = method
        if (body != null) {
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return (if (c.responseCode < 400) c.inputStream else c.errorStream).bufferedReader().readText()
    }

    private fun phone(): Store = Store.open(File.createTempFile("phone", ".json").also { it.delete() }).also { it.serverUrl = server }

    @Test
    fun fullRoundTrip() {
        // Server already has settings (as on the NAS).
        http("PUT", "/api/settings", JSONObject().put("goals", JSONObject().put("daily", 120).put("weekly", 600)))

        // A fresh phone pulls everything and must not clobber the server's settings with its defaults.
        val a = phone()
        val (err, changed) = Sync.runWith(a)
        assertNull(err)
        assertTrue(changed)
        assertEquals(120, a.settings.getJSONObject("goals").getInt("daily"))
        assertEquals(120, JSONObject(http("GET", "/api/settings")).getJSONObject("goals").getInt("daily"))

        // Igniting on the phone reaches the server (and so the enforcers).
        a.putSession(Logic.newSession("phone-burn", 25, Logic.now(), "manual"))
        assertNull(Sync.runWith(a).first)
        val state = JSONObject(http("GET", "/api/ignite"))
        assertEquals("burning", state.getString("phase"))
        assertEquals("phone-burn", state.getString("session_id"))

        // Phone settings edits push up.
        a.putSettings(Logic.cleanSettings(JSONObject().put("goals", JSONObject().put("daily", 90))))
        assertNull(Sync.runWith(a).first)
        assertEquals(90, JSONObject(http("GET", "/api/settings")).getJSONObject("goals").getInt("daily"))

        // A second phone converges on the same data; a repeat sync changes nothing.
        val b = phone()
        assertNull(Sync.runWith(b).first)
        assertEquals(a.sessions.keys, b.sessions.keys)
        assertEquals("burning", Logic.current(b).phase)
        assertFalse(Sync.runWith(b).second)
        assertTrue(b.enforcers.length() >= 0)
    }

    @Test
    fun unreachableServerIsReportedNotThrown() {
        val p = Store.open(File.createTempFile("phone", ".json").also { it.delete() }).also { it.serverUrl = "http://127.0.0.1:9" }
        val (err, changed) = Sync.runWith(p)
        assertTrue(err != null)
        assertFalse(changed)
        assertEquals(err, p.lastSyncError)
    }
}
