package ca.heeney.ignition

import android.app.Activity
import android.graphics.Color
import android.media.AudioManager
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Shown full-screen over the lock screen when a T-minus lifts off and "ring at liftoff" is on. Plays the
 * phone's ringtone until you look and tap. Launched by a full-screen-intent notification so it can wake
 * the screen; the notification also carries the text, so the lock screen shows it either way.
 */
class RingActivity : Activity() {
    private var ringtone: Ringtone? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                    WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val minutes = intent.getIntExtra("minutes", 0)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.BLACK)
            setPadding(48, 48, 48, 48)
        }
        root.addView(TextView(this).apply {
            text = "Liftoff"
            setTextColor(Notifier.ACCENT)
            textSize = 40f
            gravity = Gravity.CENTER
        })
        root.addView(TextView(this).apply {
            text = if (minutes > 0) "$minutes minute burn. Phone down — get to work." else "Get to work."
            setTextColor(Color.WHITE)
            textSize = 17f
            gravity = Gravity.CENTER
            setPadding(0, 24, 0, 48)
        })
        root.addView(Button(this).apply {
            text = "Stop ringing"
            setOnClickListener { finish() }
        })
        setContentView(root)

        startRinging()
    }

    private fun startRinging() {
        try {
            val uri = RingtoneManager.getActualDefaultRingtoneUri(this, RingtoneManager.TYPE_RINGTONE)
                ?: RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE)
            ringtone = RingtoneManager.getRingtone(this, uri)?.apply {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) isLooping = true
                streamType = AudioManager.STREAM_ALARM
                play()
            }
        } catch (e: Exception) {
            // No ringtone available; the notification already alerted.
        }
    }

    override fun onDestroy() {
        ringtone?.stop()
        super.onDestroy()
    }

    @Deprecated("Back just stops the ring")
    override fun onBackPressed() = finish()
}
