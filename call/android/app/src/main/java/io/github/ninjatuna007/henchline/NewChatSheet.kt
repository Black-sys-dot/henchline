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
/** The keyboard put away, so a sheet or menu opening shows in full (it came up behind the keyboard, 2026-11-16). */
fun hideKeyboard(activity: Activity) {
    val focus = activity.currentFocus ?: return
    activity.getSystemService(android.view.inputmethod.InputMethodManager::class.java)
        ?.hideSoftInputFromWindow(focus.windowToken, 0)
    focus.clearFocus()
}

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
        hideKeyboard(activity)  // every sheet above it, not behind it (the model picker was, 2026-11-16)
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

    /** A row with an icon, a title and a line under it, as the chat's and a message's long-press sheets show. */
    protected fun actionRow(icon: String, title: String, detail: String, colourId: Int, then: () -> Unit) = LinearLayout(activity).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(px(14), px(14), px(14), px(14))
        background = rounded(colour(R.color.navy), 16f)
        addView(TextView(activity).apply { text = icon; textSize = 20f }, LinearLayout.LayoutParams(px(38), ViewGroup.LayoutParams.WRAP_CONTENT))
        addView(LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            addView(TextView(activity).apply { text = title; setTextColor(colour(colourId)); textSize = 16f; typeface = Typeface.DEFAULT_BOLD })
            if (detail.isNotEmpty()) addView(TextView(activity).apply { text = detail; setTextColor(colour(R.color.muted)); textSize = 12f })
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        setOnClickListener { pop(this); then() }
    }.also { it.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = px(8) } }

    /** Asked again in the same sheet, in red, before deleting: with the files saved from it (Downloads/Henchline)
     *  offered too, ticked, when there are some (the user's wish, 2026-11-16). */
    protected fun deleteConfirm(rows: LinearLayout, question: String, files: Int, button: String, then: (alsoFiles: Boolean) -> Unit) {
        rows.removeAllViews()
        rows.addView(TextView(activity).apply { text = question; setTextColor(colour(R.color.text)); textSize = 15f })
        val also = android.widget.CheckBox(activity).apply {
            text = if (files == 1) "Also delete its saved file from Downloads/Henchline"
                   else "Also delete its $files saved files from Downloads/Henchline"
            setTextColor(colour(R.color.text)); textSize = 14f; isChecked = true
            buttonTintList = android.content.res.ColorStateList.valueOf(colour(R.color.cyan))
        }
        if (files > 0) rows.addView(also, wide(12))
        val go = primary(button, colour(R.color.hang), Color.WHITE)
        go.setOnClickListener { close(); then(files > 0 && also.isChecked) }
        rows.addView(go, wide(16, px(52)))
        pop(go)
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
    private val loadModels: (agent: String, then: (JSONObject) -> Unit) -> Unit,  // {models} or {menu}
    private val start: (folder: String, agent: String, model: String, params: Map<String, String>, typed: Boolean,
                        done: (String?) -> Unit) -> Unit,
) : BottomPanel(activity) {
    private var model = ""  // "" the agent's own default
    private var modelName = "Default"
    private val settings = mutableMapOf<String, String>()  // its effort, context… from the agent's menu
    private var agent = agents.first()
    private var folder: JSONObject? = folders.firstOrNull()
    private var typed = ""  // a path in the box, when it is one
    private var failure = ""  // why the last start didn't happen, shown until something else is picked
    private var modelRefresh: () -> Unit = {}

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
            modelRefresh()
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
            if (where.isNotEmpty() && model.isNotEmpty()) go.text = "${go.text} · $modelName"
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
                setOnClickListener { agent = kind; failure = ""; model = ""; modelName = "Default"; settings.clear(); pop(this); refresh() }
            }, LinearLayout.LayoutParams(px(96), ViewGroup.LayoutParams.WRAP_CONTENT).apply { marginEnd = px(10) })
        }
        sheet.addView(HorizontalScrollView(activity).apply { isHorizontalScrollBarEnabled = false; addView(chips) }, wide(10))
        // The model: a pill saying which, a list to pick from (loaded for the agent picked)
        val modelPill = TextView(activity).apply {
            setTextColor(colour(R.color.cyan)); textSize = 14f
            background = rounded(colour(R.color.navy), 18f, 1, colour(R.color.surface_high))
            setPadding(px(16), px(10), px(16), px(10))
            setOnClickListener {
                text = "Loading models…"
                loadModels(agent) { answer ->
                    refresh()
                    val menu = answer.optJSONArray("menu")
                    if (menu != null && menu.length() > 0) {  // the same menu as a chat's: model, effort, context…
                        val entries = (0 until menu.length()).map { menu.getJSONObject(it) }
                        if (model.isEmpty()) {
                            model = entries[0].optString("id")
                        }
                        CursorModelMenu(activity, entries, model, settings, title = "${agent.replaceFirstChar { it.uppercase() }}'s model",
                            subtitle = "For the new chat") { picked, params, done ->
                            model = picked
                            settings.clear(); settings.putAll(params)
                            val name = entries.firstOrNull { it.optString("id") == picked }?.optString("name") ?: picked
                            val effort = params["effort"] ?: params["reasoning_effort"]
                            modelName = listOfNotNull(name, effort, params["context"]?.takeIf { it == "1m" }?.let { "1M" }).joinToString(" · ")
                            refresh(); done(null)
                        }.show()
                        return@loadModels
                    }
                    val list = answer.optJSONArray("models")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()
                    ModelPicker(activity, "${agent.replaceFirstChar { it.uppercase() }}'s model", list, withDefault = true) { id, name ->
                        model = id; modelName = name; settings.clear(); refresh()
                    }.show()
                }
            }
        }
        sheet.addView(modelPill, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT)
            .apply { topMargin = px(10) })
        modelRefresh = { modelPill.text = "✦ Model: $modelName  ▾" }

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
            start(path, agent, model, settings.toMap(), typed.isNotEmpty()) { why ->
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
    private val gone: Boolean,  // its agent is no longer on the laptop's crew list
    private val savedFiles: Int,  // files saved from this chat that no other chat has
    private val onClear: (alsoFiles: Boolean) -> Unit,
    private val onRemove: () -> Unit,
    private val onEnd: (done: (String?) -> Unit) -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(title, subtitle)
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(16))
        if (!gone) rows.addView(row("🧹", "Clear chat", "On this phone only: the agent keeps its conversation", R.color.text) {
            deleteConfirm(rows, "Clear the chat with $title on this phone? The agent keeps its conversation.",
                savedFiles, "Clear chat", onClear)
        })
        // Always there: an agent ended on the laptop may still be on the phone's last list (2026-11-16)
        rows.addView(row("🗑", "Remove chat", "From this phone's list; it comes back only if the agent is still running", R.color.text) {
            onRemove(); close()
        })
        if (canEnd && !gone) rows.addView(row("⏹", "End agent", "Closes its tmux window on the laptop", R.color.hang) { confirm(rows) })
        else if (!gone) rows.addView(TextView(activity).apply {
            text = "Started on the laptop, so it can't be ended from here."
            setTextColor(colour(R.color.muted)); textSize = 12f; setPadding(px(4), px(8), px(4), 0)
        })
        sheet.addView(quiet("Cancel") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
    }

    /** Straight to clearing (the chat's /flush): the same question as from the menu. */
    fun showClear() {
        val sheet = open(title, subtitle)
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(16))
        deleteConfirm(rows, "Clear the chat with $title on this phone? The agent keeps its conversation.",
            savedFiles, "Clear chat", onClear)
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

/**
 * A model to pick (the user's wish, 2026-11-16): the agent's models as rows, a box filtering them (the Cursor CLI
 * lists hundreds), and "Default" first when starting (the agent's own choice).
 */
class ModelPicker(
    activity: Activity,
    private val title: String,
    private val models: List<JSONObject>,  // {id, name}
    private val withDefault: Boolean,
    private val picked: (id: String, name: String) -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(title, if (withDefault) "Default is the agent's own setting" else "Switches it now, mid-chat")
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        val box = EditText(activity).apply {
            hint = "Filter models"
            setHintTextColor(colour(R.color.muted)); setTextColor(colour(R.color.text)); textSize = 15f; setSingleLine()
            background = rounded(colour(R.color.navy), 16f, 1, colour(R.color.surface_high))
            setPadding(px(16), px(12), px(16), px(12))
        }
        fun list(query: String) {
            rows.removeAllViews()
            val q = query.trim().lowercase()
            val shown = (if (withDefault) listOf(JSONObject().put("id", "").put("name", "Default")) else emptyList()) +
                models.filter { q.isEmpty() || q in it.optString("name").lowercase() || q in it.optString("id").lowercase() }
            for (m in shown.take(80)) rows.addView(TextView(activity).apply {
                text = m.optString("name")
                setTextColor(colour(R.color.text)); textSize = 16f
                setPadding(px(16), px(14), px(16), px(14))
                background = rounded(colour(R.color.navy), 14f)
                setOnClickListener { pop(this); picked(m.optString("id"), m.optString("name")); close() }
            }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = px(6) })
        }
        box.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun afterTextChanged(s: Editable?) = list(s?.toString() ?: "")
        })
        if (models.size > 8) sheet.addView(box, wide(14))
        sheet.addView(ScrollView(activity).apply { isVerticalScrollBarEnabled = false; addView(rows) },
            wide(10, (activity.resources.displayMetrics.heightPixels * 0.5).toInt()))
        sheet.addView(quiet("Cancel") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
        list("")
    }
}

/** The + in the message box (the Claude app's "Add context"): Camera, Photos, Files, as tiles. */
class AddSheet(activity: Activity, private val picked: (String) -> Unit) : BottomPanel(activity) {
    fun show() {
        val sheet = open("Add", "For the agent whose chat this is")
        val tiles = LinearLayout(activity).apply { orientation = LinearLayout.HORIZONTAL }
        for ((icon, name) in listOf("📷" to "Camera", "🖼" to "Photos", "📄" to "Files")) {
            tiles.addView(LinearLayout(activity).apply {
                orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER
                setPadding(px(8), px(18), px(8), px(18))
                background = rounded(colour(R.color.navy), 18f, 1, colour(R.color.surface_high))
                addView(TextView(activity).apply { text = icon; textSize = 26f; gravity = Gravity.CENTER })
                addView(TextView(activity).apply {
                    text = name; setTextColor(colour(R.color.text)); textSize = 15f; gravity = Gravity.CENTER
                    setPadding(0, px(6), 0, 0)
                })
                setOnClickListener { pop(this); close(); picked(name) }
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginEnd = if (name != "Files") px(10) else 0
            })
        }
        sheet.addView(tiles, wide(18))
        sheet.addView(quiet("Cancel") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
    }
}

/**
 * A Cursor IDE chat's model, as Cursor's own menu has it (the user's wish, 2026-11-16, shown from his screen): rows
 * for Model, Effort, Context and a Fast switch, each choice from Cursor's catalog for that model. Each change is
 * sent at once, the whole setting, and the rows follow what the chat is then on.
 */
class CursorModelMenu(
    activity: Activity,
    private val menu: List<JSONObject>,  // {id, name, params: [{id, name, kind, values: [{value, name}]}]}
    private var model: String,
    private val settings: MutableMap<String, String>,
    private val title: String = "Model",
    private val subtitle: String = "As in Cursor: the chat switches as you pick",
    private val apply: (model: String, params: Map<String, String>, done: (String?) -> Unit) -> Unit,
) : BottomPanel(activity) {
    private lateinit var rows: LinearLayout
    private lateinit var note: TextView

    fun show() {
        val sheet = open(title, subtitle)
        rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(14))
        note = TextView(activity).apply { setTextColor(colour(R.color.muted)); textSize = 13f; gravity = Gravity.CENTER }
        sheet.addView(note, wide(10))
        sheet.addView(quiet("Done") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(4) })
        draw()
    }

    private fun entry() = menu.firstOrNull { it.optString("id") == model }

    private fun params(): List<JSONObject> =
        entry()?.optJSONArray("params")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()

    private fun values(param: JSONObject): List<JSONObject> =
        param.optJSONArray("values")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()

    private fun draw() {
        rows.removeAllViews()
        rows.addView(row("Model", entry()?.optString("name") ?: model) {
            ModelPicker(activity, "Model", menu.map { JSONObject().put("id", it.optString("id")).put("name", it.optString("name")) },
                withDefault = false) { id, _ ->
                model = id
                // Its own settings: kept where the new model has the same choice, else its first
                val kept = mutableMapOf<String, String>()
                for (p in params()) {
                    val options = values(p).map { it.optString("value") }
                    kept[p.optString("id")] = settings[p.optString("id")]?.takeIf { it in options } ?: options.firstOrNull() ?: ""
                }
                settings.clear(); settings.putAll(kept)
                send()
            }.show()
        })
        // Fast last, as in Cursor
        for (p in params().sortedBy { it.optString("kind") == "bool" }) {
            val id = p.optString("id")
            val options = values(p)
            if (p.optString("kind") == "bool") {
                val on = settings[id] == "true"
                rows.addView(row(p.optString("name"), if (on) "On" else "Off") {
                    settings[id] = if (on) "false" else "true"; send()
                })
            } else {
                val shown = options.firstOrNull { it.optString("value") == settings[id] }?.optString("name") ?: settings[id] ?: ""
                rows.addView(row(p.optString("name"), shown) {
                    ModelPicker(activity, p.optString("name"), options.map { JSONObject().put("id", it.optString("value")).put("name", it.optString("name")) },
                        withDefault = false) { value, _ -> settings[id] = value; send() }.show()
                })
            }
        }
    }

    private fun send() {
        draw()
        note.text = "Switching…"
        apply(model, settings.toMap()) { why ->
            note.text = why ?: "Done."
            note.setTextColor(colour(if (why == null || why.startsWith("Switched")) R.color.muted else R.color.hang))
        }
    }

    private fun row(name: String, value: String, then: () -> Unit) = LinearLayout(activity).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(px(16), px(14), px(16), px(14))
        background = rounded(colour(R.color.navy), 14f)
        addView(TextView(activity).apply { text = name; setTextColor(colour(R.color.text)); textSize = 16f },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        addView(TextView(activity).apply { text = "$value  ›"; setTextColor(colour(R.color.cyan)); textSize = 15f })
        setOnClickListener { pop(this); then() }
    }.also { it.layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { bottomMargin = px(6) } }
}

/**
 * Long-press on a message (the user's wish, 2026-11-16): copy its words, open its file in another app, or delete it
 * from this phone, with the file it saved. There is no deleting for everyone: words typed into an agent are in its
 * own conversation at once, and the sheet says so.
 */
class MessageSheet(
    activity: Activity,
    private val title: String,
    private val subtitle: String,
    private val words: String?,  // something to copy
    private val onOpen: (() -> Unit)?,  // a file: saved, and opened in another app
    private val mine: Boolean,
    private val savedFiles: Int,  // its saved copies that no other message has
    private val onDelete: (alsoFiles: Boolean) -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(title, subtitle)
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(16))
        if (!words.isNullOrEmpty()) rows.addView(actionRow("📋", "Copy", "", R.color.text) {
            val clip = activity.getSystemService(android.content.ClipboardManager::class.java)
            clip.setPrimaryClip(android.content.ClipData.newPlainText("Henchline", words))
            close()
        })
        onOpen?.let { open -> rows.addView(actionRow("📂", "Open in another app", "Saved to Downloads/Henchline first", R.color.text) {
            close(); open()
        }) }
        rows.addView(actionRow("🗑", "Delete for me", if (mine) "From this phone: the agent already has your words"
                else "From this phone only", R.color.hang) {
            deleteConfirm(rows, "Delete this from the chat on this phone?", savedFiles, "Delete", onDelete)
        })
        sheet.addView(quiet("Cancel") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
    }
}

/**
 * /later (the user's idea, 2026-11-17): when the words should reach the agent. A few quick choices, or a time
 * picked; for a wake-up, the laptop can be kept from sleeping until then. Below, this chat's scheduled messages, each
 * with Cancel.
 */
class LaterSheet(
    activity: Activity,
    private val words: String,
    private val pending: List<org.json.JSONObject>,
    private val onPick: (at: Long, keepAwake: Boolean) -> Unit,
    private val onCancel: (id: String) -> Unit,
) : BottomPanel(activity) {
    fun show() {
        val sheet = open(if (words.isEmpty()) "Scheduled messages" else "Send later",
            if (words.isEmpty()) "Type /later and your words to schedule one" else "\u201c${words.take(80)}\u201d")
        val rows = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL }
        sheet.addView(rows, wide(16))
        if (words.isNotEmpty()) {
            val awake = android.widget.CheckBox(activity).apply {
                text = "Keep the laptop awake until then (a wake-up)"
                setTextColor(colour(R.color.text)); textSize = 14f
                buttonTintList = android.content.res.ColorStateList.valueOf(colour(R.color.cyan))
            }
            val now = java.util.Calendar.getInstance()
            fun at(hour: Int, minute: Int, tomorrow: Boolean): Long = (now.clone() as java.util.Calendar).apply {
                set(java.util.Calendar.HOUR_OF_DAY, hour); set(java.util.Calendar.MINUTE, minute); set(java.util.Calendar.SECOND, 0)
                set(java.util.Calendar.MILLISECOND, 0)
                if (tomorrow || timeInMillis <= System.currentTimeMillis()) add(java.util.Calendar.DAY_OF_MONTH, 1)
            }.timeInMillis
            val choices = listOf(
                "In 15 minutes" to { System.currentTimeMillis() + 15 * 60_000L },
                "In 1 hour" to { System.currentTimeMillis() + 60 * 60_000L },
                "Tomorrow 08:30" to { at(8, 30, true) },
            )
            for ((label, time) in choices) rows.addView(actionRow("⏰", label, "", R.color.text) {
                close(); onPick(time(), awake.isChecked)
            })
            rows.addView(actionRow("🕘", "Pick a time…", "Today, or tomorrow if it has passed", R.color.text) {
                android.app.TimePickerDialog(activity, { _, hour, minute ->
                    close(); onPick(at(hour, minute, false), awake.isChecked)
                }, now.get(java.util.Calendar.HOUR_OF_DAY), now.get(java.util.Calendar.MINUTE),
                    android.text.format.DateFormat.is24HourFormat(activity)).show()
            })
            rows.addView(awake, wide(4))
        }
        if (pending.isNotEmpty()) {
            rows.addView(label("SCHEDULED IN THIS CHAT"), wide(16))
            for (item in pending) {
                val time = android.text.format.DateFormat.getTimeFormat(activity).format(java.util.Date((item.optDouble("at") * 1000).toLong()))
                val day = if (android.text.format.DateUtils.isToday((item.optDouble("at") * 1000).toLong())) "" else "tomorrow or later · "
                val row = actionRow("⏰", "$day$time", item.optString("words").take(60) + "  · tap to cancel", R.color.text) {
                    onCancel(item.optString("id")); close()
                }
                rows.addView(row)
            }
        } else if (words.isEmpty()) {
            rows.addView(TextView(activity).apply {
                text = "Nothing scheduled in this chat."; setTextColor(colour(R.color.muted)); textSize = 14f
            })
        }
        sheet.addView(quiet("Close") { close() }, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply { gravity = Gravity.END; topMargin = px(8) })
    }
}
