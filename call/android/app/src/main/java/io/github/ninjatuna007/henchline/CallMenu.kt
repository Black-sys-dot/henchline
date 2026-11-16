package io.github.ninjatuna007.henchline

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.ViewGroup
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView

/**
 * The call's own menu, as a calling app's (the user's wish, 2026-11-16): a tap on the call bar in the call's chat
 * slides it up. Who the call is with, the crew to move it to (a tap switches), how it sounds (speaker or earpiece,
 * clear or call sound, open line or hold to talk), and hang up. Each choice shows as two halves, the one on lit.
 */
class CallMenu(
    activity: Activity,
    private val who: String,
    private val crew: List<Pair<String, String>>,  // session, name
    private val current: String,
    private val speaker: Boolean,
    private val clear: Boolean,
    private val buttonOnly: Boolean,
    private val onSwitch: (String) -> Unit,
    private val onSpeaker: (Boolean) -> Unit,
    private val onClear: (Boolean) -> Unit,
    private val onButtonOnly: (Boolean) -> Unit,
    private val onHangUp: () -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(who, "On a call · tap a henchman to move the call to it")
        if (crew.size > 1) {
            sheet.addView(label("TALKING TO"), wide(18))
            val row = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
            for ((i, pair) in crew.withIndex()) row.addView(henchman(pair.first, pair.second, i))
            sheet.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled = false; addView(row) }, wide(10))
        }
        sheet.addView(label("SOUND"), wide(18))
        sheet.addView(choice("🔊  Speaker", "👂  Earpiece", speaker) { onSpeaker(it); close() }, wide(10, px(48)))
        sheet.addView(choice("Clear sound", "Call sound", clear) { onClear(it); close() }, wide(8, px(48)))
        sheet.addView(choice("Open line", "Hold to talk", !buttonOnly) { onButtonOnly(!it); close() }, wide(8, px(48)))
        val hang = primary("Hang up", colour(R.color.hang), Color.WHITE).apply { setOnClickListener { close(); onHangUp() } }
        sheet.addView(hang, wide(20, px(52)))
    }

    /** A crew member to move the call to: its face (ringed when it's the one on the call), its name and project.
     *  The others hop in turn while the menu is open, eager to take over (the user's wish, 2026-11-16). */
    private fun henchman(session: String, label: String, place: Int) = LinearLayout(activity).apply {
        orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
        setPadding(px(6), px(10), px(6), px(4))
        clipChildren = false; clipToPadding = false
        val on = session == current
        val name = label.substringBefore(",")
        val project = label.substringAfter(", in ", "").substringBefore(" · ").replace("_", " ")
        val face = ImageView(activity).apply {
            setImageResource(MainActivity.avatarOf(session))
            setPadding(px(6), px(6), px(6), px(6))
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL; setColor(colour(R.color.surface_high))
                if (on) setStroke(px(2), colour(R.color.call_live))
            }
        }
        addView(face, LinearLayout.LayoutParams(px(60), px(60)))
        addView(TextView(activity).apply {
            text = name; textSize = 12.5f; maxLines = 1; gravity = Gravity.CENTER
            ellipsize = android.text.TextUtils.TruncateAt.END
            setTextColor(colour(if (on) R.color.call_live else R.color.text))
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }, LinearLayout.LayoutParams(px(84), ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = px(6) })
        if (project.isNotEmpty()) addView(TextView(activity).apply {
            text = project; textSize = 11f; maxLines = 2; gravity = Gravity.CENTER
            ellipsize = android.text.TextUtils.TruncateAt.END
            setTextColor(colour(R.color.muted))
        }, LinearLayout.LayoutParams(px(84), ViewGroup.LayoutParams.WRAP_CONTENT))
        contentDescription = if (on) "$name in $project, on the call" else "Move the call to $name in $project"
        setOnClickListener { if (!on) { pop(this); onSwitch(session) }; close() }
        if (!on) eager(face, place)
    }

    /** A hop and a little lean, now and then, each a beat after the one before it: eager, not frantic. Only while
     *  the face is on screen (the menu closed, it stops). */
    private fun eager(face: ImageView, place: Int) {
        var running: android.animation.Animator? = null
        face.addOnAttachStateChangeListener(object : android.view.View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: android.view.View) {
                val hop = android.animation.ObjectAnimator.ofFloat(face, android.view.View.TRANSLATION_Y,
                    0f, -px(9).toFloat(), 0f, -px(4).toFloat(), 0f).apply { duration = 700 }
                val lean = android.animation.ObjectAnimator.ofFloat(face, android.view.View.ROTATION, 0f, -6f, 6f, 0f)
                    .apply { duration = 700 }
                running = android.animation.AnimatorSet().apply {
                    playTogether(hop, lean)
                    startDelay = 250L + place * 350L
                    addListener(object : android.animation.AnimatorListenerAdapter() {
                        private var cancelled = false
                        override fun onAnimationCancel(animation: android.animation.Animator) { cancelled = true }
                        override fun onAnimationEnd(animation: android.animation.Animator) {
                            if (!cancelled) { startDelay = 1600L; start() }  // a pause, then again
                        }
                    })
                    start()
                }
            }
            override fun onViewDetachedFromWindow(v: android.view.View) { running?.cancel(); running = null }
        })
    }

    /** Two halves of a pill, the one that's on lit in cyan; a tap on the other picks it (true: the first). */
    private fun choice(first: String, second: String, firstOn: Boolean, picked: (Boolean) -> Unit) =
        LinearLayout(activity).apply {
            orientation = LinearLayout.HORIZONTAL
            background = rounded(colour(R.color.navy), 24f)
            setPadding(px(4), px(4), px(4), px(4))
            for ((i, words) in listOf(first, second).withIndex()) {
                val on = (i == 0) == firstOn
                addView(TextView(activity).apply {
                    text = words; textSize = 14f; gravity = Gravity.CENTER
                    setTextColor(colour(if (on) R.color.navy else R.color.text))
                    typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
                    background = if (on) rounded(colour(R.color.cyan), 20f) else null
                    contentDescription = if (on) "$words, on" else words
                    setOnClickListener { if (!on) picked(i == 0) }
                }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f))
            }
        }
}
