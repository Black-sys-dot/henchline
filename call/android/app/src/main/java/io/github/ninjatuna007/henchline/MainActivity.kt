package io.github.ninjatuna007.henchline

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import org.json.JSONObject

/**
 * The call page as an app: the same messages to and from the call server, the same layout. The call
 * itself runs in CallService; this screen watches it, and can be closed while the call goes on.
 */
class MainActivity : Activity() {

    private lateinit var callButton: Button
    private lateinit var talk: Button
    private lateinit var state: TextView
    private lateinit var chat: LinearLayout
    private lateinit var chatScroll: ScrollView
    private lateinit var text: EditText
    private lateinit var send: Button
    private lateinit var agentMenu: Spinner
    private lateinit var sound: Button

    private val main = Handler(Looper.getMainLooper())
    private var buttonOnly = false  // a noisy place: only holding the button gets in
    private var current = ""  // the agent replies go to now; its chat is the one shown
    private var sessions = listOf<String>()
    private var typed = 0  // ids for typed lines, so a "not sent" note strikes the right one
    private var talking = false
    private var showingAgents = false  // the menu being filled, not picked by the user

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        callButton = findViewById(R.id.call)
        talk = findViewById(R.id.talk)
        state = findViewById(R.id.state)
        chat = findViewById(R.id.chat)
        chatScroll = findViewById(R.id.chatScroll)
        text = findViewById(R.id.text)
        send = findViewById(R.id.send)
        agentMenu = findViewById(R.id.agent)
        showMode("waiting")
        say("", "Tap Call. Then just talk, hold the round button to talk, or type below.", note = true)

        callButton.setOnClickListener { if (CallService.Bus.connected) hangUp() else call() }
        callButton.setOnLongClickListener { askServer { }; true }
        findViewById<Button>(R.id.settings).setOnClickListener { askServer { } }
        sound = findViewById(R.id.sound)
        sound.setOnClickListener {
            val service = CallService.Bus.service ?: return@setOnClickListener
            val toClear = !service.clear
            if (!service.switchSound(toClear)) return@setOnClickListener  // still switching from the last tap
            prefs().edit().putBoolean("clear", toClear).apply()
            showSound(toClear)
            say("", if (toClear) "Sound: clear (media)" else "Sound: call (the phone-call path)", note = true)
        }
        setUpTalkButton()
        send.setOnClickListener { sendTyped() }
        text.setOnEditorActionListener { _, action, _ -> (action == EditorInfo.IME_ACTION_SEND).also { if (it) sendTyped() } }
        agentMenu.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                val picked = sessions.getOrNull(position) ?: return
                if (!showingAgents && picked != current) CallService.Bus.service?.send(JSONObject().put("switch", picked).toString())
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
        requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS,
            Manifest.permission.BLUETOOTH_CONNECT), 1)
        answer(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        answer(intent)
    }

    /** Opened from henchline://call (an agent rang): connect, unless a call is up already. */
    private fun answer(intent: Intent?) {
        if (intent?.data?.scheme == "henchline" && intent.data?.host == "call" && CallService.Bus.service == null) {
            intent.data = null  // once: rotating the screen must not call again
            say("", "Answering…", note = true)
            call()
        }
    }

    override fun onResume() {
        super.onResume()
        CallService.Bus.listener = { message -> main.post { fromServer(message) } }
        showConnected(CallService.Bus.connected)
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
            say("", "The call needs the microphone: allow it, then tap Call again.", note = true)
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        val server = prefs().getString("server", "") ?: ""
        if (server.isEmpty()) {
            askServer { call() }
            return
        }
        state.text = "Starting…"
        startForegroundService(Intent(this, CallService::class.java).putExtra(CallService.SERVER, server)
            .putExtra(CallService.CLEAR, prefs().getBoolean("clear", true)))
    }

    private fun hangUp() {
        startService(Intent(this, CallService::class.java).setAction(CallService.STOP))
    }

    /** Settings: the call page's address, https://<laptop's Tailscale name>/ (the laptop's `tailscale serve`),
     *  and the clear-voice experiment. */
    private fun askServer(then: () -> Unit) {
        val box = EditText(this).apply {
            setText(prefs().getString("server", "https://"))
            hint = "https://laptop.your-tailnet.ts.net/"
        }
        val clear = android.widget.CheckBox(this).apply {
            text = "Clear voice (from the next call; the Sound button switches the current one): media sound, " +
                "not the muffled phone-call sound. Untick if your voice echoes back."
            isChecked = prefs().getBoolean("clear", true)
        }
        val form = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 16, 48, 0)
            addView(box)
            addView(clear)
        }
        AlertDialog.Builder(this)
            .setTitle("Settings")
            .setView(form)
            .setPositiveButton("Save") { _, _ ->
                prefs().edit().putString("server", box.text.toString().trim()).putBoolean("clear", clear.isChecked).apply()
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
                say("", "Call started", note = true)
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
                chat.findViewWithTag<TextView>("typed-$id")?.apply { alpha = 0.5f; paintFlags = paintFlags or android.graphics.Paint.STRIKE_THRU_TEXT_FLAG }
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
        sessions = (0 until list.length()).map { list.getJSONObject(it).getString("session") }
        val names = (0 until list.length()).map { list.getJSONObject(it).getString("name") }
        showingAgents = true
        agentMenu.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item,
            names.ifEmpty { listOf("No agent yet") })
        val now = status.optString("current")
        if (now.isNotEmpty() && now != current) {
            current = now
            for (i in 0 until chat.childCount) {
                val line = chat.getChildAt(i)
                val session = line.getTag(R.id.chat) as String? ?: ""
                line.visibility = if (session.isEmpty() || session == current) View.VISIBLE else View.GONE
            }
        }
        sessions.indexOf(current).takeIf { it >= 0 }?.let { agentMenu.setSelection(it) }
        main.post { showingAgents = false }
        agentMenu.isEnabled = sessions.isNotEmpty() && CallService.Bus.connected
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
                        say("", if (buttonOnly) "Open line off: hold the button to talk" else "Open line on: just talk", note = true)
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

    private fun showSound(clear: Boolean) {
        sound.text = if (clear) "Sound: clear" else "Sound: call"
    }

    private fun showConnected(on: Boolean) {
        callButton.text = if (on) "Hang up" else "Call"
        sound.isEnabled = on
        showSound(CallService.Bus.service?.clear ?: prefs().getBoolean("clear", true))
        talk.isEnabled = on
        text.isEnabled = on
        send.isEnabled = on
        agentMenu.isEnabled = on && sessions.isNotEmpty()
        if (!on) {
            state.text = "Not connected"
            showMode("waiting")
        }
    }

    /** The laptop tray's colours: grey while only the wake word gets in, then green, red, yellow. */
    private fun showMode(mode: String) {
        val colour = when (mode) {
            "listening" -> 0xFF3CB371
            "recording" -> 0xFFE24A4A
            "transcribing" -> 0xFFE0A100
            else -> 0xFF8E8C86
        }.toInt()
        talk.background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(colour) }
    }

    private fun say(who: String, words: String, mine: Boolean = false, note: Boolean = false, session: String = ""): TextView {
        val line = TextView(this).apply {
            text = if (who.isEmpty() || mine) words else "$who\n$words"
            setPadding(24, 14, 24, 14)
            gravity = if (note) Gravity.CENTER else if (mine) Gravity.END else Gravity.START
            alpha = if (note) 0.6f else 1f
            setTag(R.id.chat, session)
            visibility = if (session.isEmpty() || current.isEmpty() || session == current) View.VISIBLE else View.GONE
        }
        chat.addView(line, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT))
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        return line
    }

    companion object {
        const val HOLD_MS = 300L  // longer than this is a hold; shorter is a tap
    }
}
