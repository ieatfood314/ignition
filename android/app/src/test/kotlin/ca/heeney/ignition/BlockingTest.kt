package ca.heeney.ignition

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BlockingTest {
    @Test
    fun cleanSiteNormalises() {
        assertEquals("youtube.com", Blocking.cleanSite("https://www.YouTube.com/watch?v=abc"))
        assertEquals("reddit.com", Blocking.cleanSite("  Reddit.com/r/all  "))
        assertEquals("nas.tail40254b.ts.net:8444", Blocking.cleanSite("http://nas.tail40254b.ts.net:8444"))
        assertNull(Blocking.cleanSite("not a site"))
        assertNull(Blocking.cleanSite("localhost"))
    }

    @Test
    fun subdomainsAndAliasesMatch() {
        val sites = listOf("youtube.com", "reddit.com")
        assertEquals("youtube.com", Blocking.matchSite("m.youtube.com", sites))
        assertEquals("youtube.com", Blocking.matchSite("music.youtube.com", sites))
        assertEquals("youtube.com", Blocking.matchSite("youtu.be", sites)) // alias
        assertEquals("reddit.com", Blocking.matchSite("old.reddit.com", sites))
        assertNull(Blocking.matchSite("notyoutube.com", sites))
        assertNull(Blocking.matchSite("example.com", sites))
    }

    @Test
    fun portOnlyMatchesThatPort() {
        val sites = listOf("nas.local:8000")
        assertEquals("nas.local:8000", Blocking.matchSite("nas.local:8000", sites))
        assertNull(Blocking.matchSite("nas.local", sites))
        assertNull(Blocking.matchSite("nas.local:9000", sites))
    }

    @Test
    fun findsBlockedSiteInAddressBarText() {
        val sites = listOf("youtube.com", "facebook.com")
        assertEquals("youtube.com", Blocking.blockedSiteIn("youtube.com/watch?v=x", sites))
        assertEquals("facebook.com", Blocking.blockedSiteIn("https://www.facebook.com/marketplace", sites))
        assertEquals("facebook.com", Blocking.blockedSiteIn("messenger.com", sites)) // alias
        assertNull(Blocking.blockedSiteIn("Search or enter address", sites))
        assertNull(Blocking.blockedSiteIn("en.wikipedia.org/wiki/YouTube", sites))
    }

    @Test
    fun configReadsBlockDocument() {
        val settings = Logic.cleanSettings(
            JSONObject().put(
                "block",
                JSONObject()
                    .put("sites", JSONArray().put("https://www.YouTube.com"))
                    .put("apps", JSONArray().put(JSONObject().put("id", "com.termux").put("label", "Termux")))
                    .put("games", true),
            ),
        )
        val cfg = Blocking.config(settings)
        assertEquals(listOf("youtube.com"), cfg.sites)
        assertEquals("Termux", cfg.apps["com.termux"])
        assertTrue(cfg.games)
        assertFalse(cfg.empty)
    }
}
