package ca.heeney.ignition

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * Optional two-way sync with an ignition server (POST /api/sync). Rows are merged last-writer-wins on
 * updated_at; the server's revision counter tells us what changed since our last sync. Without a server
 * URL this does nothing and the app is entirely local.
 */
object Sync {
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private var pending: ScheduledFuture<*>? = null
    private const val JOB_ID = 1

    /** Sync soon (debounced), off the calling thread. */
    fun kick(context: Context, delayMs: Long = 600) {
        val app = context.applicationContext
        synchronized(this) {
            if (pending?.isDone == false) return
            pending = executor.schedule({ run(app) }, delayMs, TimeUnit.MILLISECONDS)
        }
    }

    fun normalize(url: String): String {
        var u = url.trim().trimEnd('/')
        if (u.isEmpty()) return ""
        if (!u.startsWith("http://") && !u.startsWith("https://")) u = "http://$u"
        return u
    }

    /** Throws with a readable message if [url] isn't an ignition server. Call off the main thread. */
    fun ping(url: String) {
        val resp = request("GET", "$url/api/ping", null)
        if (!resp.optBoolean("ignition")) throw IllegalStateException("that's not an ignition server")
    }

    /** One sync round. Returns an error message, or null on success (or when sync is off). Blocking. */
    fun run(context: Context): String? {
        val (error, changed) = runWith(Store.get(context))
        if (changed) Reactor.update(context)
        if (changed || error != null) UiBus.changed()
        return error
    }

    /** The sync round itself, independent of Android so it can be unit-tested: (error, data changed). */
    fun runWith(store: Store): Pair<String?, Boolean> {
        val url: String
        val req = JSONObject()
        val pushedItems = HashMap<String, Long>()
        val pushedSessions = HashMap<String, Long>()
        var pushedSettingsAt = 0L
        synchronized(store) {
            url = store.serverUrl
            if (url.isEmpty()) return null to false
            req.put("since", store.rev)
            val items = JSONArray()
            for (id in store.dirtyItems) store.items[id]?.let { items.put(it); pushedItems[id] = it.optLong("updated_at") }
            val sessions = JSONArray()
            for (id in store.dirtySessions) store.sessions[id]?.let { sessions.put(it); pushedSessions[id] = it.optLong("updated_at") }
            req.put("items", items).put("sessions", sessions)
            if (store.settingsDirty) {
                pushedSettingsAt = store.settingsUpdatedAt
                req.put("settings", JSONObject().put("value", store.settings).put("updated_at", store.settingsUpdatedAt))
            }
        }

        val resp = try {
            request("POST", "$url/api/sync", req)
        } catch (e: Exception) {
            val msg = e.message ?: e.javaClass.simpleName
            synchronized(store) {
                if (store.serverUrl == url) {
                    store.lastSyncError = msg
                    store.lastSyncErrorAt = Logic.now()
                    store.save()
                }
            }
            return msg to false
        }

        var changed = false
        synchronized(store) {
            if (store.serverUrl != url) return null to false
            changed = merge(resp.optJSONArray("items"), store.items, store.dirtyItems, pushedItems) or
                merge(resp.optJSONArray("sessions"), store.sessions, store.dirtySessions, pushedSessions)
            resp.optJSONObject("settings")?.let { remote ->
                val at = remote.optLong("updated_at")
                if (!store.settingsDirty || at >= store.settingsUpdatedAt) {
                    store.settings = remote.optJSONObject("value") ?: Store.defaultSettings()
                    store.settingsUpdatedAt = at
                    store.settingsDirty = false
                    changed = true
                }
            }
            if (pushedSettingsAt != 0L && store.settingsUpdatedAt == pushedSettingsAt) store.settingsDirty = false
            val t = Logic.now()
            store.enforcers = JSONArray().also { out ->
                val arr = resp.optJSONArray("enforcers") ?: JSONArray()
                for (i in 0 until arr.length()) {
                    val e = arr.getJSONObject(i)
                    out.put(e.put("seen_at", t - e.optLong("age")))
                }
            }
            store.rev = resp.optLong("rev", store.rev)
            store.lastSyncOk = t
            store.lastSyncError = null
            store.save()
        }
        return null to changed
    }

    private fun merge(remote: JSONArray?, local: MutableMap<String, JSONObject>, dirty: MutableSet<String>, pushed: Map<String, Long>): Boolean {
        var changed = false
        if (remote != null) {
            for (i in 0 until remote.length()) {
                val row = remote.getJSONObject(i)
                val id = row.optString("id")
                val mine = local[id]
                val mineAt = mine?.optLong("updated_at") ?: 0L
                if (id in dirty && mineAt > row.optLong("updated_at")) continue // our newer edit is still pending
                if (mine == null || mine.toString() != row.toString()) changed = true
                local[id] = row
                dirty.remove(id)
            }
        }
        // Anything we pushed and haven't touched since has been seen by the server.
        for ((id, at) in pushed) if (local[id]?.optLong("updated_at") == at) dirty.remove(id)
        return changed
    }

    private fun request(method: String, url: String, body: JSONObject?): JSONObject {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 8000
            conn.readTimeout = 15000
            conn.setRequestProperty("Accept", "application/json")
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code >= 400) {
                val err = runCatching { JSONObject(text).optString("error") }.getOrNull()
                throw IllegalStateException(if (!err.isNullOrEmpty()) err else "HTTP $code")
            }
            return JSONObject(text)
        } finally {
            conn.disconnect()
        }
    }

    // ---- background sync every ~15 min while a server is configured

    fun schedulePeriodic(context: Context, enabled: Boolean) {
        val js = context.getSystemService(JobScheduler::class.java)
        if (!enabled) {
            js.cancel(JOB_ID)
            return
        }
        if (js.getPendingJob(JOB_ID) != null) return
        js.schedule(
            JobInfo.Builder(JOB_ID, ComponentName(context, SyncJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(15 * 60 * 1000L)
                .setPersisted(true)
                .build(),
        )
    }
}

class SyncJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Thread {
            val error = Sync.run(applicationContext)
            jobFinished(params, error != null)
        }.start()
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true
}

/** Lets the open activity know that data changed underneath it (sync, alarms, widgets). */
object UiBus {
    @Volatile var listener: (() -> Unit)? = null
    fun changed() {
        listener?.invoke()
    }
}
