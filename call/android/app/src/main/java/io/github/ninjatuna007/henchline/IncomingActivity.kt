package io.github.ninjatuna007.henchline

import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject

/**
 * An agent is calling: its henchman, its name, a line of henchman-speak (spoken over its own ringtone, in
 * its talkback voice), why it rang, and Answer / Decline. Shown over the lock screen; unanswered, it gives
 * up after RingService.RING_SECONDS.
 */
class IncomingActivity : Activity() {

    private val main = Handler(Looper.getMainLooper())
    private var ring: MediaPlayer? = null
    private var phrase: MediaPlayer? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        val agent = (intent.getStringExtra(AGENT) ?: "").takeIf { it in FLAVOURS } ?: "handsfree"
        val why = intent.getStringExtra(WHY) ?: ""
        val lines = phrasesOf(agent)
        val pick = lines.indices.random()

        val dp = resources.displayMetrics.density
        val screen = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(getColor(R.color.navy))
            setPadding((24 * dp).toInt(), (96 * dp).toInt(), (24 * dp).toInt(), (64 * dp).toInt())
        }
        screen.addView(TextView(this).apply {
            text = "${nameOf(agent)} is calling"
            setTextColor(getColor(R.color.text))
            textSize = 28f
            gravity = Gravity.CENTER
        })
        screen.addView(ImageView(this).apply { setImageResource(MainActivity.avatarOf(agent)) },
            LinearLayout.LayoutParams((220 * dp).toInt(), (220 * dp).toInt()).apply { topMargin = (32 * dp).toInt() })
        screen.addView(TextView(this).apply {  // the banner: what it is saying
            text = "“${lines[pick]}”"
            setTextColor(getColor(R.color.cyan))
            textSize = 20f
            gravity = Gravity.CENTER
            setPadding(0, (24 * dp).toInt(), 0, (8 * dp).toInt())
        })
        if (why.isNotEmpty()) screen.addView(TextView(this).apply {
            text = why
            setTextColor(getColor(R.color.muted))
            textSize = 15f
            gravity = Gravity.CENTER
        })
        screen.addView(android.view.View(this), LinearLayout.LayoutParams(1, 0, 1f))
        val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        fun round(icon: Int, background: Int, what: String, then: () -> Unit) = ImageButton(this).apply {
            setImageResource(icon)
            setBackgroundResource(background)
            setColorFilter(getColor(android.R.color.white))
            contentDescription = what
            setOnClickListener { then() }
        }
        buttons.addView(round(R.drawable.ic_call_end, R.drawable.round_hang, "Decline") { stop() },
            LinearLayout.LayoutParams((76 * dp).toInt(), (76 * dp).toInt()).apply { marginEnd = (88 * dp).toInt() })
        buttons.addView(round(R.drawable.ic_phone, R.drawable.round_call, "Answer") {
            startActivity(Intent(this, MainActivity::class.java).setAction(MainActivity.ANSWER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
            stop()
        }, LinearLayout.LayoutParams((76 * dp).toInt(), (76 * dp).toInt()))
        screen.addView(buttons)
        setContentView(screen)

        ring = player(resourceId("ring_$agent"), loop = true, volume = 0.45f)  // started by sayAgain's first beat
        phrase = player(resourceId("phrase_${agent}_$pick"), loop = false, volume = 1f)
        sayAgain()
        main.postDelayed({ stop() }, RingService.RING_SECONDS * 1000)
    }

    /** The phrase over the ringtone, on the ringtone's beat: it starts with the tone's loop and again on
     *  the first beat after it has ended, so voice and tone stay in step (the user's wish, 2026-11-15; a
     *  fixed gap drifted against the tone). */
    private fun sayAgain() {
        val sound = phrase ?: return
        // The tones play their motif twice a loop, so half a loop is a beat; the phrase comes back on the
        // first beat after it ends (whole loops left 2.5 s gaps)
        val beat = ring?.duration?.takeIf { it > 0 }?.toLong()?.div(2) ?: (sound.duration + SAY_GAP)
        val every = beat * ((sound.duration + beat - 1) / beat).coerceAtLeast(1)
        ring?.seekTo(0)  // the tone's loop starts with the phrase
        sound.seekTo(0)
        sound.start()
        main.postDelayed({ sayAgain() }, every)
    }

    /** A sound that won't load just isn't played: the screen, its buttons and the time-out go on. */
    private fun player(id: Int, loop: Boolean, volume: Float): MediaPlayer? {
        if (id == 0) return null
        val sound = MediaPlayer()
        return try {
            sound.apply {
                setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                resources.openRawResourceFd(id).use { setDataSource(it.fileDescriptor, it.startOffset, it.length) }
                isLooping = loop
                setVolume(volume, volume)
                prepare()
                if (loop) start()
            }
        } catch (e: Exception) {
            android.util.Log.w("Henchline", "ring sound failed: ${e.message}")
            sound.release()
            null
        }
    }

    private fun stop() {
        main.removeCallbacksAndMessages(null)
        ring?.release()
        phrase?.release()
        ring = null
        phrase = null
        getSystemService(NotificationManager::class.java).cancel(RingService.NOTIFICATION)
        finish()
    }

    override fun onDestroy() {
        if (ring != null) stop()
        super.onDestroy()
    }

    private fun resourceId(name: String) = resources.getIdentifier(name, "raw", packageName)

    private fun phrasesOf(agent: String): List<String> {
        val all = JSONObject(resources.openRawResource(R.raw.phrases).bufferedReader().readText())
        val lines = all.optJSONArray(agent) ?: all.getJSONArray("handsfree")
        return (0 until lines.length()).map { lines.getString(it) }
    }

    companion object {
        const val AGENT = "agent"
        const val WHY = "why"
        const val SAY_GAP = 700L  // ms between the phrase ending and starting again
        val FLAVOURS = setOf("claude", "cursor", "codex", "handsfree")  // henchmen, tones and phrases there are

        fun nameOf(agent: String) = when (agent) {
            "claude" -> "Claude"
            "cursor" -> "Cursor"
            "codex" -> "Codex"
            else -> "Your crew"
        }
    }
}
