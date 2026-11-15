package io.github.ninjatuna007.henchline

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import androidx.drawerlayout.widget.DrawerLayout
import org.json.JSONObject

/**
 * The call screen: the same messages to and from the call server as the call page. Your crew and the settings
 * live in a drawer from the left; each agent is a techno henchman of its own flavour (avatars.py). The call
 * itself runs in CallService; this screen watches it, and can be closed while the call goes on.
 */
class MainActivity : Activity() {

    private lateinit var drawer: DrawerLayout
    private lateinit var callButton: ImageButton
    private lateinit var talk: Button
    private lateinit var who: TextView
    private lateinit var avatar: ImageView
    private lateinit var state: TextView
    private lateinit var chat: LinearLayout
    private lateinit var chatScroll: ScrollView
    private lateinit var text: EditText
    private lateinit var send: ImageButton
    private lateinit var crew: LinearLayout
    private lateinit var clear: Switch

    private val main = Handler(Looper.getMainLooper())
    private var buttonOnly = false  // a noisy place: only holding the button gets in
    private var current = ""  // the agent replies go to now; its chat is the one shown
    private var names = mapOf<String, String>()  // session: the name the server gives it
    private var typed = 0  // ids for typed lines, so a "not sent" note strikes the right one
    private var talking = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        drawer = findViewById(R.id.drawer)
        callButton = findViewById(R.id.call)
        talk = findViewById(R.id.talk)
        who = findViewById(R.id.who)
        avatar = findViewById(R.id.avatar)
        state = findViewById(R.id.state)
        chat = findViewById(R.id.chat)
        chatScroll = findViewById(R.id.chatScroll)
        text = findViewById(R.id.text)
        send = findViewById(R.id.send)
        crew = findViewById(R.id.crew)
        clear = findViewById(R.id.clear)
        showMode("waiting")
        showCrew()

        findViewById<ImageButton>(R.id.menu).setOnClickListener { drawer.openDrawer(Gravity.START) }
        callButton.setOnClickListener { if (CallService.Bus.connected) hangUp() else call() }
        findViewById<View>(R.id.serverRow).setOnClickListener { askServer { } }
        showClear(prefs().getBoolean("clear", true))
        setUpTalkButton()
        send.setOnClickListener { sendTyped() }
        text.setOnEditorActionListener { _, action, _ -> (action == EditorInfo.IME_ACTION_SEND).also { if (it) sendTyped() } }
        requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS,
            Manifest.permission.BLUETOOTH_CONNECT), 1)
        RingService.register(this)  // so agents can ring this phone
        askFullScreen()
        answer(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        answer(intent)
    }

    /** Opened from henchline://call (an agent rang): ask, as a phone does. Any app or web page can open the
     *  link, so it never turns the mic on by itself: only Answer connects. */
    private fun answer(intent: Intent?) {
        if (intent?.action == ANSWER) {  // Answer on IncomingActivity: the user's own tap, so connect
            intent.action = null
            call()
            return
        }
        if (intent?.data?.scheme == "henchline" && intent.data?.host == "call" && CallService.Bus.service == null) {
            intent.data = null  // once: rotating the screen must not ask again
            AlertDialog.Builder(this)
                .setIcon(R.mipmap.ic_launcher)
                .setTitle("Your crew is calling")
                .setMessage("An agent wants to talk.")
                .setPositiveButton("Answer") { _, _ -> say("", "Answering…", note = true); call() }
                .setNegativeButton("Decline", null)
                .show()
        }
    }

    override fun onResume() {
        super.onResume()
        CallService.Bus.listener = { message -> main.post { fromServer(message) } }
        showConnected(CallService.Bus.connected)
        showServer()
    }

    private fun showServer() {
        findViewById<TextView>(R.id.server).text =
            prefs().getString("server", "")?.removePrefix("https://")?.trimEnd('/')?.ifEmpty { null }
                ?: "Set the laptop's address"
    }

    override fun onPause() {
        CallService.Bus.listener = null  // the call goes on; this screen just stops drawing it
        super.onPause()
    }

    private fun call() {
        if (CallService.Bus.service != null) {  // already connecting: one call at a time
            state.text = "Connecting…"
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            say("", "The call needs the microphone: allow it, then call again.", note = true)
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        val server = prefs().getString("server", "") ?: ""
        if (server.isEmpty()) {
            askServer { call() }
            return
        }
        state.text = "Connecting…"
        startForegroundService(Intent(this, CallService::class.java).putExtra(CallService.SERVER, server)
            .putExtra(CallService.CLEAR, prefs().getBoolean("clear", true)))
    }

    private fun hangUp() {
        startService(Intent(this, CallService::class.java).setAction(CallService.STOP))
    }

    /** A ring shows over the lock screen only with this permission, which Android 14+ asks the user for, once. */
    private fun askFullScreen() {
        val manager = getSystemService(android.app.NotificationManager::class.java)
        if (android.os.Build.VERSION.SDK_INT < 34 || manager.canUseFullScreenIntent() || prefs().getBoolean("asked_full", false)) return
        prefs().edit().putBoolean("asked_full", true).apply()
        AlertDialog.Builder(this)
            .setIcon(R.mipmap.ic_launcher)
            .setTitle("Let your crew ring you")
            .setMessage("Allow Henchline to show calls over the lock screen, as a phone call does.")
            .setPositiveButton("Allow") { _, _ ->
                startActivity(Intent(android.provider.Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                    android.net.Uri.parse("package:$packageName")))
            }
            .setNegativeButton("Not now", null)
            .show()
    }

    /** The call page's address: https://<laptop's Tailscale name>/ (the laptop's `tailscale serve`). */
    private fun askServer(then: () -> Unit) {
        val box = EditText(this).apply {
            setText(prefs().getString("server", "https://"))
            hint = "https://laptop.your-tailnet.ts.net/"
        }
        AlertDialog.Builder(this)
            .setTitle("The laptop's address")
            .setView(LinearLayout(this).apply { setPadding(48, 16, 48, 0); addView(box) })
            .setPositiveButton("Save") { _, _ ->
                prefs().edit().putString("server", box.text.toString().trim()).apply()
                showServer()
                then()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun prefs() = getSharedPreferences("call", MODE_PRIVATE)

    private fun fromServer(message: String) {
        val status = JSONObject(message)
        when {
            status.has("opened") -> {
                showConnected(true)
                if (buttonOnly) CallService.Bus.service?.send(JSONObject().put("button_only", true).toString())
            }
            status.has("closed") -> {
                showConnected(false)
                say("", status.getString("closed"), note = true)
            }
            status.has("chat") -> {
                val line = status.getJSONObject("chat")
                val from = line.getString("from")
                say(from, line.getString("text"), mine = from == "you", session = line.optString("session"))
            }
            status.has("agents") -> showAgents(status)
            status.has("note") -> {
                val id = status.optString("unsent")
                chat.findViewWithTag<TextView>("typed-$id")?.apply {
                    alpha = 0.5f
                    paintFlags = paintFlags or Paint.STRIKE_THRU_TEXT_FLAG
                }
                say("", status.getString("note"), note = true)
            }
            status.has("mode") -> {
                state.text = status.optString("state")
                showMode(status.getString("mode"))
            }
        }
    }

    /** The agents a call can switch to; the chat shows the current one's lines. */
    private fun showAgents(status: JSONObject) {
        val list = status.getJSONArray("agents")
        names = (0 until list.length()).associate {
            list.getJSONObject(it).getString("session") to list.getJSONObject(it).getString("name")
        }
        val now = status.optString("current")
        if (now.isNotEmpty() && now != current) {
            current = now
            for (i in 0 until chat.childCount) {
                val line = chat.getChildAt(i)
                val session = line.getTag(R.id.chat) as String? ?: ""
                line.visibility = if (session.isEmpty() || session == current) View.VISIBLE else View.GONE
            }
            chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        }
        showCrew()
    }

    /** The drawer's crew list and the top bar: each agent's henchman and name, the current one marked. */
    private fun showCrew() {
        crew.removeAllViews()
        if (names.isEmpty()) {
            crew.addView(TextView(this).apply {
                text = if (CallService.Bus.connected) "Nobody has spoken lately." else "Call to see your crew."
                setTextColor(getColor(R.color.muted))
                setPadding(0, 12, 0, 12)
            })
        }
        for ((session, name) in names) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(12, 12, 12, 12)
                if (session == current) setBackgroundResource(R.drawable.row_current)
                setOnClickListener {
                    if (session != current) CallService.Bus.service?.send(JSONObject().put("switch", session).toString())
                    drawer.closeDrawer(Gravity.START)
                }
            }
            row.addView(ImageView(this).apply { setImageResource(avatarOf(session)) }, LinearLayout.LayoutParams(dp(40), dp(40)))
            row.addView(TextView(this).apply {
                text = name
                setTextColor(getColor(R.color.text))
                textSize = 15f
                setPadding(dp(14), 0, 0, 0)
            })
            crew.addView(row)
        }
        val name = names[current]
        who.text = name?.substringBefore(",") ?: "Henchline"
        avatar.setImageResource(if (name != null) avatarOf(current) else R.drawable.avatar_handsfree)
    }

    private fun setUpTalkButton() {
        val hold = Runnable {
            talking = true
            CallService.Bus.service?.send("""{"push": true}""")
        }
        talk.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> main.postDelayed(hold, HOLD_MS)
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    main.removeCallbacks(hold)
                    if (talking) {
                        talking = false
                        CallService.Bus.service?.send("""{"push": false}""")
                    } else if (event.actionMasked == MotionEvent.ACTION_UP) {
                        buttonOnly = !buttonOnly
                        CallService.Bus.service?.send(JSONObject().put("button_only", buttonOnly).toString())
                        say("", if (buttonOnly) "Open line off: hold to talk" else "Open line on: just talk", note = true)
                        view.performClick()
                    }
                }
            }
            true
        }
    }

    private fun sendTyped() {
        val words = text.text.toString().trim()
        if (words.isEmpty() || !CallService.Bus.connected) return
        val id = (++typed).toString()
        CallService.Bus.service?.send(JSONObject().put("chat", words).put("session", current).put("id", id).toString())
        say("you", words, mine = true, session = current).tag = "typed-$id"
        text.setText("")
    }

    /** The clear-sound switch shows this, without counting as a flip. A flip switches the call too, if one is up. */
    private fun showClear(on: Boolean) {
        clear.setOnCheckedChangeListener(null)
        clear.isChecked = on
        clear.setOnCheckedChangeListener { _, flipped ->
            val service = CallService.Bus.service
            if (service != null && service.clear != flipped && !service.switchSound(flipped)) {
                showClear(!flipped)  // still switching from the last flip
                return@setOnCheckedChangeListener
            }
            prefs().edit().putBoolean("clear", flipped).apply()
            if (service != null) say("", if (flipped) "Clear sound" else "Call sound", note = true)
        }
    }

    private fun showConnected(on: Boolean) {
        callButton.setImageResource(if (on) R.drawable.ic_call_end else R.drawable.ic_phone)
        callButton.setBackgroundResource(if (on) R.drawable.round_hang else R.drawable.round_call)
        callButton.contentDescription = if (on) "Hang up" else "Call"
        talk.isEnabled = on
        text.isEnabled = on
        send.isEnabled = on
        // Off a call there is nothing to hold or type to: just the henchman and how to call
        val shown = if (on) View.VISIBLE else View.GONE
        talk.visibility = shown
        findViewById<View>(R.id.inputRow).visibility = shown
        chatScroll.visibility = shown  // off a call: the henchman and how to call, not the last call's chat
        findViewById<View>(R.id.idle).visibility = if (on) View.GONE else View.VISIBLE
        showClear(CallService.Bus.service?.clear ?: prefs().getBoolean("clear", true))
        if (!on) {
            state.text = "Not connected"
            showMode("waiting")
        }
        showCrew()
    }

    /** The laptop tray's colours: grey while only the wake word gets in, then green, red, yellow. */
    private fun showMode(mode: String) {
        val colour = when (mode) {
            "listening" -> 0xFF3CB371
            "recording" -> 0xFFE24A4A
            "transcribing" -> 0xFFE0A100
            else -> 0xFF5A6A8E
        }.toInt()
        talk.background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(colour)
            setStroke(dp(3), getColor(R.color.cyan) and 0x55FFFFFF)  // a faint goggle-cyan ring
        }
    }

    /** A chat line: the agent's henchman and a bubble on the left, yours on the right, notes in the middle. */
    private fun say(from: String, words: String, mine: Boolean = false, note: Boolean = false, session: String = ""): TextView {
        val bubble = TextView(this).apply {
            text = words
            setTextColor(getColor(if (note) R.color.muted else R.color.text))
            textSize = if (note) 13f else 15f
            if (!note) {
                setBackgroundResource(if (mine) R.drawable.bubble_mine else R.drawable.bubble_agent)
                setPadding(dp(14), dp(10), dp(14), dp(10))
            }
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = when {
                note -> Gravity.CENTER_HORIZONTAL
                mine -> Gravity.END
                else -> Gravity.START or Gravity.BOTTOM
            }
            setPadding(0, dp(4), 0, dp(4))
            setTag(R.id.chat, session)
            visibility = if (session.isEmpty() || current.isEmpty() || session == current) View.VISIBLE else View.GONE
        }
        if (!note && !mine && from.isNotEmpty()) {
            row.addView(ImageView(this).apply { setImageResource(avatarOf(session.ifEmpty { from })) },
                LinearLayout.LayoutParams(dp(28), dp(28)).apply { marginEnd = dp(8) })
        }
        row.addView(bubble, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT).apply { if (!note) width = LinearLayout.LayoutParams.WRAP_CONTENT })
        bubble.maxWidth = (resources.displayMetrics.widthPixels * 0.75).toInt()
        chat.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT))
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        return bubble
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    companion object {
        const val HOLD_MS = 300L  // longer than this is a hold; shorter is a tap
        const val ANSWER = "io.github.ninjatuna007.henchline.ANSWER"  // from IncomingActivity's Answer

        /** Each agent's henchman, by its session (claude-…, cursor-…, codex-…) or its name. */
        fun avatarOf(sessionOrName: String): Int = when {
            sessionOrName.startsWith("claude", ignoreCase = true) -> R.drawable.avatar_claude
            sessionOrName.startsWith("cursor", ignoreCase = true) -> R.drawable.avatar_cursor
            sessionOrName.startsWith("codex", ignoreCase = true) -> R.drawable.avatar_codex
            else -> R.drawable.avatar_handsfree
        }
    }
}
