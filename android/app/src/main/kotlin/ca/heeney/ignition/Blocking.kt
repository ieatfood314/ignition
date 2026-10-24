package ca.heeney.ignition

import org.json.JSONObject

/**
 * The shared block list (settings.block) and the rules for matching it, kept free of Android classes so
 * it can be unit-tested. Sites match their subdomains too; a site with a port only matches that port.
 */
object Blocking {
    class Config(val sites: List<String>, val apps: Map<String, String>, val games: Boolean) {
        val empty get() = sites.isEmpty() && apps.isEmpty() && !games
    }

    /** Other domains that belong to a site (subdomains are always covered). Mirrors the enforcer. */
    private val ALIASES = mapOf(
        "youtube.com" to listOf("youtu.be", "youtube-nocookie.com"),
        "reddit.com" to listOf("redd.it"),
        "facebook.com" to listOf("fb.com", "fb.me", "fb.watch", "messenger.com"),
    )

    private val SITE = Regex("^[a-z0-9-]+(\\.[a-z0-9-]+)+(:\\d{1,5})?$")
    private val HOSTLIKE = Regex("(?:[a-z][a-z0-9+.-]*://)?((?:[a-z0-9-]+\\.)+[a-z]{2,63})(:\\d{1,5})?(?=[/?#\\s]|$)", RegexOption.IGNORE_CASE)

    fun config(settings: JSONObject): Config {
        val b = settings.optJSONObject("block") ?: JSONObject()
        val sites = b.optJSONArray("sites")?.let { a -> (0 until a.length()).mapNotNull { cleanSite(a.optString(it)) } } ?: emptyList()
        val apps = LinkedHashMap<String, String>()
        b.optJSONArray("apps")?.let { a ->
            for (i in 0 until a.length()) {
                val o = a.optJSONObject(i) ?: continue
                val id = o.optString("id")
                if (id.isNotEmpty()) apps[id] = o.optString("label", id).ifEmpty { id }
            }
        }
        return Config(sites, apps, b.optBoolean("games"))
    }

    /** "https://www.YouTube.com/watch?v=x" -> "youtube.com"; null if it doesn't look like a site. */
    fun cleanSite(value: String): String? {
        var s = value.trim().lowercase().replace(Regex("^[a-z]+://"), "")
        s = s.split('/', '?', '#')[0].substringAfterLast('@').trim('.')
        if (s.startsWith("www.")) s = s.removePrefix("www.")
        return s.takeIf { SITE.matches(it) }
    }

    /** host[:port] candidates found in a piece of UI text such as a URL bar ("youtube.com/watch…"). */
    fun hostsIn(text: String): List<String> =
        HOSTLIKE.findAll(text.trim()).map { m -> m.groupValues[1].lowercase() + m.groupValues[2] }.toList()

    /** The blocked site [host] (optionally host:port) falls under, or null. */
    fun matchSite(host: String, sites: List<String>): String? {
        val h = host.lowercase().removePrefix("www.")
        val (name, port) = h.split(":", limit = 2).let { it[0] to it.getOrNull(1) }
        for (site in sites) {
            val (siteName, sitePort) = site.split(":", limit = 2).let { it[0] to it.getOrNull(1) }
            if (sitePort != null && sitePort != port) continue
            for (base in listOf(siteName) + ALIASES[siteName].orEmpty()) {
                if (name == base || name.endsWith(".$base")) return site
            }
        }
        return null
    }

    /** The blocked site shown in [text], if any. */
    fun blockedSiteIn(text: String, sites: List<String>): String? {
        if (sites.isEmpty() || text.isBlank()) return null
        for (h in hostsIn(text)) matchSite(h, sites)?.let { return it }
        return null
    }
}
