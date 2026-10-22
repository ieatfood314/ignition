package ca.heeney.ignition

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.util.SizeF
import android.widget.RemoteViews
import org.json.JSONObject

/** Shared plumbing for the three widgets: size handling, the two-layer layout, tap-to-open. */
abstract class BaseWidget : AppWidgetProvider() {
    abstract val page: String
    abstract fun draw(context: Context, r: WidgetRenderer, widthDp: Float, heightDp: Float): WidgetRenderer.Layers
    abstract fun describe(context: Context): String

    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        ids.forEach { render(context, manager, it) }
    }

    override fun onAppWidgetOptionsChanged(context: Context, manager: AppWidgetManager, id: Int, options: Bundle) {
        render(context, manager, id)
    }

    fun render(context: Context, manager: AppWidgetManager, id: Int) {
        val r = WidgetRenderer(context)
        val options = manager.getAppWidgetOptions(id)
        val sizes = sizes(options)
        val views = if (sizes.isNotEmpty()) {
            RemoteViews(sizes.associateWith { build(context, r, id, it.width, it.height) })
        } else {
            val w = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 80)
            val h = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 80)
            build(context, r, id, w.toFloat(), h.toFloat())
        }
        manager.updateAppWidget(id, views)
    }

    private fun build(context: Context, r: WidgetRenderer, id: Int, w: Float, h: Float): RemoteViews {
        val layers = draw(context, r, w, h)
        return RemoteViews(context.packageName, R.layout.widget).apply {
            setImageViewBitmap(R.id.layer_mono, layers.mono)
            setImageViewBitmap(R.id.layer_red, layers.red)
            setContentDescription(R.id.root, describe(context))
            setOnClickPendingIntent(R.id.root, Notifier.openApp(context, page, 100 + id))
        }
    }

    private fun sizes(options: Bundle): List<SizeF> {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return emptyList()
        @Suppress("DEPRECATION")
        val list: List<SizeF>? = if (Build.VERSION.SDK_INT >= 33) {
            options.getParcelableArrayList(AppWidgetManager.OPTION_APPWIDGET_SIZES, SizeF::class.java)
        } else {
            options.getParcelableArrayList(AppWidgetManager.OPTION_APPWIDGET_SIZES)
        }
        return list.orEmpty().filter { it.width > 0f && it.height > 0f }.distinct().take(8)
    }
}

class IgniteWidget : BaseWidget() {
    override val page = "ignite"
    override fun draw(context: Context, r: WidgetRenderer, widthDp: Float, heightDp: Float): WidgetRenderer.Layers {
        val store = Store.get(context)
        val (cur, next) = synchronized(store) { Logic.current(store) to Logic.nextSchedule(store) }
        return r.ignite(widthDp, heightDp, cur, next, context)
    }

    override fun describe(context: Context): String {
        val store = Store.get(context)
        val cur = synchronized(store) { Logic.current(store) }
        val s = cur.session ?: return "Ignition: ready to ignite"
        return when (cur.phase) {
            "burning" -> "Ignition: burning, ${(s.optLong("ends_at") - Logic.now() + 59) / 60} minutes left"
            else -> "Ignition: T-minus ${(s.optLong("starts_at") - Logic.now() + 59) / 60} minutes"
        }
    }
}

class NextUpWidget : BaseWidget() {
    override val page = "need"
    override fun draw(context: Context, r: WidgetRenderer, widthDp: Float, heightDp: Float): WidgetRenderer.Layers {
        val store = Store.get(context)
        val (items, count) = synchronized(store) { Widgets.upNext(store, 8) to Widgets.openCount(store) }
        return r.nextUp(widthDp, heightDp, items, count)
    }

    override fun describe(context: Context): String {
        val store = Store.get(context)
        val items = synchronized(store) { Widgets.upNext(store, 3) }
        return if (items.isEmpty()) "Ignition: nothing to do" else "Next up: " + items.joinToString(", ") { it.optString("title") }
    }
}

class BurnWidget : BaseWidget() {
    override val page = "ignite"
    override fun draw(context: Context, r: WidgetRenderer, widthDp: Float, heightDp: Float): WidgetRenderer.Layers {
        val store = Store.get(context)
        return r.burn(widthDp, heightDp, synchronized(store) { Logic.stats(store) })
    }

    override fun describe(context: Context): String {
        val store = Store.get(context)
        val s = synchronized(store) { Logic.stats(store) }
        return "Burned ${s.optLong("today") / 60} minutes today, ${s.optLong("week") / 60} this week"
    }
}

object Widgets {
    private val providers = listOf(IgniteWidget::class.java, NextUpWidget::class.java, BurnWidget::class.java)

    fun updateAll(context: Context) {
        val manager = AppWidgetManager.getInstance(context)
        for (cls in providers) {
            val ids = manager.getAppWidgetIds(ComponentName(context, cls))
            if (ids.isEmpty()) continue
            val provider = cls.getDeclaredConstructor().newInstance()
            ids.forEach { runCatching { provider.render(context, manager, it) } }
        }
    }

    private fun open(store: Store) = store.items.values.filter {
        !it.optBoolean("deleted") && it.optString("list") == "need" && it.isNull("done_at")
    }

    fun openCount(store: Store) = open(store).size

    /** The need-to-do list in "smart" order (due date, then priority, then age), like the web UI. */
    fun upNext(store: Store, n: Int): List<JSONObject> = open(store).sortedWith(
        compareBy<JSONObject> { it.isNull("due") }
            .thenBy { it.stringOrNull("due") ?: "" }
            .thenByDescending { it.optInt("priority") }
            .thenBy { it.optLong("created_at") },
    ).take(n)
}
