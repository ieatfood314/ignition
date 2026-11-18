package ca.heeney.ignition

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SpansTest {
    private fun s(n: Int) = n * 1000L
    private fun on(t: Int, pkg: String, cls: String = "Main") = Event(s(t), Ev.RESUMED, pkg, cls)
    private fun off(t: Int, pkg: String, cls: String = "Main") = Event(s(t), Ev.PAUSED, pkg, cls)

    @Test fun oneAppThenAnother() {
        val b = buildSpans(listOf(
            Event(s(0), Ev.SCREEN_ON), Event(s(1), Ev.KEYGUARD_HIDDEN),
            on(1, "launcher"), off(5, "launcher"), on(5, "signal"), off(65, "signal"),
            Event(s(65), Ev.SCREEN_OFF),
        ), s(100))
        assertEquals(listOf(Span(s(1), s(5), "launcher"), Span(s(5), s(65), "signal")), b.spans)
        assertEquals(listOf(s(1)), b.pickups)
        assertEquals(s(65), b.checkpoint)
        assertFalse(b.active)
    }

    @Test fun screensInsideOneAppAreOneVisit() {
        val b = buildSpans(listOf(
            on(0, "reddit", "Feed"), off(30, "reddit", "Feed"), on(30, "reddit", "Post"),
            off(90, "reddit", "Post"), on(91, "reddit", "Feed"), Event(s(120), Ev.SCREEN_OFF),
        ), s(200))
        assertEquals(listOf(Span(s(0), s(120), "reddit")), b.spans)
    }

    @Test fun theNextScreenMayResumeBeforeTheOldOnePauses() {
        val b = buildSpans(listOf(
            on(0, "reddit", "Feed"), on(30, "reddit", "Post"), off(30, "reddit", "Feed"),
            Event(s(60), Ev.SCREEN_OFF),
        ), s(200))
        assertEquals(listOf(Span(s(0), s(60), "reddit")), b.spans)
    }

    @Test fun comingBackLaterIsANewVisit() {
        val b = buildSpans(listOf(
            on(0, "maps"), off(20, "maps"), on(50, "maps"), Event(s(80), Ev.SCREEN_OFF),
        ), s(200))
        assertEquals(listOf(Span(s(0), s(20), "maps"), Span(s(50), s(80), "maps")), b.spans)
    }

    @Test fun aSpanEndsAtItsPauseNotAtTheNextThing() {
        val b = buildSpans(listOf(
            on(0, "camera"), off(10, "camera"), Event(s(40), Ev.SCREEN_OFF),
        ), s(200))
        assertEquals(listOf(Span(s(0), s(10), "camera")), b.spans)
    }

    @Test fun whatIsOpenNowRunsToNowAndIsMarkedActive() {
        val b = buildSpans(listOf(Event(s(0), Ev.SCREEN_OFF), Event(s(9), Ev.SCREEN_ON), on(10, "youtube")), s(300))
        assertEquals(listOf(Span(s(10), s(300), "youtube")), b.spans)
        assertTrue(b.active)
        assertEquals(s(0), b.checkpoint)
    }

    @Test fun aGlanceThroughAScreenIsNotRecorded() {
        val b = buildSpans(listOf(
            on(0, "launcher"), Event(400, Ev.PAUSED, "launcher", "Main"), Event(400, Ev.RESUMED, "signal", "Main"),
            Event(s(30), Ev.SCREEN_OFF),
        ), s(200))
        assertEquals(listOf(Span(400, s(30), "signal")), b.spans)
    }

    @Test fun aPhoneThatDiedStopsWhereItsLogDid() {
        val b = buildSpans(listOf(
            on(0, "youtube"), Event(s(600), Ev.KEYGUARD_HIDDEN), Event(s(90000), Ev.STARTUP), on(90010, "launcher"),
            Event(s(90020), Ev.SCREEN_OFF),
        ), s(100000))
        assertEquals(listOf(Span(s(0), s(600), "youtube"), Span(s(90010), s(90020), "launcher")), b.spans)
    }

    @Test fun rebuildingFromTheCheckpointGivesTheSameSpans() {
        val events = listOf(
            on(0, "signal"), Event(s(50), Ev.SCREEN_OFF), Event(s(500), Ev.SCREEN_ON),
            Event(s(501), Ev.KEYGUARD_HIDDEN), on(501, "reddit"), off(560, "reddit"), on(560, "launcher"),
        )
        val whole = buildSpans(events, s(600))
        val later = buildSpans(events.filter { it.time >= whole.checkpoint!! }, s(600))
        assertEquals(whole.spans.filter { it.start >= s(50) }, later.spans)
        assertEquals(whole.pickups, later.pickups)
    }

    @Test fun anAppOpeningBehindADarkScreenWaitsForTheScreen() {
        // a blocker takes the front at bedtime with the screen already off, and is still there in the morning
        val b = buildSpans(listOf(
            on(0, "video"), Event(s(60), Ev.SCREEN_OFF), on(200, "blocker"),
            Event(s(30000), Ev.SCREEN_ON), Event(s(30002), Ev.KEYGUARD_HIDDEN), off(30010, "blocker"), on(30010, "launcher"),
            Event(s(30020), Ev.SCREEN_OFF),
        ), s(40000))
        assertEquals(listOf(
            Span(s(0), s(60), "video"), Span(s(30000), s(30010), "blocker"), Span(s(30010), s(30020), "launcher"),
        ), b.spans)
    }

    @Test fun anAppThatCameAndWentInTheDarkIsNotRecorded() {
        val b = buildSpans(listOf(
            Event(s(0), Ev.SCREEN_OFF), on(100, "alarm"), off(130, "alarm"),
            Event(s(500), Ev.SCREEN_ON), on(501, "launcher"), Event(s(520), Ev.SCREEN_OFF),
        ), s(600))
        assertEquals(listOf(Span(s(501), s(520), "launcher")), b.spans)
    }

    @Test fun anUnlockCountsAsTheScreenComingOn() {
        val b = buildSpans(listOf(
            Event(s(0), Ev.SCREEN_OFF), Event(s(100), Ev.KEYGUARD_HIDDEN), on(101, "reddit"), Event(s(160), Ev.SCREEN_OFF),
        ), s(600))
        assertEquals(listOf(Span(s(101), s(160), "reddit")), b.spans)
    }

    @Test fun nothingHappenedNothingRecorded() {
        val b = buildSpans(emptyList(), s(10))
        assertTrue(b.spans.isEmpty())
        assertNull(b.checkpoint)
        assertFalse(b.active)
    }
}
