package ca.heeney.ignition

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

class ApiError(val status: Int, message: String) : Exception(message)

/**
 * The app's own implementation of the server's /api endpoints, backed by [Store]. The bundled web UI
 * calls it through the JS bridge, so it behaves the same whether or not a server is configured.
 * Every mutation saves the store, re-runs [Reactor] (alarms, notifications, widgets) and kicks a sync.
 */
class LocalApi(private val context: Context) {
    private val store = Store.get(context)

    fun handle(method: String, path: String, body: JSONObject): Any {
        val parts = path.trim('/').split("/")
        if (parts.firstOrNull() != "api") throw ApiError(404, "not found")
        val r = parts.drop(1)
        val result: Any
        var mutated = true
        synchronized(store) {
            result = when {
                r == listOf("items") && method == "GET" -> { mutated = false; items() }
                r == listOf("items") && method == "POST" -> createItem(body)
                r.size == 2 && r[0] == "items" && method == "PATCH" -> updateItem(r[1], body)
                r.size == 2 && r[0] == "items" && method == "DELETE" -> deleteItem(r[1])
                r == listOf("ignite") && method == "GET" -> { mutated = false; Logic.igniteState(store) }
                r == listOf("ignite") && method == "POST" -> ignite(body.optInt("minutes", -1), body.optInt("delay", 0), "manual")
                r == listOf("ignite", "stop") -> stop()
                r == listOf("ignite", "scrub") -> scrub()
                r == listOf("ignite", "now") -> igniteNow()
                r == listOf("ignite", "extend") -> extend(body.optInt("minutes", -1))
                r == listOf("ignite", "skip") -> {
                    Logic.skip(store, body.optString("schedule_id"), body.optString("date"))
                    Logic.igniteState(store)
                }
                r == listOf("settings") && method == "GET" -> { mutated = false; Logic.cleanSettings(store.settings) }
                r == listOf("settings") && method == "PUT" -> Logic.cleanSettings(body).also { store.putSettings(it) }
                r == listOf("stats") -> { mutated = false; Logic.stats(store) }
                else -> throw ApiError(404, "not found")
            }
            if (mutated) store.save()
        }
        if (mutated) {
            Reactor.update(context)
            Sync.kick(context)
        }
        return result
    }

    // ---- items

    private fun items(): JSONArray =
        JSONArray(store.items.values.filter { !it.optBoolean("deleted") }.sortedBy { it.optLong("created_at") })

    private fun item(id: String) = store.items[id]?.takeUnless { it.optBoolean("deleted") } ?: throw ApiError(404, "no such item")

    private fun applyItemFields(target: JSONObject, body: JSONObject) {
        for (key in body.keys()) {
            when (key) {
                "title" -> {
                    val t = body.optString("title").trim()
                    if (t.isEmpty()) throw ApiError(400, "title is required")
                    target.put("title", t.take(500))
                }
                "estimate_min" -> target.put("estimate_min", body.intOrNull(key)?.coerceAtLeast(0) ?: JSONObject.NULL)
                "due" -> target.put("due", body.stringOrNull(key)?.takeIf { it.isNotEmpty() }?.take(10) ?: JSONObject.NULL)
                "priority" -> target.put("priority", body.optInt(key).coerceIn(0, 3))
                "notes" -> target.put("notes", body.optString(key).take(20000))
                "list" -> {
                    val l = body.optString(key)
                    if (l != "need" && l != "want") throw ApiError(400, "bad list")
                    target.put("list", l)
                }
                "done" -> target.put("done_at", if (body.optBoolean(key)) Logic.now() else JSONObject.NULL)
            }
        }
        if (target.optString("list") == "want") target.put("due", JSONObject.NULL)
    }

    private fun createItem(body: JSONObject): JSONObject {
        if (body.optString("title").isBlank()) throw ApiError(400, "title is required")
        val item = JSONObject()
            .put("id", Store.newId()).put("list", "need").put("title", "").put("estimate_min", JSONObject.NULL)
            .put("due", JSONObject.NULL).put("priority", 0).put("notes", "").put("created_at", Logic.now())
            .put("done_at", JSONObject.NULL).put("deleted", false)
        applyItemFields(item, body)
        store.putItem(item)
        return item
    }

    private fun updateItem(id: String, body: JSONObject): JSONObject {
        val item = JSONObject(item(id).toString())
        applyItemFields(item, body)
        store.putItem(item)
        return item
    }

    private fun deleteItem(id: String): JSONObject {
        store.items[id]?.let { store.putItem(JSONObject(it.toString()).put("deleted", true)) }
        return JSONObject().put("ok", true)
    }

    // ---- sessions

    fun ignite(minutes: Int, delay: Int, source: String): JSONObject {
        if (minutes !in 1..Logic.MAX_MINUTES) throw ApiError(400, "minutes must be 1-${Logic.MAX_MINUTES}")
        if (delay !in 0..Logic.MAX_DELAY) throw ApiError(400, "T-minus must be 0-${Logic.MAX_DELAY} minutes")
        when (Logic.current(store).phase) {
            "burning" -> throw ApiError(409, "already burning")
            "countdown" -> throw ApiError(409, "a countdown is already running")
        }
        store.putSession(Logic.newSession(Store.newId(), minutes, Logic.now() + delay * 60L, source))
        return Logic.igniteState(store)
    }

    private fun touchCurrent(phase: String, change: (JSONObject) -> Unit): JSONObject {
        val cur = Logic.current(store)
        if (cur.phase != phase) throw ApiError(409, if (phase == "countdown") "no countdown" else "nothing burning")
        val s = JSONObject(cur.session.toString())
        change(s)
        store.putSession(s)
        return Logic.igniteState(store)
    }

    fun stop(): JSONObject {
        val cur = Logic.current(store)
        if (cur.phase == "countdown") return scrub()
        if (cur.phase == "burning" && !Logic.canStop(cur.session!!)) throw ApiError(403, "no way out")
        if (cur.phase != "burning") return Logic.igniteState(store)
        return touchCurrent("burning") { it.put("stopped_at", Logic.now()) }
    }

    fun scrub(): JSONObject = touchCurrent("countdown") {
        if (!Logic.canScrub(it)) throw ApiError(403, "under an hour to liftoff: no scrubbing")
        it.put("stopped_at", Logic.now())
    }

    fun igniteNow(): JSONObject = touchCurrent("countdown") {
        val t = Logic.now()
        it.put("starts_at", t).put("ends_at", t + it.optInt("minutes") * 60L)
    }

    fun extend(minutes: Int): JSONObject {
        if (minutes !in 1..Logic.MAX_MINUTES) throw ApiError(400, "bad minutes")
        val phase = Logic.current(store).phase
        if (phase == "idle") throw ApiError(409, "nothing burning")
        return touchCurrent(phase) {
            if (it.optInt("minutes") + minutes > Logic.MAX_MINUTES) throw ApiError(400, "that's too long")
            it.put("minutes", it.optInt("minutes") + minutes).put("ends_at", it.optLong("ends_at") + minutes * 60L)
        }
    }

    /** Entry point for notification actions, widgets and Tasker; same rules as the UI. */
    fun command(action: String, minutes: Int = 0, delay: Int = 0, scheduleId: String = "", date: String = ""): String? = try {
        when (action) {
            "ignite" -> handle("POST", "/api/ignite", JSONObject().put("minutes", minutes).put("delay", delay))
            "now" -> handle("POST", "/api/ignite/now", JSONObject())
            "scrub" -> handle("POST", "/api/ignite/scrub", JSONObject())
            "extend" -> handle("POST", "/api/ignite/extend", JSONObject().put("minutes", minutes))
            "skip" -> handle("POST", "/api/ignite/skip", JSONObject().put("schedule_id", scheduleId).put("date", date))
        }
        null
    } catch (e: ApiError) {
        e.message
    }
}
