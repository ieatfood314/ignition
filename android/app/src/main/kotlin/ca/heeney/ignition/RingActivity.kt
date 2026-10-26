package ca.heeney.ignition

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.Ringtone
import android.media.RingtoneManager
import android.os.Build
import android.os.Bundle
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Shown full-screen over the lock screen when a T-minus lifts off and "ring at liftoff" is on. Rocket and
 * Buzz are short: one rocket launch of a few seconds and a couple of buzzes. Ringtone rings until you tap.
 * Launched by a full-screen-intent notification so it can wake the screen; the notification also carries the text, so the lock screen shows it either way.
 */
class RingActivity : Activity() {
    private var ringtone: Ringtone? = null
    private var player: MediaPlayer? = null
    private var vibrator: Vibrator? = null

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
            text = "Got it"
            setOnClickListener { finish() }
        })
        setContentView(root)

        startRinging()
    }

    private fun startRinging() {
        val sound = sound(this)
        buzz(repeat = sound == "ringtone")
        when (sound) {
            "rocket" -> rocket()
            "ringtone" -> ringtone()
        }
    }

    private fun buzz(repeat: Boolean) {
        vibrator = getSystemService(Vibrator::class.java)?.takeIf { it.hasVibrator() }?.apply {
            // A rumble and a short one, once; or over and over alongside a ringtone.
            vibrate(VibrationEffect.createWaveform(longArrayOf(0, 600, 300, 300, 600), if (repeat) 1 else -1), ALARM)
        }
    }

    private fun rocket() {
        val am = getSystemService(AudioManager::class.java)
        player = MediaPlayer.create(this, R.raw.liftoff, ALARM, am.generateAudioSessionId())?.apply { start() }
    }

    private fun ringtone() {
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
        player?.release()
        vibrator?.cancel()
        super.onDestroy()
    }

    @Deprecated("Back just stops the ring")
    override fun onBackPressed() = finish()

    companion object {
        val SOUNDS = setOf("rocket", "buzz", "ringtone")
        private val ALARM = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()

        /** What plays at liftoff: "rocket" (the default), "buzz" (vibrate only) or "ringtone". */
        fun sound(context: Context): String =
            Reactor.prefs(context).getString("ring_sound", null)?.takeIf { it in SOUNDS } ?: "rocket"
    }
}
