package ca.heeney.ignition

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.text.TextPaint
import android.text.TextUtils
import org.json.JSONObject
import java.io.File
import java.time.LocalDate
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Draws the home-screen widgets in the same Nothing-OS style as the T3 usage widget: two alpha masks,
 * [Layers.mono] (tinted black/white by the launcher to follow the system theme) and [Layers.red] for the
 * sparse red accents; Nothing's own dot-matrix and mono typefaces; dot-matrix bars and rings.
 */
class WidgetRenderer(context: Context) {
    class Layers(val mono: Bitmap, val red: Bitmap)

    private val density = context.resources.displayMetrics.density
    private val ndot: Typeface = systemFont("Ndot-57.otf") ?: Typeface.MONOSPACE
    private val ndotCaps: Typeface = systemFont("NDot57Caps.otf") ?: ndot
    private val mono: Typeface = systemFont("LetteraMonoLL-Regular.otf") ?: Typeface.MONOSPACE
    private val monoMedium: Typeface = systemFont("LetteraMonoLL-Medium.otf") ?: mono

    private inner class Frame(widthDp: Float, heightDp: Float) {
        val w = max(1, (widthDp * density).roundToInt())
        val h = max(1, (heightDp * density).roundToInt())
        val monoBmp: Bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ALPHA_8)
        val redBmp: Bitmap = Bitmap.createBitmap(w, h, Bitmap.Config.ALPHA_8)
        val m = Canvas(monoBmp)
        val r = Canvas(redBmp)
        fun layers() = Layers(monoBmp, redBmp)
    }

    private fun dp(v: Float) = v * density

    // ---------------------------------------------------------------- ignite (1x1)

    fun ignite(widthDp: Float, heightDp: Float, cur: Logic.Current, next: JSONObject?, ctx: Context): Layers {
        val f = Frame(widthDp, heightDp)
        val side = min(f.w, f.h).toFloat()
        val k = (min(widthDp, heightDp) / 80f).coerceIn(0.6f, 2f)
        val cx = f.w / 2f
        val cy = f.h / 2f
        val s = cur.session
        val t = Logic.now()
        when {
            cur.phase == "burning" && s != null -> {
                val total = (s.optLong("ends_at") - s.optLong("starts_at")).coerceAtLeast(1)
                val left = (s.optLong("ends_at") - t).coerceAtLeast(0)
                ring(f, cx, cy, side / 2f - dp(9f * k), dp(2.6f * k), left.toFloat() / total)
                val mins = ((left + 59) / 60).toInt()
                val big = if (mins < 100) "$mins" else "${mins / 60}:${"%02d".format(mins % 60)}"
                val bigPaint = text(ndot, dp((if (big.length <= 2) 26f else 17f) * k), 1f)
                val small = text(monoMedium, dp(6.5f * k), SECONDARY)
                val gap = dp(2f * k)
                val blockH = dp((if (big.length <= 2) 26f else 17f) * k) + gap + dp(6.5f * k)
                val top = cy - blockH / 2f
                centered(f.m, big, cx, top - bigPaint.ascent() * 0.86f, bigPaint)
                centered(f.m, if (mins < 100) "MIN LEFT" else "LEFT", cx, top + blockH - small.descent(), small)
                f.r.drawCircle(cx, cy - side / 2f + dp(9f * k), dp(2.4f * k), fill(1f)) // live dot
            }
            cur.phase == "countdown" && s != null -> {
                val left = (s.optLong("starts_at") - t).coerceAtLeast(0)
                val mins = ((left + 59) / 60).toInt()
                val big = text(ndot, dp(22f * k), 1f)
                val small = text(monoMedium, dp(6.5f * k), SECONDARY)
                centered(f.m, "T-$mins", cx, cy + dp(4f * k), big)
                centered(f.m, "IGNITION " + Notifier.clock(ctx, s.optLong("starts_at")).uppercase(), cx, cy + dp(18f * k), small)
                f.r.drawCircle(cx, cy - dp(20f * k), dp(2.4f * k), fill(1f))
            }
            else -> {
                val flame = dp(26f * k)
                f.r.drawPath(Flame.path(cx, cy - dp(8f * k), flame), fill(1f))
                val label = text(ndotCaps, dp(8.5f * k), 1f)
                centered(f.m, "IGNITE", cx, cy + flame / 2f + dp(6f * k), label)
                if (next != null && next.optLong("at") - t < 24 * 3600) {
                    val small = text(mono, dp(6f * k), SECONDARY)
                    centered(f.m, "NEXT " + Notifier.clock(ctx, next.optLong("at")).uppercase(), cx, cy + flame / 2f + dp(15f * k), small)
                }
            }
        }
        return f.layers()
    }

    private fun ring(f: Frame, cx: Float, cy: Float, radius: Float, dot: Float, fraction: Float) {
        val count = 36
        val lit = (fraction.coerceIn(0f, 1f) * count)
        for (i in 0 until count) {
            val a = -PI / 2 + 2 * PI * i / count
            val x = cx + radius * cos(a).toFloat()
            val y = cy + radius * sin(a).toFloat()
            // Remaining time is lit, clockwise from 12 o'clock; it burns down as the session runs.
            if (i < lit) f.m.drawCircle(x, y, dot / 2f, fill(1f)) else f.m.drawCircle(x, y, dot / 2f, fill(DIM))
        }
    }

    // ---------------------------------------------------------------- next up (2x2)

    fun nextUp(widthDp: Float, heightDp: Float, items: List<JSONObject>, openCount: Int): Layers {
        val f = Frame(widthDp, heightDp)
        val k = (widthDp / 150f).coerceIn(0.75f, 1.4f)
        val pad = dp(PAD * k)
        val x0 = pad
        val x1 = f.w - pad
        var y = pad
        val head = text(ndotCaps, dp(10f * k), 1f)
        val count = text(mono, dp(8f * k), SECONDARY)
        f.r.drawCircle(x0 + dp(2.5f * k), y + dp(5f * k), dp(2.5f * k), fill(1f))
        f.m.drawText("NEXT UP", x0 + dp(10f * k), y - head.ascent(), head)
        if (openCount > 0) {
            val c = "$openCount"
            f.m.drawText(c, x1 - count.measureText(c), y - head.ascent(), count)
        }
        y += dp(10f * k) + dp(9f * k)

        val title = text(monoMedium, dp(9.5f * k), 1f)
        val meta = text(mono, dp(7.5f * k), SECONDARY)
        val rowH = dp(9.5f * k) + dp(3f * k) + dp(7.5f * k) + dp(8f * k)
        if (items.isEmpty()) {
            f.m.drawText("ALL CLEAR.", x0, y - title.ascent(), title)
            f.m.drawText("NOTHING DUE.", x0, y + dp(14f * k) - meta.ascent(), meta)
            return f.layers()
        }
        for (it in items) {
            if (y + rowH - dp(8f * k) > f.h - pad + dp(2f)) break
            f.m.drawText(clip(it.optString("title"), x1 - x0, title), x0, y - title.ascent(), title)
            val my = y + dp(9.5f * k) + dp(3f * k) - meta.ascent()
            var mx = x0
            val pri = it.optInt("priority")
            if (pri > 0) {
                for (b in 0 until 3) {
                    val on = b < pri
                    (if (on) f.r else f.m).drawRect(mx, my - dp(6f * k), mx + dp(1.6f * k), my, fill(if (on) 1f else DIM))
                    mx += dp(3f * k)
                }
                mx += dp(4f * k)
            }
            val est = it.intOrNull("estimate_min")?.let { m -> estimate(m) }
            if (est != null) {
                f.m.drawText(est, mx, my, meta)
                mx += meta.measureText(est) + dp(7f * k)
            }
            it.stringOrNull("due")?.let { due ->
                val (label, hot) = dueLabel(due)
                (if (hot) f.r else f.m).drawText(label, mx, my, if (hot) text(mono, dp(7.5f * k), 1f) else meta)
            }
            y += rowH
        }
        return f.layers()
    }

    // ---------------------------------------------------------------- burn goals (2x1)

    fun burn(widthDp: Float, heightDp: Float, stats: JSONObject): Layers {
        val f = Frame(widthDp, heightDp)
        val k = min((widthDp / 150f), heightDp / 74f).coerceIn(0.7f, 1.5f)
        val pad = dp(PAD * k * 0.9f)
        val x0 = pad
        val x1 = f.w - pad
        val goals = stats.optJSONObject("goals") ?: JSONObject()
        val rows = listOf(
            Triple("TODAY", stats.optLong("today"), goals.optInt("daily")),
            Triple("WEEK", stats.optLong("week"), goals.optInt("weekly")),
        )
        val label = text(monoMedium, dp(7.5f * k), SECONDARY)
        val value = text(ndot, dp(13f * k), 1f)
        val rowH = (f.h - 2 * pad) / 2f
        rows.forEachIndexed { i, (name, sec, goal) ->
            val top = pad + i * rowH
            val baseline = top - value.ascent() * 0.9f
            val met = goal > 0 && sec >= goal * 60L
            val v = duration(sec) + if (goal > 0) "/" + duration(goal * 60L) else ""
            val vp = if (met) text(ndot, dp(13f * k), 1f) else value
            (if (met) f.r else f.m).drawText(v, x1 - vp.measureText(v), baseline, vp)
            f.m.drawText(name, x0, baseline, label)
            if (goal > 0) {
                val barTop = baseline + dp(5f * k)
                bar(f, if (met) f.r else f.m, x0, x1, barTop, dp(2.6f * k), dp(4.2f * k), dp(1.6f * k), 2, sec / (goal * 60f))
            }
        }
        return f.layers()
    }

    private fun bar(f: Frame, litCanvas: Canvas, x0: Float, x1: Float, top: Float, dot: Float, pitch: Float, rowGap: Float, rows: Int, fraction: Float) {
        val count = max(2, ((x1 - x0 - dot) / pitch).toInt() + 1)
        val step = (x1 - x0 - dot) / (count - 1)
        val filled = fraction.coerceIn(0f, 1f) * count
        val radius = dot / 2f
        for (row in 0 until rows) {
            val cy = top + row * (dot + rowGap) + radius
            for (i in 0 until count) {
                val cx = x0 + i * step + radius
                if (i + 1 <= filled) litCanvas.drawCircle(cx, cy, radius, fill(1f)) else f.m.drawCircle(cx, cy, radius, fill(DIM))
            }
        }
    }

    // ---------------------------------------------------------------- helpers

    fun composite(layers: Layers, background: Int = 0xFF1B1B1D.toInt()): Bitmap {
        val out = Bitmap.createBitmap(layers.mono.width, layers.mono.height, Bitmap.Config.ARGB_8888)
        val c = Canvas(out)
        c.drawColor(background)
        c.drawBitmap(layers.mono, 0f, 0f, Paint().apply { color = Color.WHITE })
        c.drawBitmap(layers.red, 0f, 0f, Paint().apply { color = NOTHING_RED })
        return out
    }

    private fun estimate(min: Int) = if (min < 60) "${min}M" else if (min % 60 == 0) "${min / 60}H" else "${min / 60}H${min % 60}M"

    private fun duration(sec: Long): String {
        val m = sec / 60
        return if (m < 60) "${m}M" else if (m % 60 == 0L) "${m / 60}H" else "${m / 60}H${m % 60}"
    }

    private fun dueLabel(due: String): Pair<String, Boolean> {
        val d = runCatching { LocalDate.parse(due) }.getOrNull() ?: return due to false
        val diff = java.time.temporal.ChronoUnit.DAYS.between(LocalDate.now(), d)
        val label = when {
            diff < -1 -> "${-diff}D LATE"
            diff == -1L -> "YESTERDAY"
            diff == 0L -> "TODAY"
            diff == 1L -> "TOMORROW"
            diff < 7 -> d.dayOfWeek.name.take(3)
            else -> "${d.month.name.take(3)} ${d.dayOfMonth}"
        }
        return label to (diff <= 0)
    }

    private fun centered(c: Canvas, s: String, cx: Float, baseline: Float, p: Paint) = c.drawText(s, cx - p.measureText(s) / 2f, baseline, p)

    private fun clip(s: String, room: Float, p: Paint) =
        TextUtils.ellipsize(s, TextPaint(p), max(room, 0f), TextUtils.TruncateAt.END).toString()

    private fun text(font: Typeface, size: Float, alpha: Float) = Paint(Paint.ANTI_ALIAS_FLAG or Paint.SUBPIXEL_TEXT_FLAG).apply {
        typeface = font
        textSize = size
        color = Color.WHITE
        this.alpha = (alpha * 255).roundToInt()
    }

    private fun fill(alpha: Float) = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
        this.alpha = (alpha * 255).roundToInt()
    }

    private fun systemFont(name: String): Typeface? = runCatching {
        val file = File("/system/fonts/$name")
        if (file.canRead()) Typeface.createFromFile(file) else null
    }.getOrNull()

    companion object {
        const val NOTHING_RED = 0xFFD71921.toInt()
        private const val SECONDARY = 0.55f
        private const val DIM = 0.18f
        private const val PAD = 14f
    }
}
