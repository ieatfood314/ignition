package ca.heeney.ignition

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom

/**
 * All of the app's data, kept in one JSON file in the app's private storage.
 *
 * Items, sessions and settings use the same shapes as the ignition server's JSON API, so they can be
 * synced verbatim. Rows changed on this phone are remembered in [dirtyItems] / [dirtySessions] until the
 * server has acknowledged them. Callers must hold the store's monitor (`synchronized(store)`) while
 * reading or mutating it, and call [save] after mutating.
 */
class Store private constructor(private val file: File) {
    val items = LinkedHashMap<String, JSONObject>()
    val sessions = LinkedHashMap<String, JSONObject>()
    var settings: JSONObject = defaultSettings()
    var settingsUpdatedAt = 0L
    var settingsDirty = false
    val dirtyItems = HashSet<String>()
    val dirtySessions = HashSet<String>()

    /** Server revision we have seen everything up to. */
    var rev = 0L
    var serverUrl = ""
    var enforcers = JSONArray()
    var lastSyncOk = 0L
    var lastSyncError: String? = null
    var lastSyncErrorAt = 0L

    private fun load() {
        if (!file.exists()) return
        val root = runCatching { JSONObject(file.readText()) }.getOrNull() ?: return
        root.optJSONArray("items")?.let { arr -> for (i in 0 until arr.length()) arr.getJSONObject(i).let { items[it.getString("id")] = it } }
        root.optJSONArray("sessions")?.let { arr -> for (i in 0 until arr.length()) arr.getJSONObject(i).let { sessions[it.getString("id")] = it } }
        root.optJSONObject("settings")?.let { settings = Logic.cleanSettings(it) }
        settingsUpdatedAt = root.optLong("settings_updated_at")
        settingsDirty = root.optBoolean("settings_dirty")
        root.optJSONArray("dirty_items")?.let { arr -> for (i in 0 until arr.length()) dirtyItems.add(arr.getString(i)) }
        root.optJSONArray("dirty_sessions")?.let { arr -> for (i in 0 until arr.length()) dirtySessions.add(arr.getString(i)) }
        rev = root.optLong("rev")
        serverUrl = root.optString("server_url")
        enforcers = root.optJSONArray("enforcers") ?: JSONArray()
        lastSyncOk = root.optLong("last_sync_ok")
        lastSyncError = if (root.isNull("last_sync_error")) null else root.optString("last_sync_error", null)
        lastSyncErrorAt = root.optLong("last_sync_error_at")
    }

    fun save() {
        val root = JSONObject()
            .put("items", JSONArray(items.values))
            .put("sessions", JSONArray(sessions.values))
            .put("settings", settings)
            .put("settings_updated_at", settingsUpdatedAt)
            .put("settings_dirty", settingsDirty)
            .put("dirty_items", JSONArray(dirtyItems))
            .put("dirty_sessions", JSONArray(dirtySessions))
            .put("rev", rev)
            .put("server_url", serverUrl)
            .put("enforcers", enforcers)
            .put("last_sync_ok", lastSyncOk)
            .put("last_sync_error", lastSyncError ?: JSONObject.NULL)
            .put("last_sync_error_at", lastSyncErrorAt)
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(root.toString())
        tmp.renameTo(file)
    }

    fun putItem(item: JSONObject) {
        item.put("updated_at", System.currentTimeMillis())
        items[item.getString("id")] = item
        dirtyItems.add(item.getString("id"))
    }

    fun putSession(session: JSONObject) {
        session.put("updated_at", System.currentTimeMillis())
        sessions[session.getString("id")] = session
        dirtySessions.add(session.getString("id"))
    }

    fun putSettings(value: JSONObject) {
        settings = value
        settingsUpdatedAt = System.currentTimeMillis()
        settingsDirty = true
    }

    /** Forget sync bookkeeping and mark everything as ours, so it is all pushed to a (new) server. */
    fun resetSync() {
        rev = 0
        dirtyItems.addAll(items.keys)
        dirtySessions.addAll(sessions.keys)
        if (settingsUpdatedAt > 0) settingsDirty = true
        enforcers = JSONArray()
        lastSyncOk = 0
        lastSyncError = null
    }

    companion object {
        @Volatile private var instance: Store? = null

        fun get(context: Context): Store = instance ?: synchronized(this) {
            instance ?: Store(File(context.applicationContext.filesDir, "ignition.json")).also {
                it.load()
                instance = it
            }
        }

        /** A standalone store backed by [file], for unit tests. */
        fun open(file: File): Store = Store(file).also { it.load() }

        fun defaultSettings(): JSONObject = Logic.cleanSettings(JSONObject())

        private val random = SecureRandom()
        fun newId(): String = ByteArray(8).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }
    }
}
