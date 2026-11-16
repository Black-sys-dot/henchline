package io.github.ninjatuna007.henchline

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.Editable
import android.text.TextUtils
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject

/**
 * Bottom sheets in the share sheet's style (the user's wish, 2026-11-16: the plain dialogs looked "from 2008"):
 * a panel that slides up over the chat list, dimming it, closed by a tap outside it or Cancel.
 */
open class BottomPanel(protected val activity: Activity) {
    protected val dp = activity.resources.displayMetrics.density
    protected fun px(v: Int) = (v * dp).toInt()
    protected fun colour(id: Int) = activity.getColor(id)
    protected fun rounded(fill: Int, radius: Float, stroke: Int = 0, strokeColour: Int = 0) = GradientDrawable().apply {
        setColor(fill); cornerRadius = radius * dp
        if (stroke > 0) setStroke(px(stroke), strokeColour)
    }

    private var root: FrameLayout? = null
    private var sheet: LinearLayout? = null
    val isOpen get() = root?.parent != null

    /** A fresh panel with its handle, title and a line under it; what goes in it is added to the returned layout. */
    protected fun open(title: String, subtitle: String): LinearLayout {
        close(animate = false)
        val root = FrameLayout(activity).apply { setBackgroundColor(Color.parseColor("#99000000")); alpha = 0f }
        val sheet = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = GradientDrawable().apply {
                setColor(colour(R.color.surface))
                cornerRadii = floatArrayOf(28 * dp, 28 * dp, 28 * dp, 28 * dp, 0f, 0f, 0f, 0f)
            }
            setPadding(px(24), px(12), px(24), px(24))
            isClickable = true  // a tap on the sheet isn't a tap outside it
        }
        sheet.addView(View(activity).apply { background = rounded(colour(R.color.muted), 2f) },
            LinearLayout.LayoutParams(px(36), px(4)).apply { gravity = Gravity.CENTER_HORIZONTAL; bottomMargin = px(16) })
        sheet.addView(TextView(activity).apply {
            text = title; setTextColor(colour(R.color.text)); textSize = 22f
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        })
        if (subtitle.isNotEmpty()) sheet.addView(TextView(activity).apply {
            text = subtitle; setTextColor(colour(R.color.muted)); textSize = 14f; setPadding(0, px(4), 0, 0)
        })
        root.addView(sheet, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        root.setOnClickListener { close() }
        activity.addContentView(root, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        root.animate().alpha(1f).setDuration(180).start()
        sheet.translationY = 800 * dp
        sheet.animate().translationY(0f).setDuration(300).setInterpolator(DecelerateInterpolator(1.6f)).start()
        this.root = root
        this.sheet = sheet
        return sheet
    }

    fun close(animate: Boolean = true) {
        val r = root ?: return
        val s = sheet
        root = null; sheet = null
        val done = { (r.parent as? ViewGroup)?.removeView(r) }
        if (!animate || s == null) return done().let {}
        r.animate().alpha(0f).setDuration(200).start()
        s.animate().translationY(s.height.toFloat()).setDuration(200).withEndAction { done() }.start()
    }

    protected fun label(words: String) = TextView(activity).apply {
        text = words; setTextColor(colour(R.color.muted)); textSize = 12f; letterSpacing = 0.08f; typeface = Typeface.DEFAULT_BOLD
    }

    protected fun primary(words: String, fill: Int = colour(R.color.cyan), ink: Int = colour(R.color.navy)) = Button(activity).apply {
        text = words; isAllCaps = false; textSize = 16f; setTextColor(ink)
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        background = rounded(fill, 24f); stateListAnimator = null
    }

    protected fun quiet(words: String, then: () -> Unit) = Button(activity).apply {
        text = words; isAllCaps = false; textSize = 16f; setTextColor(colour(R.color.cyan))
        setBackgroundColor(Color.TRANSPARENT); stateListAnimator = null
        setOnClickListener { then() }
    }

    protected fun wide(topMargin: Int = 0, height: Int = ViewGroup.LayoutParams.WRAP_CONTENT) =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, height).apply { this.topMargin = px(topMargin) }

    /** A view popping in, a little springy: something is happening. */
    protected fun pop(view: View) {
        view.scaleX = 0.92f; view.scaleY = 0.92f
        view.animate().scaleX(1f).scaleY(1f).setDuration(220).setInterpolator(OvershootInterpolator()).start()
    }
}

/**
 * New chat (the user's wish, 2026-11-16): the agents as henchmen to tap, the folders the laptop offers as cards
 * (newest first), one box that both filters them and takes a typed path, and a button saying exactly what will
 * start where. start() does the asking; it calls back with null when the chat opened, or why not.
 */
class NewChatSheet(
    activity: Activity,
    private val folders: List<JSONObject>,  // {path, name}
    private val agents: List<String>,
    private val start: (folder: String, agent: String, typed: Boolean, done: (String?) -> Unit) -> Unit,
) : BottomPanel(activity) {
    private var agent = agents.first()
    private var folder: JSONObject? = folders.firstOrNull()
    private var typed = ""  // a path in the box, when it is one
    private var failure = ""  // why the last start didn't happen, shown until something else is picked

    fun show() {
        val sheet = open("New chat", "Starts an agent on your laptop, in its own tmux window")
        sheet.addView(label("AGENT"), wide(20))
        val chips = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
        val go = primary("")
        val warning = TextView(activity).apply {
            textSize = 12f; gravity = Gravity.CENTER; visibility = View.GONE
        }
        val cards = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        val box = EditText(activity)
        val home = folders.firstOrNull { it.optString("name") == "home" }?.optString("path")
            ?: folders.firstOrNull()?.optString("path")?.let { Regex("^(/home/[^/]+)").find(it)?.value }

        fun pretty(path: String) = if (home != null && path.startsWith(home)) "~" + path.removePrefix(home) else path
        fun refresh() {
            for (i in 0 until chips.childCount) {
                val chip = chips.getChildAt(i)
                val on = chip.tag == agent
                chip.background = rounded(if (on) colour(R.color.surface_high) else Color.TRANSPARENT, 20f, if (on) 2 else 1,
                    if (on) colour(R.color.cyan) else colour(R.color.surface_high))
                chip.alpha = if (on) 1f else 0.7f
            }
            for (i in 0 until cards.childCount) {
                val card = cards.getChildAt(i)
                val on = typed.isEmpty() && card.tag == folder?.optString("path") || typed.isNotEmpty() && card.tag == "typed"
                card.background = rounded(colour(if (on) R.color.surface_high else R.color.navy), 16f, if (on) 2 else 0, colour(R.color.cyan))
            }
            val where = if (typed.isNotEmpty()) typed.trimEnd('/').substringAfterLast('/') else folder?.optString("name") ?: ""
            go.text = if (where.isEmpty()) "Pick a folder" else "Start ${agent.replaceFirstChar { it.uppercase() }} in ${where.replace("_", " ")}"
            go.isEnabled = where.isNotEmpty()
            go.alpha = if (go.isEnabled) 1f else 0.5f
            when {
                failure.isNotEmpty() -> { warning.text = failure; warning.setTextColor(colour(R.color.hang)); warning.visibility = View.VISIBLE }
                typed.isNotEmpty() -> {
                    warning.text = "A folder the agent hasn't worked in: starting there trusts it to read and change its files."
                    warning.setTextColor(colour(R.color.muted)); warning.visibility = View.VISIBLE
                }
                else -> warning.visibility = View.GONE
            }
        }
        fun listFolders(query: String) {
            cards.removeAllViews()
            failure = ""
            val q = query.trim()
            typed = if (q.startsWith("/") || q.startsWith("~")) q else ""
            if (typed.isNotEmpty()) cards.addView(card("typed", "✏️", "Use this path", typed) { refresh() })
            val shown = if (typed.isNotEmpty() || q.isEmpty()) folders
                else folders.filter { q.lowercase() in it.optString("name").lowercase() || q.lowercase() in it.optString("path").lowercase() }
            for (f in shown) cards.addView(card(f.optString("path"), "📁", f.optString("name").replace("_", " "),
                pretty(f.optString("path")).substringBeforeLast("/").ifEmpty { "/" }) {
                folder = f
                failure = ""
                if (typed.isNotEmpty()) box.setText("")  // a picked card wins over a half-typed path
                refresh()
            })
            if (shown.isEmpty() && typed.isEmpty()) cards.addView(TextView(activity).apply {
                text = "No folder matches. Type a path starting with ~ or /."
                setTextColor(colour(R.color.muted)); textSize = 13f; setPadding(px(4), px(12), px(4), px(12))
            })
            refresh()
        }

        for (kind in agents) {
            chips.addView(LinearLayout(activity).apply {
                tag = kind
                orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
                setPadding(px(12), px(10), px(12), px(8))
                addView(ImageView(activity).apply { setImageResource(MainActivity.avatarOf(kind)) }, LinearLayout.LayoutParams(px(52), px(52)))
                addView(TextView(activity).apply {
                    text = if (kind == "cursor") "Cursor CLI" else kind.replaceFirstChar { it.uppercase() }
                    setTextColor(colour(R.color.text)); textSize = 13f; gravity = Gravity.CENTER; setPadding(0, px(6), 0, 0)
                })
                setOnClickListener { agent = kind; failure = ""; pop(this); refresh() }
            }, LinearLayout.LayoutParams(px(96), ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginEnd = px(10) })
        }
        sheet.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled = false; addView(chips) }, wide(10))

        sheet.addView(label("FOLDER"), wide(18))
        box.apply {
            hint = "Search, or type a path (~/…)"
            setHintTextColor(colour(R.color.muted)); setTextColor(colour(R.color.text)); textSize = 15f; setSingleLine()
            background = rounded(colour(R.color.navy), 16f, 1, colour(R.color.surface_high))
            setPadding(px(16), px(12), px(16), px(12))
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun afterTextChanged(s: Editable?) = listFolders(s?.toString() ?: "")
            })
        }
        sheet.addView(box, wide(10))
        // The list scrolls inside the sheet, at most about half the screen
        sheet.addView(ScrollView(activity).apply { isVerticalScrollBarEnabled = false; addView(cards) },
            wide(10, minOf(px(76) * maxOf(folders.size, 1), (activity.resources.displayMetrics.heightPixels * 0.38).toInt())))
        sheet.addView(warning, wide(12))
        val buttons = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL or Gravity.END }
        buttons.addView(quiet("Cancel") { close() })
        buttons.addView(go, LinearLayout.LayoutParams(0, px(52), 1f).apply { marginStart = px(8) })
        go.setOnClickListener {
            val path = if (typed.isNotEmpty()) (if (typed.startsWith("~") && home != null) home + typed.removePrefix("~") else typed)
                       else folder?.optString("path") ?: return@setOnClickListener
            go.isEnabled = false
            go.text = "Starting ${agent.replaceFirstChar { it.uppercase() }}…"
            go.animate().alpha(0.7f).setDuration(150).start()
            start(path, agent, typed.isNotEmpty()) { why ->
                if (why == null) close()  // this sheet's own close: never a newer sheet's
                else { failure = why; refresh() }
            }
        }
        sheet.addView(buttons, wide(16))
        listFolders("")
    }

    private fun card(tag: String, icon: String, title: String, detail: String, picked: () -> Unit) = LinearLayout(activity).apply {
        this.tag = tag
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(px(14), px(12), px(14), px(12))
        addView(TextView(activity).apply { text = icon; textSize = 22f }, LinearLayout.LayoutParams(px(36), ViewGroup.LayoutParams.WRAP_CONTENT))
        addView(LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(activity).apply {
                text = title; setTextColor(colour(R.color.text)); textSize = 16f; typeface = Typeface.DEFAULT_BOLD
                maxLines = 1; ellipsize = TextUtils.TruncateAt.END
            })
            addView(TextView(activity).apply {
                text = detail; setTextColor(colour(R.color.muted)); textSize = 12f; maxLines = 1; ellipsize = TextUtils.TruncateAt.MIDDLE
            })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        setOnClickListener { pop(this); picked() }
    }.also { it.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = px(8) } }
}

/**
 * Long-press on a chat: what can be done with it, as rows with an icon; ending an agent asks again in the same
 * sheet, in red, before it happens.
 */
class ChatActionsSheet(
    activity: Activity,
    private val title: String,
    private val subtitle: String,
    private val canEnd: Boolean,
    private val onClear: () -> Unit,
    private val onEnd: (done: (String?) -> Unit) -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(title, subtitle)
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(16))
        rows.addView(row("🧹", "Clear chat", "On this phone only: the agent keeps its conversation", R.color.text) { onClear(); close() })
        if (canEnd) rows.addView(row("⏹", "End agent", "Closes its tmux window on the laptop", R.color.hang) { confirm(rows) })
        else rows.addView(TextView(activity).apply {
            text = "Started on the laptop, so it can't be ended from here."
            setTextColor(colour(R.color.muted)); textSize = 12f; setPadding(px(4), px(8), px(4), 0)
        })
        sheet.addView(quiet("Cancel") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
    }

    private fun confirm(rows: LinearLayout) {
        rows.removeAllViews()
        rows.addView(TextView(activity).apply {
            text = "End $title? Its tmux window closes and its chat leaves the list."
            setTextColor(colour(R.color.text)); textSize = 15f
        })
        val end = primary("End agent", colour(R.color.hang), Color.WHITE)
        val note = TextView(activity).apply { setTextColor(colour(R.color.hang)); textSize = 12f; visibility = View.GONE }
        end.setOnClickListener {
            end.isEnabled = false; end.text = "Ending…"
            onEnd { why -> if (why == null) close() else { note.text = why; note.visibility = View.VISIBLE; end.isEnabled = true; end.text = "End agent" } }
        }
        rows.addView(end, wide(16, px(52)))
        rows.addView(note, wide(8))
        pop(end)
    }

    private fun row(icon: String, title: String, detail: String, colourId: Int, then: () -> Unit) = LinearLayout(activity).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(px(14), px(14), px(14), px(14))
        background = rounded(colour(R.color.navy), 16f)
        addView(TextView(activity).apply { text = icon; textSize = 20f }, LinearLayout.LayoutParams(px(38), ViewGroup.LayoutParams.WRAP_CONTENT))
        addView(LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(activity).apply { text = title; setTextColor(colour(colourId)); textSize = 16f; typeface = Typeface.DEFAULT_BOLD })
            addView(TextView(activity).apply { text = detail; setTextColor(colour(R.color.muted)); textSize = 12f })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        setOnClickListener { pop(this); then() }
    }.also { it.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = px(8) } }
}
