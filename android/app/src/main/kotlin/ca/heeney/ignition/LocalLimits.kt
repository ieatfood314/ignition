package ca.heeney.ignition

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Burn limits kept on the phone when it has no server. Other limit rules still belong to the server. */
object LocalLimits {
    private val lock = Any()
    @Volatile private var cached: LimitSnapshot? = null
    @Volatile private var cachedAt = 0L
    private val SITE = Regex("^[a-z0-9-]+(\\.[a-z0-9-]+)+(:\\d{1,5})?$")
    private val PROGRAM = Regex("^[A-Za-z0-9._+-]{1,64}$")
    private val PACKAGE = Regex("^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z0-9_]+)+$")
    private val NAME = Regex("^n:.{1,200}$")
    private val CATEGORY = Regex("^c:[a-z][a-z0-9-]{0,63}$")

    private fun file(ctx: Context) = File(ctx.applicationContext.filesDir, "burn-limits.json")

    private fun read(ctx: Context): MutableList<JSONObject> {
        val f = file(ctx)
        if (!f.exists()) return mutableListOf()
        val arr = runCatching { JSONArray(f.readText()) }.getOrNull() ?: return mutableListOf()
        return (0 until arr.length()).mapTo(mutableListOf()) { arr.getJSONObject(it) }
    }

    private fun write(ctx: Context, limits: List<JSONObject>) {
        cached = null
        val f = file(ctx)
        val tmp = File(f.path + ".tmp")
        tmp.writeText(JSONArray(limits).toString())
        tmp.renameTo(f)
    }

    private fun legacyTargets(block: JSONObject): Pair<List<String>, JSONObject> {
        val targets = mutableListOf<String>()
        val labels = JSONObject()
        fun add(target: String, label: String? = null) {
            if (target !in targets) targets.add(target)
            if (label != null) labels.put(target, label)
        }
        block.optJSONArray("sites")?.let { a ->
            for (i in 0 until a.length()) {
                val site = Blocking.cleanSite(a.optString(i)) ?: continue
                add("s:$site", site)
            }
        }
        block.optJSONArray("apps")?.let { a ->
            for (i in 0 until a.length()) {
                val app = a.optJSONObject(i) ?: continue
                val id = app.optString("id")
                if (PACKAGE.matches(id)) add("a:$id", app.optString("label", id).ifEmpty { id })
            }
        }
        block.optJSONArray("programs")?.let { a ->
            for (i in 0 until a.length()) {
                val program = a.optString(i)
                if (PROGRAM.matches(program)) add("p:$program", program)
            }
        }
        if (block.optBoolean("games")) add("g:phone", "Phone games")
        if (block.optBoolean("pc_games")) add("g:computer", "Computer games")
        return targets to labels
    }

    /** Carry the old standalone burn block list into the unified model once. */
    private fun migrate(ctx: Context, store: Store) {
        val block = store.settings.optJSONObject("block") ?: return
        synchronized(store) {
            val (targets, labels) = legacyTargets(block)
            if (targets.isNotEmpty()) synchronized(lock) {
                val limits = read(ctx)
                for (start in targets.indices step 60) {
                    val part = targets.drop(start).take(60)
                    val id = if (start == 0) "l-burning" else "l-" + Store.newId().take(8)
                    if (limits.any { it.optString("id") == id }) continue
                    val partLabels = JSONObject()
                    for (target in part) labels.optString(target).takeIf { it.isNotEmpty() }?.let { partLabels.put(target, it) }
                    limits.add(JSONObject().put("id", id).put("name", "Burn blocklist" + if (start == 0) "" else " ${start / 60 + 1}")
                        .put("targets", JSONArray(part)).put("labels", partLabels)
                        .put("rules", JSONArray().put(JSONObject().put("kind", "burn"))).put("unlock", JSONObject.NULL))
                }
                write(ctx, limits)
            }
            val settings = Logic.cleanSettings(store.settings)
            settings.remove("block")
            store.putSettings(settings)
            store.save()
        }
    }

    private fun clean(body: JSONObject): JSONObject {
        val targets = mutableListOf<String>()
        val given = body.optJSONArray("targets") ?: JSONArray()
        for (i in 0 until minOf(given.length(), 60)) {
            val t = given.optString(i).trim()
            val valid = when {
                t.startsWith("s:") -> SITE.matches(t.substring(2))
                t.startsWith("a:") -> PACKAGE.matches(t.substring(2))
                t.startsWith("p:") -> PROGRAM.matches(t.substring(2))
                t in setOf("g:phone", "g:computer") -> true
                else -> NAME.matches(t) || CATEGORY.matches(t)
            }
            if (!valid) throw ApiError(400, "A target doesn't look right.")
            if (t !in targets) targets.add(t)
        }
        if (targets.isEmpty()) throw ApiError(400, "Pick at least one app, site or category.")
        val rules = body.optJSONArray("rules") ?: JSONArray()
        if (rules.length() == 0 || (0 until rules.length()).any { rules.getJSONObject(it).optString("kind") != "burn" }) {
            throw ApiError(400, "Without a server, limits can block while burning only.")
        }
        val id = body.optString("id").takeIf { Regex("^l-[a-z0-9]{4,16}$").matches(it) } ?: "l-" + Store.newId().take(8)
        val labels = JSONObject()
        val oldLabels = body.optJSONObject("labels") ?: JSONObject()
        for (t in targets) oldLabels.optString(t).takeIf { it.isNotEmpty() }?.let { labels.put(t, it.take(80)) }
        return JSONObject().put("id", id).put("name", body.optString("name").trim().take(60))
            .put("targets", JSONArray(targets)).put("labels", labels)
            .put("rules", JSONArray().put(JSONObject().put("kind", "burn")))
            .put("unlock", body.opt("unlock") ?: JSONObject.NULL)
    }

    private fun all(ctx: Context, store: Store): List<JSONObject> {
        migrate(ctx, store)
        return synchronized(lock) { read(ctx) }
    }

    private fun label(limit: JSONObject): String {
        limit.optString("name").takeIf { it.isNotEmpty() }?.let { return it }
        val labels = limit.optJSONObject("labels") ?: JSONObject()
        val names = limit.optJSONArray("targets")?.let { a ->
            (0 until a.length()).map { i ->
                val t = a.getString(i)
                labels.optString(t).ifEmpty {
                    when (t) {
                        "g:phone" -> "Phone games"
                        "g:computer" -> "Computer games"
                        else -> t.substring(2).replaceFirstChar { it.uppercase() }
                    }
                }
            }
        }.orEmpty()
        return names.take(3).joinToString(", ") + if (names.size > 3) " and ${names.size - 3} more" else ""
    }

    fun report(ctx: Context, store: Store): JSONObject {
        val out = JSONArray()
        val now = System.currentTimeMillis()
        val cur = synchronized(store) { Logic.current(store) }
        val burning = cur.phase == "burning"
        val endsAt = if (burning) cur.session!!.optLong("ends_at") else null
        for (limit in all(ctx, store)) {
            val targets = limit.optJSONArray("targets") ?: JSONArray()
            fun targets(prefix: String) = JSONArray().also { a ->
                for (i in 0 until targets.length()) targets.getString(i).takeIf { it.startsWith(prefix) }?.let { a.put(it.substring(2)) }
            }
            val rule = JSONObject().put("kind", "burn").put("active", burning).put("used", 0)
                .put("allowed", JSONObject.NULL).put("blocked", burning).put("until", endsAt ?: JSONObject.NULL)
                .put("left", JSONObject.NULL)
            val state = JSONObject().put("rules", JSONArray().put(rule)).put("blocked", burning)
                .put("until", endsAt ?: JSONObject.NULL).put("left", JSONObject.NULL)
            val unlock = limit.optJSONObject("unlock")
            val unlockState = JSONObject().put("per_day", unlock?.opt("per_day") ?: 0).put("used", 0)
                .put("left", unlock?.opt("per_day") ?: JSONObject.NULL).put("open_until", JSONObject.NULL)
                .put("times", JSONArray()).put("pending", JSONObject.NULL)
            out.put(JSONObject().put("limit", limit).put("pending", JSONObject.NULL).put("pending_at", JSONObject.NULL)
                .put("deleting", false).put("created", 0).put("state", state).put("label", label(limit))
                .put("unlock", unlockState)
                .put("match", JSONObject().put("apps", targets("a:")).put("sites", targets("s:")).put("names", targets("n:")))
                .put("others", JSONArray()))
        }
        val cats = JSONArray()
        val defaults = listOf(
            listOf("make", "Make", "#ffffff", "Writing, building, coding"),
            listOf("talk", "Talk", "#a3a3a3", "Messages, mail, calls"),
            listOf("other", "Other", "#575757", "Settings, files, home screen"),
            listOf("browse", "Browse", "#6e2219", "Other web pages"),
            listOf("watch", "Watch", "#a92e1f", "Video, feeds, reading"),
            listOf("play", "Play", "#e23a29", "Games"),
        )
        defaults.forEach { c -> cats.put(JSONObject().put("id", c[0]).put("name", c[1]).put("color", c[2]).put("about", c[3]).put("custom", false)) }
        val burn = if (cur.phase == "idle") JSONObject.NULL else JSONObject().put("phase", cur.phase)
            .put("starts_at", cur.session!!.optLong("starts_at")).put("ends_at", cur.session!!.optLong("ends_at"))
        return JSONObject().put("limits", out).put("settings", JSONObject().put("changes_wait", 0).put("guard", true))
            .put("now", now).put("category_defs", cats).put("settings_pending", JSONObject.NULL)
            .put("settings_at", JSONObject.NULL).put("day_start_hour", DAY_START_HOUR).put("burn", burn).put("local", true)
    }

    fun save(ctx: Context, store: Store, body: JSONObject): JSONObject {
        val new = clean(body)
        val burnLocked = synchronized(store) { Logic.blockLocked(store) }
        synchronized(lock) {
            val limits = read(ctx)
            val at = limits.indexOfFirst { it.optString("id") == new.optString("id") }
            if (at >= 0 && burnLocked) {
                // A burn already in force can gain targets, never lose one.
                val old = limits[at]
                val targets = old.getJSONArray("targets")
                val have = (0 until targets.length()).map { targets.getString(it) }.toMutableSet()
                val additions = new.getJSONArray("targets")
                for (i in 0 until additions.length()) if (have.add(additions.getString(i))) targets.put(additions.getString(i))
                new.put("targets", targets)
                val labels = JSONObject(old.optJSONObject("labels")?.toString() ?: "{}")
                val newer = new.optJSONObject("labels") ?: JSONObject()
                newer.keys().forEach { labels.put(it, newer.get(it)) }
                new.put("labels", labels)
                new.put("unlock", old.opt("unlock") ?: JSONObject.NULL)
            }
            if (at >= 0) limits[at] = new else limits.add(new)
            write(ctx, limits)
        }
        BlockerService.recheck()
        return JSONObject().put("limit", new).put("applied", true).put("at", JSONObject.NULL)
    }

    fun delete(ctx: Context, store: Store, id: String): JSONObject {
        if (synchronized(store) { Logic.blockLocked(store) }) throw ApiError(403, "Burn blocks can't be removed during a burn.")
        synchronized(lock) { write(ctx, read(ctx).filterNot { it.optString("id") == id }) }
        BlockerService.recheck()
        return JSONObject().put("applied", true).put("at", JSONObject.NULL)
    }

    fun snapshot(ctx: Context, store: Store): LimitSnapshot {
        if (System.currentTimeMillis() - cachedAt < 10 * 60_000L) cached?.let { return it }
        // Do not hold LocalLimits.lock while report() takes the store lock: LocalApi takes them the other way.
        val snap = LimitSnapshot.parse(report(ctx, store), System.currentTimeMillis(), BlockerService.gamePackages(ctx))
        return synchronized(lock) {
            cached = snap
            cachedAt = System.currentTimeMillis()
            snap
        }
    }

    /** Move local burn limits onto a newly connected server; keep them locally unless every save applies. */
    fun upload(ctx: Context, store: Store, url: String) {
        val limits = all(ctx, store)
        val existing = Http.get("$url/api/limits").optJSONArray("limits") ?: JSONArray()
        fun alreadyThere(limit: JSONObject): Boolean {
            val targets = limit.getJSONArray("targets").let { a -> (0 until a.length()).map { a.getString(it) }.toSet() }
            for (i in 0 until existing.length()) {
                val other = existing.getJSONObject(i).getJSONObject("limit")
                val otherTargets = other.getJSONArray("targets").let { a -> (0 until a.length()).map { a.getString(it) }.toSet() }
                val rules = other.optJSONArray("rules") ?: JSONArray()
                val oneBurn = rules.length() == 1 && rules.getJSONObject(0).optString("kind") == "burn"
                if (other.optString("name") == limit.optString("name") && otherTargets == targets && oneBurn) return true
            }
            return false
        }
        for (limit in limits) {
            if (alreadyThere(limit)) continue
            // Never overwrite a server's unrelated l-burning. Fresh ids also make retries safe.
            val upload = JSONObject(limit.toString()).put("id", "l-" + Store.newId().take(8))
            val response = JSONObject(Http.send("PUT", "$url/api/limits", upload.toString()))
            if (!response.optBoolean("applied")) throw ApiError(409, "The server hasn't applied the burn limit yet. Try again later.")
        }
        synchronized(lock) {
            cached = null
            file(ctx).delete()
        }
    }
}
