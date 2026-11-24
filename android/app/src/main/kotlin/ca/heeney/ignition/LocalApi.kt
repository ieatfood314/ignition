package ca.heeney.ignition

import android.content.Context
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
                r == listOf("settings") && method == "PUT" -> Logic.lockSettings(store, Logic.cleanSettings(body)).also { store.putSettings(it) }
                r == listOf("limits") && method == "GET" -> { mutated = false; LocalLimits.report(context, store) }
                r == listOf("limits") && method == "PUT" && body.optBoolean("dry") -> {
                    mutated = false
                    JSONObject().put("wait", false).put("minutes", 0)
                }
                r == listOf("limits") && method == "PUT" -> LocalLimits.save(context, store, body)
                r.size == 2 && r[0] == "limits" && method == "DELETE" -> LocalLimits.delete(context, store, r[1])
                r == listOf("limits", "settings") && method == "PUT" -> {
                    mutated = false
                    JSONObject().put("settings", JSONObject().put("changes_wait", 0).put("guard", true))
                        .put("applied", true).put("at", JSONObject.NULL)
                }
                r == listOf("stats") -> { mutated = false; Logic.stats(store) }
                r == listOf("burns") -> { mutated = false; Logic.burns(store) }
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
        if (cur.phase == "burning" && !Logic.canStop(cur.session!!, Logic.endEarlyOver(store))) throw ApiError(403, "no way out")
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
