package ca.heeney.ignition

import org.json.JSONArray
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

    private fun serverItems(): Map<String, JSONObject> {
        val arr = JSONArray(http("GET", "/api/items"))
        return (0 until arr.length()).map { arr.getJSONObject(it) }.associateBy { it.getString("id") }
    }

    @Test
    fun fullRoundTrip() {
        // Server already has data and settings (as on the NAS).
        val web = JSONObject(http("POST", "/api/items", JSONObject().put("title", "From the web").put("priority", 2)))
        http("PUT", "/api/settings", JSONObject().put("goals", JSONObject().put("daily", 120).put("weekly", 600)))

        // A fresh phone pulls everything and must not clobber the server's settings with its defaults.
        val a = phone()
        val (err, changed) = Sync.runWith(a)
        assertNull(err)
        assertTrue(changed)
        assertEquals("From the web", a.items[web.getString("id")]!!.getString("title"))
        assertEquals(120, a.settings.getJSONObject("goals").getInt("daily"))
        assertEquals(120, JSONObject(http("GET", "/api/settings")).getJSONObject("goals").getInt("daily"))

        // Phone adds, edits and deletes; the server sees all of it.
        a.putItem(JSONObject().put("id", "phone-1").put("list", "want").put("title", "From the phone").put("priority", 1)
            .put("created_at", Logic.now()).put("due", JSONObject.NULL).put("estimate_min", 30).put("notes", "").put("done_at", JSONObject.NULL).put("deleted", false))
        a.putItem(JSONObject(a.items[web.getString("id")].toString()).put("title", "Edited on phone"))
        assertNull(Sync.runWith(a).first)
        assertTrue(a.dirtyItems.isEmpty())
        var items = serverItems()
        assertEquals("From the phone", items["phone-1"]!!.getString("title"))
        assertEquals("Edited on phone", items[web.getString("id")]!!.getString("title"))
        a.putItem(JSONObject(a.items["phone-1"].toString()).put("deleted", true))
        assertNull(Sync.runWith(a).first)
        items = serverItems()
        assertFalse("phone-1" in items)

        // A newer web edit wins over the phone's copy.
        Thread.sleep(5)
        val newer = JSONObject(a.items[web.getString("id")].toString()).put("title", "Edited on web again")
            .put("updated_at", System.currentTimeMillis() + 1000)
        http("POST", "/api/sync", JSONObject().put("since", 0).put("items", JSONArray().put(newer)))
        assertNull(Sync.runWith(a).first)
        assertEquals("Edited on web again", a.items[web.getString("id")]!!.getString("title"))

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
        assertEquals(a.items.keys, b.items.keys)
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
