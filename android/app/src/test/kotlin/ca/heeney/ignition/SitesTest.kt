package ca.heeney.ignition

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SitesTest {
    private fun s(n: Int) = n * 1000L
    private val ff = "org.mozilla.firefox"
    private fun mark(t: Int, site: String, pkg: String = ff) = Mark(s(t), pkg, site)

    @Test fun theDomainIsReadFromWhatTheBarSays() {
        assertEquals("youtube.com", Sites.hostIn(" m.youtube.com. Search or enter address"))
        assertEquals("en.wikipedia.org", Sites.hostIn("https://en.wikipedia.org/wiki/Light_curve"))
        assertEquals("reddit.com", Sites.hostIn("www.reddit.com/r/linux"))
        assertEquals("example.com", Sites.hostIn("example.com"))
        assertNull(Sites.hostIn("Search or enter address"))
        assertNull(Sites.hostIn("youtube"))
    }

    @Test fun addressBarIds() {
        assertTrue(Blocking.isUrlBarId("com.android.chrome:id/url_bar"))
        assertTrue(Blocking.isUrlBarId("org.mozilla.firefox:id/ADDRESSBAR_URL_BOX"))
        assertFalse(Blocking.isUrlBarId("org.mozilla.firefox:id/mozac_browser_toolbar_clear_view"))
        assertFalse(Blocking.isUrlBarId(null))
    }

    @Test fun aBrowserSpanIsCutWhereTheSiteChanged() {
        val out = splitBySite(listOf(Span(s(0), s(100), ff)), listOf(mark(1, "youtube.com"), mark(40, "reddit.com")))
        assertEquals(listOf(Span(s(0), s(40), ff, "youtube.com"), Span(s(40), s(100), ff, "reddit.com")), out)
    }

    @Test fun itOpensOnTheTabItWasLeftOn() {
        val out = splitBySite(listOf(Span(s(500), s(600), ff)), listOf(mark(10, "youtube.com")))
        assertEquals(listOf(Span(s(500), s(600), ff, "youtube.com")), out)
    }

    @Test fun aSiteNotedJustAfterOpeningIsWhereItOpened() {
        val out = splitBySite(listOf(Span(s(500), s(600), ff)), listOf(mark(10, "youtube.com"), mark(501, "github.com")))
        assertEquals(listOf(Span(s(500), s(600), ff, "github.com")), out)
    }

    @Test fun aPageThatOnlyPassedThroughGivesItsTimeToTheNext() {
        val out = splitBySite(listOf(Span(s(0), s(100), ff)),
            listOf(mark(0, "youtube.com"), mark(30, "accounts.google.com"), mark(31, "github.com")))
        assertEquals(listOf(Span(s(0), s(30), ff, "youtube.com"), Span(s(30), s(100), ff, "github.com")), out)
    }

    @Test fun theSameSiteAgainIsNoCut() {
        val out = splitBySite(listOf(Span(s(0), s(100), ff)), listOf(mark(0, "youtube.com"), mark(50, "youtube.com")))
        assertEquals(listOf(Span(s(0), s(100), ff, "youtube.com")), out)
    }

    @Test fun nothingNotedMeansNoSite() {
        val out = splitBySite(listOf(Span(s(0), s(100), ff)), listOf(mark(200, "youtube.com")))
        assertEquals(listOf(Span(s(0), s(100), ff, "")), out)
    }

    @Test fun otherAppsPassThrough() {
        val spans = listOf(Span(s(0), s(50), "signal"), Span(s(50), s(100), ff))
        val out = splitBySite(spans, listOf(mark(0, "youtube.com"), mark(60, "github.com", pkg = "com.android.chrome")))
        assertEquals(listOf(Span(s(0), s(50), "signal"), Span(s(50), s(100), ff, "youtube.com")), out)
    }
}
