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
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.json.JSONObject

/**
 * The call screen: the same messages to and from the call server as the call page. Your crew and the settings
 * live in a drawer from the left; each agent is a techno henchman of its own flavour (avatars.py). The call
 * itself runs in CallService; this screen watches it, and can be closed while the call goes on.
 */
class MainActivity : Activity() {

    private lateinit var listScreen: View
    private lateinit var chatScreen: View
    private lateinit var settingsScreen: View
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var quoteBar: TextView
    private var quote = ""  // the message being replied to (swiped right), sent along with the answer
    private lateinit var callButton: ImageButton
    private lateinit var talk: Button
    private lateinit var who: TextView
    private lateinit var avatar: ImageView
    private lateinit var state: TextView
    private lateinit var chat: LinearLayout
    private lateinit var chatScroll: ScrollView
    private lateinit var text: EditText
    private lateinit var send: ImageButton
    private lateinit var attach: ImageButton
    private lateinit var crew: LinearLayout
    private lateinit var clear: Switch

    private val main = Handler(Looper.getMainLooper())
    private var buttonOnly = false  // a noisy place: only holding the button gets in
    private var current = ""  // the agent replies go to now; its chat is the one shown
    private var names = mapOf<String, String>()  // session: the name the server gives it
    private var typed = 0  // ids for typed lines, so a "not sent" note strikes the right one
    private var talking = false
    private var pendingReply: String? = null  // an answered ring's report-back address, kept across set-up steps

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.main)
        listScreen = findViewById(R.id.listScreen)
        chatScreen = findViewById(R.id.chatScreen)
        settingsScreen = findViewById(R.id.settingsScreen)
        refresh = findViewById(R.id.refresh)
        quoteBar = findViewById(R.id.quote)
        refresh.setColorSchemeColors(getColor(R.color.cyan))
        refresh.setProgressBackgroundColorSchemeColor(getColor(R.color.surface))
        refresh.setOnRefreshListener { findCrew() }
        quoteBar.setOnClickListener { setQuote("") }  // tap it to stop replying
        findViewById<View>(R.id.profile).setOnClickListener { show(settingsScreen) }
        findViewById<View>(R.id.settingsBack).setOnClickListener { show(listScreen) }
        findViewById<TextView>(R.id.myName).apply {
            text = prefs().getString("my_name", "Boss")
            setOnClickListener { askName(this) }
        }
        callButton = findViewById(R.id.call)
        talk = findViewById(R.id.talk)
        who = findViewById(R.id.who)
        avatar = findViewById(R.id.avatar)
        state = findViewById(R.id.state)
        chat = findViewById(R.id.chat)
        chatScroll = findViewById(R.id.chatScroll)
        text = findViewById(R.id.text)
        send = findViewById(R.id.send)
        attach = findViewById(R.id.attach)
        crew = findViewById(R.id.crew)
        clear = findViewById(R.id.clear)
        showMode("waiting")
        current = prefs().getString("current", "") ?: ""
        names = try {  // the crew as last seen on a call: each agent's chat can be read and texted off a call
            val list = org.json.JSONArray(prefs().getString("crew", "[]"))
            (0 until list.length()).associate { list.getJSONObject(it).getString("session") to list.getJSONObject(it).getString("name") }
        } catch (_: Exception) { emptyMap() }
        showCrew()

        findViewById<ImageButton>(R.id.menu).setOnClickListener { show(listScreen) }
        callButton.setOnClickListener { if (CallService.Bus.connected) hangUp() else call() }
        findViewById<View>(R.id.serverRow).setOnClickListener { askServer { } }
        showClear(prefs().getBoolean("clear", true))
        setUpTalkButton()
        send.setOnClickListener { sendTyped() }
        attach.setOnClickListener { pickFiles() }
        text.setOnEditorActionListener { _, action, _ -> (action == EditorInfo.IME_ACTION_SEND).also { if (it) sendTyped() } }
        requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS,
            Manifest.permission.BLUETOOTH_CONNECT), 1)
        showHistory()
        show(if (CallService.Bus.connected) chatScreen else listScreen)  // home: the chat list
        RingService.register(this)  // so agents can ring this phone
        askFullScreen()
        DoNotDisturb.ask(this)
        answer(intent)
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, permissions, results)
        if (code != MIC_FOR_CALL) return
        val reply = pendingReply.also { pendingReply = null }
        if (results.firstOrNull() == PackageManager.PERMISSION_GRANTED) call(reply)
        else {
            Reply.send(reply, "unreachable")  // answered, but no microphone: the call can't be made
            say("", "The call needs the microphone: allow it, then call again.", note = true)
        }
    }

    /** The paperclip: pictures or any files, for the agent whose chat is showing (the picker offers Photos too). */
    private fun pickFiles() {
        startActivityForResult(Intent(Intent.ACTION_GET_CONTENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE)
            .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true), PICK_FILES)
    }

    @Deprecated("the platform Activity has no result API besides this")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        super.onActivityResult(code, result, data)
        if (code != PICK_FILES || result != RESULT_OK || data == null) return
        val uris = data.clipData?.let { clip -> (0 until clip.itemCount).map { clip.getItemAt(it).uri } }
            ?: listOfNotNull(data.data)
        if (uris.isEmpty()) return
        // The same sheet as sharing from another app (the user's wish, 2026-11-15): what it is, who gets it (this
        // chat's agent first), a caption; anything already typed in the box comes along as the caption
        val words = text.text.toString().trim()
        text.setText("")
        val crew = names.toList().ifEmpty { listOf(current to (prefs().getString("current_name", "") ?: "")) }
        sheet?.close()  // one at a time
        sheet = Sheet(this, uris, uris.map { Files.nameOf(this, it) }, "", crew, current, over = true, note0 = words,
            onSent = { to ->  // off a call, the chat follows where it went (on one, the laptop says who's current)
                if (!CallService.Bus.connected && to != current) {
                    current = to
                    prefs().edit().putString("current", current).putString("current_name", names[current] ?: "").apply()
                    showHistory(); showCrew()
                }
            }).also { it.show() }
    }

    /** What you attached, on your side of the chat: a picture small, anything else its name. */
    /** A picture you sent, from its small copy: shown on your side of the chat, its name under it. */
    private fun picture(thumb: String, caption: String, session: String) {
        val bubble = say("you", caption, mine = true, session = session, keep = false)
        val width = dp(220)
        val cached = pictures.get(thumb)
        if (cached != null) {
            bubble.setCompoundDrawablesWithIntrinsicBounds(null, android.graphics.drawable.BitmapDrawable(resources, cached), null, null)
            bubble.compoundDrawablePadding = dp(8)
            return
        }
        decoder.execute {  // decoded small, two at a time, off the main thread: a chat may hold many pictures
            val shown = try {
                val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
                android.graphics.BitmapFactory.decodeFile(thumb, bounds)
                var sample = 1
                while (bounds.outWidth / (sample * 2) >= width) sample *= 2
                val bitmap = android.graphics.BitmapFactory.decodeFile(thumb,
                    android.graphics.BitmapFactory.Options().apply { inSampleSize = sample })
                bitmap?.let {
                    android.graphics.Bitmap.createScaledBitmap(it, width, (it.height * width / it.width.coerceAtLeast(1)).coerceAtLeast(1), true)
                }
            } catch (_: Exception) {
                null
            } ?: return@execute
            pictures.put(thumb, shown)
            main.post {
                bubble.setCompoundDrawablesWithIntrinsicBounds(null, android.graphics.drawable.BitmapDrawable(resources, shown), null, null)
                bubble.compoundDrawablePadding = dp(8)
            }
        }
    }

    private var sheet: Sheet? = null  // the paperclip's share sheet, while it is up

    /** A swipe in from the left edge of a chat goes back to the list, as the old drawer's swipe did. */
    private var edgeStart = -1f

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (chatScreen.visibility == View.VISIBLE && sheet?.isOpen != true) when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> edgeStart = if (event.x < dp(24)) event.x else -1f
            MotionEvent.ACTION_UP -> {
                if (edgeStart >= 0 && event.x - edgeStart > dp(100)) { edgeStart = -1f; show(listScreen); return true }
                edgeStart = -1f
            }
        }
        return super.dispatchTouchEvent(event)
    }

    @Deprecated("the platform Activity's back handling, kept for Android 10-12")
    override fun onBackPressed() {
        when {  // back closes the sheet, then a chat or settings (to the list); only the list leaves the app
            sheet?.isOpen == true -> sheet?.close()
            listScreen.visibility != View.VISIBLE -> show(listScreen)
            else -> super.onBackPressed()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        answer(intent)
    }

    /** Opened from henchline://call (an agent rang): ask, as a phone does. Any app or web page can open the
     *  link, so it never turns the mic on by itself: only Answer connects. */
    private fun answer(intent: Intent?) {
        // The Quick Settings tile. Any app can open this screen, so only the tile's own key turns the mic on
        if (intent?.action == CALL_NOW) {
            intent.action = null
            if (intent.getStringExtra(CALL_KEY) == callKey(this)) call()
            return
        }
        if (intent?.action == OPEN_FILE) {
            openFromNotification(intent)
            return
        }
        if (intent?.action == ANSWER) {  // Answer on IncomingActivity or the ring's banner: the user's own tap
            intent.action = null
            Ringing.end(this)  // stop ringing, everywhere
            call(intent.getStringExtra(IncomingActivity.REPLY))
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
        if (CallService.Bus.connected) {  // what was said while this screen wasn't looking
            CallService.Bus.lastAgents?.let { fromServer(it) }
            CallService.Bus.lastMode?.let { fromServer(it) }
        }
        showHistory()  // what came meanwhile (a call's lines, a text): from the kept chat, on a call or off it
        showServer()
        showRinging()
    }

    /** Opened while an agent's ring still sounds: say so, with Answer and Decline. */
    private fun showRinging() {
        val ring = Ringing.now(this) ?: return
        if (CallService.Bus.service != null || ringingShown?.isShowing == true) return
        val reply = ring.optString("reply").ifEmpty { null }
        ringingShown = AlertDialog.Builder(this)
            .setIcon(avatarOf(ring.optString("agent")))
            .setTitle("${IncomingActivity.nameOf(ring.optString("agent"))} is calling")
            .setMessage(ring.optString("why").ifEmpty { "Your crew wants to talk." })
            .setPositiveButton("Answer") { _, _ -> call(reply) }
            .setNegativeButton("Decline") { _, _ -> Reply.send(reply, "declined"); Ringing.end(this) }
            .show()
    }

    private var ringingShown: AlertDialog? = null

    private fun showServer() {
        findViewById<TextView>(R.id.server).text =
            prefs().getString("server", "")?.removePrefix("https://")?.trimEnd('/')?.ifEmpty { null }
                ?: "Set the laptop's address"
    }

    override fun onPause() {
        CallService.Bus.listener = null  // the call goes on; this screen just stops drawing it
        super.onPause()
    }

    private fun call(given: String? = null) {
        // A call made by hand while a ring sounds answers that ring: the laptop routes it to the agent that rang
        val reply = given ?: Ringing.now(this)?.optString("reply")?.ifEmpty { null }
        Ringing.end(this)
        if (CallService.Bus.service != null) {  // already connecting: one call at a time
            state.text = "Connecting…"
            return
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            pendingReply = reply ?: pendingReply  // the call is made when the microphone is allowed
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), MIC_FOR_CALL)
            return
        }
        val server = prefs().getString("server", "") ?: ""
        if (server.isEmpty()) {
            askServer { call(reply) }
            return
        }
        state.text = "Connecting…"
        show(chatScreen)
        startForegroundService(Intent(this, CallService::class.java).putExtra(CallService.SERVER, server)
            .putExtra(CallService.CLEAR, prefs().getBoolean("clear", true)).putExtra(IncomingActivity.REPLY, reply))
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
                val file = line.optJSONObject("file")
                // The call has kept it already (CallService): drawn here only
                if (file == null || line.optString("text").isNotEmpty())
                    say(from, line.getString("text"), mine = from == "you", session = line.optString("session"), keep = false)
                if (file != null) fileCard(from, file, line.optString("session"), keep = false)
            }
            status.has("agents") -> showAgents(status)
            status.has("reload") -> showHistory()  // a share was put in the kept chat (UploadService)
            status.has("talking") ->  // the earbuds or the notification started or ended a turn
                android.widget.Toast.makeText(this, if (status.getBoolean("talking")) "Talking: tap again to send" else "Sent",
                    android.widget.Toast.LENGTH_SHORT).show()
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
        prefs().edit().putString("crew", list.toString()).apply()  // to pick from, and text, off a call
        val now = status.optString("current")
        if (now.isNotEmpty() && now != current) {
            current = now
            prefs().edit().putString("current", current).putString("current_name", names[current] ?: "").apply()
            showHistory()
        }
        showCrew()
    }

    /** The chat list (the home screen): each agent with its henchman, its name and workspace, its last line and
     *  when, newest first; the one you're talking to marked. Tap one to open its chat. */
    private fun showCrew() {
        crew.removeAllViews()
        if (names.isEmpty()) {
            crew.addView(TextView(this).apply {
                text = "No crew yet. Pull down to look for agents running on your computer, or call once."
                setTextColor(getColor(R.color.muted))
                setPadding(dp(20), dp(24), dp(20), dp(24))
            })
        }
        val lasts = names.keys.associateWith { Chats.load(this, it).lastOrNull() }
        for ((session, name) in names.entries.sortedByDescending { lasts[it.key]?.optLong("at") ?: 0 }.map { it.key to it.value }) {
            val last = lasts[session]
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(20), dp(12), dp(20), dp(12))
                background = getDrawable(if (session == current) R.drawable.row_current else android.R.color.transparent)
                setOnClickListener { open(session) }
            }
            row.addView(ImageView(this).apply { setImageResource(avatarOf(session)) }, LinearLayout.LayoutParams(dp(52), dp(52)))
            val words = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, 0, 0) }
            val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            top.addView(TextView(this).apply {
                text = name.substringBefore(",")
                setTextColor(getColor(R.color.text)); textSize = 17f; typeface = android.graphics.Typeface.DEFAULT_BOLD
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            top.addView(TextView(this).apply {
                text = last?.optLong("at")?.takeIf { it > 0 }?.let { android.text.format.DateUtils.getRelativeTimeSpanString(it).toString() } ?: ""
                setTextColor(getColor(R.color.muted)); textSize = 12f
            })
            words.addView(top)
            words.addView(TextView(this).apply {
                val workspace = name.substringAfter(", ", "").removePrefix("in ")
                val line = last?.let { (if (it.optBoolean("mine")) "You: " else "") + it.optString("text").ifEmpty { "📎 file" } } ?: ""
                text = listOf(workspace, line).filter { it.isNotEmpty() }.joinToString(" · ")
                setTextColor(getColor(R.color.muted)); textSize = 14f; maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            row.addView(words, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            crew.addView(row)
        }
        val name = names[current] ?: prefs().getString("current_name", "")?.ifEmpty { null }.takeIf { current.isNotEmpty() }
        who.text = name?.substringBefore(",") ?: "Henchline"
        avatar.setImageResource(if (name != null) avatarOf(current) else R.drawable.avatar_handsfree)
    }

    private fun setUpTalkButton() {
        val hold = Runnable {
            talking = true
            CallService.Bus.service?.send("""{"push": true}""")
            CallService.Bus.service?.beep(true)
        }
        talk.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> main.postDelayed(hold, HOLD_MS)
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    main.removeCallbacks(hold)
                    if (talking) {
                        talking = false
                        CallService.Bus.service?.send("""{"push": false}""")
                        CallService.Bus.service?.beep(false)
                    } else if (event.actionMasked == MotionEvent.ACTION_UP) {
                        buttonOnly = !buttonOnly
                        CallService.Bus.service?.send(JSONObject().put("button_only", buttonOnly).toString())
                        // A brief notice, not a chat line: these piled up in the chat (2026-11-15); the top bar says it too
                        android.widget.Toast.makeText(this, if (buttonOnly) "Open line off: hold to talk" else "Open line on: just talk",
                            android.widget.Toast.LENGTH_SHORT).show()
                        view.performClick()
                    }
                }
            }
            true
        }
    }

    private fun sendTyped() {
        val entered = text.text.toString().trim()
        if (entered.isEmpty()) return
        // Replying to a message: it goes along, on the same line (a newline would send early in a terminal)
        val words = if (quote.isEmpty()) entered else "(replying to: \"${quote.replace("\n", " ").take(160)}\") $entered"
        setQuote("")
        if (!CallService.Bus.connected) {  // off a call: a text, to the agent whose chat this is
            if (current.isEmpty()) return
            val bubble = say("you", words, mine = true, session = current)
            text.setText("")
            val to = current
            Thread {
                val note = Messages.text(this, to, words)
                if (note.isNotEmpty()) main.post {
                    bubble.alpha = 0.5f
                    bubble.paintFlags = bubble.paintFlags or Paint.STRIKE_THRU_TEXT_FLAG
                    say("", note, note = true)
                }
            }.start()
            return
        }
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
            if (service != null) android.widget.Toast.makeText(this, if (flipped) "Clear sound" else "Call sound",
                android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    private fun showConnected(on: Boolean) {
        callButton.setImageResource(if (on) R.drawable.ic_call_end else R.drawable.ic_phone)
        callButton.setBackgroundResource(if (on) R.drawable.round_hang else R.drawable.round_call)
        callButton.contentDescription = if (on) "Hang up" else "Call"
        // Off a call the message box stays when an agent's chat is shown: what is typed (or attached) is a text
        val typing = on || current.isNotEmpty()
        talk.isEnabled = on
        text.isEnabled = typing
        send.isEnabled = typing
        attach.isEnabled = typing
        val name = names[current] ?: prefs().getString("current_name", "")?.ifEmpty { null }
        text.hint = if (on) "Message your crew…" else "Text ${name?.substringBefore(",") ?: "this agent"}…"
        // Off a call there is nothing to hold or type to: just the henchman and how to call
        val shown = if (on) View.VISIBLE else View.GONE
        talk.visibility = shown
        findViewById<View>(R.id.inputRow).visibility = if (typing) View.VISIBLE else View.GONE
        // Off a call: the kept chat with the last agent, read-only, or the henchman and how to call
        val readable = on || chat.childCount > 0
        chatScroll.visibility = if (readable) View.VISIBLE else View.GONE
        findViewById<View>(R.id.idle).visibility = if (readable) View.GONE else View.VISIBLE
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
    private fun say(from: String, words: String, mine: Boolean = false, note: Boolean = false, session: String = "",
                    keep: Boolean = true): TextView {
        if (keep && !note) Chats.add(this, session, JSONObject().put("from", from).put("text", words).put("mine", mine))
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
        if (!note) swipeToReply(row, words)
        row.addView(bubble, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT).apply { if (!note) width = LinearLayout.LayoutParams.WRAP_CONTENT })
        if (mine && !note) {  // you: the boss the crew works for
            row.addView(ImageView(this).apply { setImageResource(R.drawable.avatar_you) },
                LinearLayout.LayoutParams(dp(28), dp(28)).apply { marginStart = dp(8) })
        }
        bubble.maxWidth = (resources.displayMetrics.widthPixels * 0.7).toInt()
        chat.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT))
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        return bubble
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    /** One screen at a time: the chat list, a chat, or settings. */
    private fun show(screen: View) {
        for (s in listOf(listScreen, chatScreen, settingsScreen)) s.visibility = if (s === screen) View.VISIBLE else View.GONE
        if (screen === listScreen) showCrew()
    }

    /** Into an agent's chat from the list: on a call the laptop switches to it (and says so); off one, its chat
     *  is shown and texts go to it. */
    private fun open(session: String) {
        if (session != current) {
            if (CallService.Bus.connected) CallService.Bus.service?.send(JSONObject().put("switch", session).toString())
            else {
                current = session
                prefs().edit().putString("current", current).putString("current_name", names[current] ?: "").apply()
                showHistory()
            }
        }
        showCrew()
        show(chatScreen)
    }

    /** Pull down on the list: the laptop looks for every agent running (as "switch me to" does) and lists them. */
    private fun findCrew() {
        val origin = Files.origin(this) ?: run { refresh.isRefreshing = false; return say("", "Set the laptop's address in Settings first.", note = true).let {} }
        Thread {
            val found = try {
                Files.client.newCall(okhttp3.Request.Builder().url("$origin/crew").header("Origin", origin).build())
                    .execute().use { org.json.JSONObject(it.body?.string() ?: "{}").optJSONArray("agents") }
            } catch (_: Exception) { null }
            main.post {
                refresh.isRefreshing = false
                if (found == null) {
                    android.widget.Toast.makeText(this, "Couldn't reach the laptop (is Tailscale on?)", android.widget.Toast.LENGTH_SHORT).show()
                    return@post
                }
                names = (0 until found.length()).associate { found.getJSONObject(it).getString("session") to found.getJSONObject(it).getString("name") }
                prefs().edit().putString("crew", found.toString()).apply()
                showCrew()
            }
        }.start()
    }

    /** Replying to a message: shown above the box until sent, or tapped away. */
    private fun setQuote(words: String) {
        quote = words
        quoteBar.text = "↩ " + words
        quoteBar.visibility = if (words.isEmpty()) View.GONE else View.VISIBLE
        if (words.isNotEmpty()) text.requestFocus()
    }

    /** A message dragged to the right far enough is the one being replied to, as in WhatsApp. */
    private fun swipeToReply(row: View, words: String) {
        var startX = 0f
        var dragging = false
        row.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> { startX = event.rawX; dragging = false; false }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - startX
                    if (dx > dp(12)) { dragging = true; view.parent.requestDisallowInterceptTouchEvent(true) }
                    if (dragging) view.translationX = dx.coerceIn(0f, dp(96).toFloat())
                    dragging
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    if (dragging && view.translationX >= dp(72)) setQuote(words)
                    view.animate().translationX(0f).setDuration(150).start()
                    dragging.also { dragging = false }
                }
                else -> false
            }
        }
    }

    private fun askName(view: TextView) {
        val box = EditText(this).apply { setText(view.text) }
        AlertDialog.Builder(this).setTitle("Your name").setView(LinearLayout(this).apply { setPadding(48, 16, 48, 0); addView(box) })
            .setPositiveButton("Save") { _, _ ->
                val name = box.text.toString().trim().ifEmpty { "Boss" }
                prefs().edit().putString("my_name", name).apply()
                view.text = name
            }
            .setNegativeButton("Cancel", null).show()
    }

    /** The current agent's kept chat, from the start: on opening the app, and on switching agents. */
    private fun showHistory() {
        chat.removeAllViews()
        for (line in Chats.load(this, current)) {
            val file = line.optJSONObject("file")
            if (file != null) fileCard(line.optString("from"), file, current, keep = false)
            else if (line.has("thumb")) picture(line.getString("thumb"), line.optString("text"), current)
            else say(line.optString("from"), line.optString("text"), mine = line.optBoolean("mine"), session = current, keep = false)
        }
        if (!CallService.Bus.connected) showConnected(false)  // off a call: the chat if there is one, else the henchman
    }

    /** A file an agent sent: a picture shown small (tap for it whole), audio with its own play button, anything
     *  else as a card that saves and opens it. Long-press any of them to save it and open it in another app. */
    private fun fileCard(from: String, file: JSONObject, session: String, keep: Boolean = true) {
        if (keep) Chats.add(this, session, JSONObject().put("from", from).put("file", file))
        val url = file.getString("url")
        val name = file.optString("name", "file")
        val type = file.optString("type", "application/octet-stream")
        val size = file.optLong("size")
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundResource(R.drawable.bubble_agent)
            setPadding(dp(10), dp(10), dp(10), dp(10))
            setOnLongClickListener { Files.open(this@MainActivity, url, name, type) { say("", it, note = true) }; true }
        }
        val label = TextView(this).apply {
            text = "$name · ${Files.sizeText(size)}"
            setTextColor(getColor(R.color.muted))
            textSize = 13f
        }
        when {
            type.startsWith("image/") -> {
                val picture = ImageView(this).apply {
                    adjustViewBounds = true
                    maxHeight = dp(280)
                    scaleType = ImageView.ScaleType.FIT_START
                    setOnClickListener { showPicture(url, name, type) }
                }
                card.addView(picture, LinearLayout.LayoutParams(dp(240), LinearLayout.LayoutParams.WRAP_CONTENT))
                Thread {
                    val bitmap = Files.preview(this, url, dp(240))
                    main.post { if (bitmap != null) picture.setImageBitmap(bitmap) else label.text = "$name (couldn't load the preview: tap and hold to download)" }
                }.start()
                card.addView(label)
            }
            type.startsWith("audio/") -> card.addView(audioRow(url, name, size))
            else -> {
                label.text = "📎 $name · ${Files.sizeText(size)}\nTap to open"
                label.setTextColor(getColor(R.color.text))
                label.textSize = 15f
                card.setOnClickListener { Files.open(this, url, name, type) { say("", it, note = true) } }
                card.addView(label)
            }
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.START or Gravity.BOTTOM
            setPadding(0, dp(4), 0, dp(4))
            setTag(R.id.chat, session)
            visibility = if (session.isEmpty() || current.isEmpty() || session == current) View.VISIBLE else View.GONE
        }
        row.addView(ImageView(this).apply { setImageResource(avatarOf(session.ifEmpty { from })) },
            LinearLayout.LayoutParams(dp(28), dp(28)).apply { marginEnd = dp(8) })
        row.addView(card)
        chat.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT))
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
    }

    /** Play and pause, streamed from the laptop: one sound at a time. On a call it plays alongside the agents'
     *  voices (it asks for no audio focus of its own, so the call isn't paused for it). */
    private fun audioRow(url: String, name: String, size: Long): View {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val button = ImageButton(this).apply {
            setImageResource(android.R.drawable.ic_media_play)
            setBackgroundResource(R.drawable.round_call)
            backgroundTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.cyan))
            contentDescription = "Play $name"
        }
        val label = TextView(this).apply {
            text = "$name\n${Files.sizeText(size)}"
            setTextColor(getColor(R.color.text))
            textSize = 14f
            setPadding(dp(12), 0, 0, 0)
        }
        row.addView(button, LinearLayout.LayoutParams(dp(44), dp(44)))
        row.addView(label, LinearLayout.LayoutParams(dp(190), LinearLayout.LayoutParams.WRAP_CONTENT))
        button.setOnClickListener { playOrPause(url, button, label, name) }
        return row
    }

    private var sound: android.media.MediaPlayer? = null
    private var soundUrl = ""
    private var soundButton: ImageButton? = null

    private fun playOrPause(url: String, button: ImageButton, label: TextView, name: String) {
        val playing = sound
        if (playing != null && soundUrl == url) {
            if (playing.isPlaying) { playing.pause(); button.setImageResource(android.R.drawable.ic_media_play) }
            else { playing.start(); button.setImageResource(android.R.drawable.ic_media_pause) }
            return
        }
        stopSound()
        val address = Files.resolve(this, url) ?: return
        soundUrl = url
        soundButton = button
        label.text = "$name\nLoading…"
        sound = android.media.MediaPlayer().apply {
            setAudioAttributes(android.media.AudioAttributes.Builder().setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                .setContentType(android.media.AudioAttributes.CONTENT_TYPE_MUSIC).build())
            setOnPreparedListener {
                label.text = "$name\n${it.duration / 60000}:${"%02d".format(it.duration / 1000 % 60)}"
                it.start()
                button.setImageResource(android.R.drawable.ic_media_pause)
            }
            setOnCompletionListener { button.setImageResource(android.R.drawable.ic_media_play) }
            setOnErrorListener { _, _, _ ->
                label.text = "$name\nCouldn't play it here: tap and hold to download"
                stopSound()
                true
            }
            try {
                setDataSource(address)
                prepareAsync()
            } catch (e: Exception) {
                label.text = "$name\nCouldn't play it: ${e.message}"
            }
        }
    }

    private fun stopSound() {
        sound?.release()
        sound = null
        soundUrl = ""
        soundButton?.setImageResource(android.R.drawable.ic_media_play)
        soundButton = null
    }

    /** A picture whole, over the call screen; the button saves it and opens it in another app. */
    private fun showPicture(url: String, name: String, type: String) {
        val picture = ImageView(this).apply { adjustViewBounds = true; setBackgroundColor(0xFF000000.toInt()) }
        val dialog = AlertDialog.Builder(this)
            .setTitle(name)
            .setView(picture)
            .setPositiveButton("Save and open") { _, _ -> Files.open(this, url, name, type) { say("", it, note = true) } }
            .setNegativeButton("Close", null)
            .show()
        Thread {
            val bitmap = Files.preview(this, url, resources.displayMetrics.widthPixels)
            main.post { if (dialog.isShowing && bitmap != null) picture.setImageBitmap(bitmap) }
        }.start()
    }

    /** A file from a notification (no call up, or the call screen wasn't showing): pictures and audio here,
     *  anything else saved and opened. */
    private fun openFromNotification(intent: Intent) {
        val url = intent.getStringExtra(Files.URL) ?: return
        val name = intent.getStringExtra(Files.NAME) ?: "file"
        val type = intent.getStringExtra(Files.TYPE) ?: "application/octet-stream"
        intent.action = null
        val note: (String) -> Unit = { android.widget.Toast.makeText(this, it, android.widget.Toast.LENGTH_SHORT).show() }
        when {
            type.startsWith("image/") -> showPicture(url, name, type)
            type.startsWith("audio/") -> {
                val holder = LinearLayout(this).apply { setPadding(dp(24), dp(16), dp(24), 0) }
                val row = audioRow(url, name, intent.getLongExtra(Files.SIZE, 0)) as LinearLayout
                holder.addView(row)
                AlertDialog.Builder(this).setTitle(name).setView(holder)
                    .setPositiveButton("Save and open") { _, _ -> Files.open(this, url, name, type, note) }
                    .setNegativeButton("Close", null)
                    .setOnDismissListener { stopSound() }
                    .show()
                row.getChildAt(0).performClick()  // it starts playing
            }
            else -> Files.open(this, url, name, type, note)
        }
    }

    companion object {
        const val HOLD_MS = 300L  // longer than this is a hold; shorter is a tap
        const val ANSWER = "io.github.ninjatuna007.henchline.ANSWER"  // from IncomingActivity's Answer
        const val MIC_FOR_CALL = 2  // the permission request a call waits on
        const val PICK_FILES = 3  // the paperclip's picker
        // Pictures in the chat: decoded two at a time and kept a while, for the app's whole life (not per screen)
        private val decoder = java.util.concurrent.Executors.newFixedThreadPool(2)
        private val pictures = object : android.util.LruCache<String, android.graphics.Bitmap>(16 * 1024 * 1024) {  // 16 MB
            override fun sizeOf(key: String, value: android.graphics.Bitmap) = value.byteCount
        }
        const val CALL_NOW = "io.github.ninjatuna007.henchline.CALL_NOW"  // the Quick Settings tile
        const val CALL_KEY = "call_key"

        /** A secret of this install's, which only the tile knows to send. */
        fun callKey(context: android.content.Context): String {
            val prefs = context.getSharedPreferences("call", MODE_PRIVATE)
            return prefs.getString(CALL_KEY, null) ?: java.util.UUID.randomUUID().toString().also {
                prefs.edit().putString(CALL_KEY, it).apply()
            }
        }
        const val OPEN_FILE = "io.github.ninjatuna007.henchline.OPEN_FILE"  // a file's notification, tapped

        /** Each agent's henchman, by its session (claude-…, cursor-…, codex-…) or its name. */
        fun avatarOf(sessionOrName: String): Int = when {
            sessionOrName.startsWith("claude", ignoreCase = true) -> R.drawable.avatar_claude
            sessionOrName.startsWith("cursor", ignoreCase = true) -> R.drawable.avatar_cursor
            sessionOrName.startsWith("codex", ignoreCase = true) -> R.drawable.avatar_codex
            else -> R.drawable.avatar_handsfree
        }
    }
}
