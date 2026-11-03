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
    fun readsFirefoxScreenReaderAddressBar() {
        // Firefox 159's Compose toolbar: a View with this content description and no text.
        val sites = listOf("youtube.com")
        assertEquals("youtube.com", Blocking.blockedSiteIn(" m.youtube.com. Search or enter address", sites))
        assertEquals("youtube.com", Blocking.blockedSiteIn("youtube.com, secure", sites))
        assertNull(Blocking.blockedSiteIn(" en.wikipedia.org. Search or enter address", sites))
    }

    @Test
    fun recognisesAddressBarIds() {
        assertTrue(Blocking.isUrlBarId("ADDRESSBAR_URL_BOX"))
        assertTrue(Blocking.isUrlBarId("ADDRESSBAR_URL"))
        assertTrue(Blocking.isUrlBarId("org.mozilla.firefox:id/mozac_browser_toolbar_url_view"))
        assertTrue(Blocking.isUrlBarId("com.android.chrome:id/url_bar"))
        assertTrue(Blocking.isUrlBarId("com.duckduckgo.mobile.android:id/omnibarTextInput"))
        assertTrue(Blocking.isUrlBarId("com.sec.android.app.sbrowser:id/location_bar_edit_text"))
        assertFalse(Blocking.isUrlBarId("ADDRESSBAR_TABS_COUNTER"))
        assertFalse(Blocking.isUrlBarId("com.android.chrome:id/url_action_button"))
        assertFalse(Blocking.isUrlBarId("top_sites_list.top_site_item"))
        assertFalse(Blocking.isUrlBarId(null))
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
