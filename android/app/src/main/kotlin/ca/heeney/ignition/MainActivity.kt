package ca.heeney.ignition

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.view.WindowInsets
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * Hosts the same web UI as the server (bundled from ../web as assets). The UI's fetch calls are routed
 * through [Bridge] to [LocalApi], plus a few /native/... endpoints for phone-only settings.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView
    private val handler = Handler(Looper.getMainLooper())
    private var pageLoaded = false
    private val poll = object : Runnable {
        override fun run() {
            Sync.kick(this@MainActivity, 0)
            handler.postDelayed(this, 10_000)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.BLACK
        window.navigationBarColor = Color.BLACK
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        web = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            overScrollMode = View.OVER_SCROLL_NEVER
            addJavascriptInterface(Bridge(), "IgnitionNative")
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (request.url.scheme == "file") return false
                    startActivity(Intent(Intent.ACTION_VIEW, request.url)) // links in notes open in the browser
                    return true
                }

                override fun onPageFinished(view: WebView, url: String) {
                    pageLoaded = true
                }
            }
        }
        root.addView(web, FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT)
        // Edge-to-edge is forced on Android 15; keep the page clear of the system bars and the keyboard.
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsets.CONSUMED
        }
        setContentView(root)
        web.loadUrl("file:///android_asset/index.html#" + (intent.getStringExtra(EXTRA_PAGE) ?: "ignite"))

        if (intent.getBooleanExtra("render_widgets", false)) renderWidgetPreviews()
        Notifier.channels(this)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
            !Reactor.prefs(this).getBoolean("asked_notifications", false)
        ) {
            Reactor.prefs(this).edit().putBoolean("asked_notifications", true).apply()
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_PAGE)?.let { page ->
            web.evaluateJavascript("location.hash = ${JSONObject.quote(page)}", null)
        }
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        web.resumeTimers()
        UiBus.listener = { runOnUiThread { if (pageLoaded) web.evaluateJavascript("window.ignitionRefresh && window.ignitionRefresh()", null) } }
        Reactor.update(this)
        UiBus.changed()
        handler.post(poll)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(poll)
        UiBus.listener = null
        web.onPause()
        web.pauseTimers()
    }

    @Deprecated("Predictive back isn't enabled; the web UI decides what back means.")
    override fun onBackPressed() {
        web.evaluateJavascript("window.ignitionBack ? window.ignitionBack() : false") { handled ->
            if (handled != "true") finish()
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        Reactor.update(this)
        UiBus.changed()
    }

    /** Debug: `adb shell am start -n ca.heeney.ignition/.MainActivity --ez render_widgets true` writes PNGs to Android/data/ca.heeney.ignition/files/. */
    private fun renderWidgetPreviews() {
        val r = WidgetRenderer(this)
        val store = Store.get(this)
        val (cur, next, stats, items, count) = synchronized(store) {
            listOf(Logic.current(store), Logic.nextSchedule(store), Logic.stats(store), Widgets.upNext(store, 8), Widgets.openCount(store))
        }
        val dir = getExternalFilesDir(null) ?: return
        @Suppress("UNCHECKED_CAST")
        val shots = mapOf(
            "ignite" to r.ignite(76f, 76f, cur as Logic.Current, next as JSONObject?, this),
            "next" to r.nextUp(160f, 160f, items as List<JSONObject>, count as Int),
            "burn" to r.burn(160f, 76f, stats as JSONObject),
        )
        for ((name, layers) in shots) {
            java.io.File(dir, "widget-$name.png").outputStream().use { r.composite(layers).compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        }
    }

    private fun notificationsGranted() =
        Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    // ---- phone-only settings: /native/app

    private fun appInfo(): JSONObject {
        val store = Store.get(this)
        val p = Reactor.prefs(this)
        synchronized(store) {
            return JSONObject()
                .put("server_url", store.serverUrl)
                .put(
                    "sync", JSONObject().put("last_ok", store.lastSyncOk).put("last_error", store.lastSyncError ?: JSONObject.NULL)
                        .put("last_error_at", store.lastSyncErrorAt),
                )
                .put("dnd", JSONObject().put("enabled", p.getBoolean("dnd_enabled", false)).put("granted", Reactor.dndGranted(this)))
                .put("notifications", JSONObject().put("granted", notificationsGranted()))
                .put("blocker", JSONObject().put("enabled", BlockerService.enabled(this)))
                .put(
                    "ring",
                    JSONObject().put("enabled", p.getBoolean("ring_enabled", false)).put("full_screen", canFullScreen())
                        .put("sound", RingActivity.sound(this)),
                )
                .put(
                    "launch_window",
                    JSONObject().put("home", LaunchWindow.homeGateway(this) ?: JSONObject.NULL)
                        .put("here", LaunchWindow.homeGateway(this) != null && LaunchWindow.currentWifiGateway(this) == LaunchWindow.homeGateway(this)),
                )
                .put("version", packageManager.getPackageInfo(packageName, 0).versionName)
        }
    }

    private fun canFullScreen(): Boolean =
        Build.VERSION.SDK_INT < 34 || getSystemService(NotificationManager::class.java).canUseFullScreenIntent()

    private fun appList(): JSONArray {
        val pm = packageManager
        val games = BlockerService.gamePackages(this)
        val out = ArrayList<JSONObject>()
        for (app in pm.getInstalledApplications(0)) {
            if (app.packageName == packageName) continue
            // Launchable apps the user would recognise, plus anything flagged as a game.
            val launchable = pm.getLaunchIntentForPackage(app.packageName) != null
            val game = app.packageName in games
            if (!launchable && !game) continue
            out.add(
                JSONObject().put("id", app.packageName)
                    .put("label", pm.getApplicationLabel(app).toString())
                    .put("game", game),
            )
        }
        out.sortBy { it.optString("label").lowercase() }
        return JSONArray(out)
    }

    private fun native(method: String, path: String, body: JSONObject): Any {
        val store = Store.get(this)
        return when {
            path == "/native/apps" && method == "GET" -> appList()
            path == "/native/app" && method == "GET" -> appInfo()
            path == "/native/app" && method == "PUT" -> {
                if (body.has("dnd_enabled")) {
                    val on = body.optBoolean("dnd_enabled")
                    Reactor.prefs(this).edit().putBoolean("dnd_enabled", on).apply()
                    if (on && !Reactor.dndGranted(this)) {
                        startActivity(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
                    }
                }
                if (body.has("ring_enabled")) {
                    val on = body.optBoolean("ring_enabled")
                    Reactor.prefs(this).edit().putBoolean("ring_enabled", on).apply()
                    if (on && !canFullScreen()) {
                        startActivity(
                            Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT)
                                .setData(android.net.Uri.parse("package:$packageName")),
                        )
                    }
                }
                if (body.optString("ring_sound") in RingActivity.SOUNDS) {
                    Reactor.prefs(this).edit().putString("ring_sound", body.optString("ring_sound")).apply()
                }
                if (body.optString("home_wifi") == "current" && !LaunchWindow.setHomeToCurrent(this)) {
                    throw ApiError(400, "Not on Wi-Fi right now, or no gateway found.")
                }
                appInfo()
            }
            path == "/native/app/ring-test" && method == "POST" -> {
                startActivity(Intent(this, RingActivity::class.java))
                appInfo()
            }
            path == "/native/app/blocker" && method == "POST" -> {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                Toast.makeText(this, "Turn on Ignition under Installed apps.", Toast.LENGTH_LONG).show()
                appInfo()
            }
            path == "/native/app/server" && method == "POST" -> {
                val url = Sync.normalize(body.optString("server_url"))
                if (url.isNotEmpty()) {
                    try {
                        Sync.ping(url)
                    } catch (e: Exception) {
                        throw ApiError(400, "Can't reach it: ${e.message ?: e.javaClass.simpleName}")
                    }
                }
                synchronized(store) {
                    if (url != store.serverUrl) {
                        store.serverUrl = url
                        store.resetSync()
                        store.save()
                    }
                }
                if (url.isNotEmpty()) Sync.run(this)
                Reactor.update(this)
                appInfo()
            }
            path == "/native/app/notifications" && method == "POST" -> {
                if (Build.VERSION.SDK_INT >= 33) {
                    if (shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS) ||
                        !Reactor.prefs(this).getBoolean("asked_notifications", false)
                    ) {
                        runOnUiThread { requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1) }
                    } else {
                        startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
                    }
                }
                appInfo()
            }
            else -> throw ApiError(404, "not found")
        }
    }

    inner class Bridge {
        private val api = LocalApi(this@MainActivity)

        /** Called synchronously from app.js; returns {"status": int, "body": ...}. Runs on the WebView's bridge thread. */
        @JavascriptInterface
        fun request(method: String, path: String, body: String): String {
            val json = if (body.isBlank()) JSONObject() else runCatching { JSONTokener(body).nextValue() as? JSONObject }.getOrNull() ?: JSONObject()
            return try {
                val result = if (path.startsWith("/native/")) native(method, path, json) else api.handle(method, path, json)
                JSONObject().put("status", 200).put("body", result).toString()
            } catch (e: ApiError) {
                JSONObject().put("status", e.status).put("body", JSONObject().put("error", e.message)).toString()
            } catch (e: Exception) {
                JSONObject().put("status", 500).put("body", JSONObject().put("error", e.message ?: e.javaClass.simpleName)).toString()
            }
        }
    }

    companion object {
        const val EXTRA_PAGE = "page"
    }
}
