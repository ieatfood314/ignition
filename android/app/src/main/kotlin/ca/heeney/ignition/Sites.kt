package ca.heeney.ignition

/**
 * Which site a browser was showing, and when. Plain Kotlin with no Android in it, so it can be tested on a desk.
 *
 * [BlockerService] reads the browser's address bar and notes a [Mark] each time the site there changes. At
 * sync the browser's spans are cut where the marks say the site changed. Only the domain is kept, never the
 * address.
 */
data class Mark(val time: Long, val pkg: String, val site: String)

/** A site change closer than this to the last one was a page passing through (a redirect, a login hop). */
const val SETTLE = 3000L

object Sites {
    // A domain may be followed by a path, whitespace or sentence punctuation: Firefox's address bar reads
    // " m.youtube.com. Search or enter address" to a screen reader.
    private val HOSTLIKE = Regex(
        "(?:[a-z][a-z0-9+.-]*://)?((?:[a-z0-9-]+\\.)+[a-z]{2,63})(?::\\d{1,5})?(?=[/?#\\s.,;)]|$)",
        RegexOption.IGNORE_CASE,
    )
    // The server's own list, kept short: what a mobile site puts in front of the real one.
    private val NOISE = setOf("www", "m", "mobile", "amp", "touch")

    /** The domain an address bar shows, without the "www." or "m." in front, or null if it shows none. */
    fun hostIn(text: String): String? {
        val host = HOSTLIKE.find(text.trim())?.groupValues?.get(1)?.lowercase() ?: return null
        var labels = host.split('.')
        while (labels.size > 2 && labels[0] in NOISE) labels = labels.drop(1)
        return labels.joinToString(".")
    }
}

/**
 * Cuts each browser span where the site changed. A span starts on the site last noted for that browser
 * (it opens on the tab you left), unless a new one is noted within [SETTLE] of it opening. A site held
 * for less than [SETTLE] gives its time to the one after it. Spans of apps with no marks pass through.
 */
fun splitBySite(spans: List<Span>, marks: List<Mark>): List<Span> {
    if (marks.isEmpty()) return spans
    val byPkg = marks.sortedBy { it.time }.groupBy { it.pkg }
    val out = ArrayList<Span>(spans.size)
    for (span in spans) {
        val ms = byPkg[span.pkg]
        if (ms == null) {
            out.add(span)
            continue
        }
        var site = ms.lastOrNull { it.time <= span.start + SETTLE }?.site ?: ""
        var from = span.start
        for (m in ms) {
            if (m.time <= span.start + SETTLE) continue
            if (m.time >= span.end) break
            if (m.site == site) continue
            if (m.time - from < SETTLE) {
                site = m.site
                continue
            }
            out.add(Span(from, m.time, span.pkg, site))
            from = m.time
            site = m.site
        }
        out.add(Span(from, span.end, span.pkg, site))
    }
    return out
}
