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
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
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
    private lateinit var search: EditText
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
    private var current = ""  // the chat shown: texts and voice messages go to its agent
    // The call and the chats are apart, as WhatsApp's (the user's wish, 2026-11-16): the call is with callSession;
    // any other chat can be read and texted meanwhile, and a bar leads back to the call
    private var callSession = ""
    private var callFrom = ""  // the chat a call was started from: the call goes to its agent

    /** The chat shown is the call's: the talk button, hang-up and the call's state belong to it. */
    private fun onCall() = CallService.Bus.connected && (callSession.isEmpty() || current == callSession)
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
        search = findViewById(R.id.search)
        search.addTextChangedListener(object : android.text.TextWatcher {
            // After a short pause in typing, not on every key (2026-11-16 review)
            override fun afterTextChanged(e: android.text.Editable?) {
                main.removeCallbacks(searchSoon)
                main.postDelayed(searchSoon, 250)
            }
            override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
            override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
        })
        findViewById<TextView>(R.id.sort).apply {
            fun label() = mapOf("recent" to "Recent", "project" to "By project", "agent" to "By agent")[sortMode()]
            text = label()
            setOnClickListener {
                val next = mapOf("recent" to "project", "project" to "agent", "agent" to "recent")[sortMode()]
                prefs().edit().putString("sort", next).apply()
                text = label()
                showCrew()
            }
        }
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
        findViewById<View>(R.id.compose).setOnClickListener { newChat() }
        findViewById<Switch>(R.id.thoughtsSwitch).apply {
            isChecked = prefs().getBoolean("thoughts", true)
            setOnCheckedChangeListener { _, on -> prefs().edit().putBoolean("thoughts", on).apply() }
        }
        for (id in listOf(R.id.callBarList, R.id.callBarChat)) findViewById<View>(id).setOnClickListener { openCall() }
        findViewById<View>(R.id.modelChip).setOnClickListener { switchModel() }
        findViewById<View>(R.id.tabChats).setOnClickListener { showCalls(false) }
        findViewById<View>(R.id.tabCalls).setOnClickListener { showCalls(true) }
        callButton.setOnClickListener {
            when {
                onCall() -> hangUp()
                CallService.Bus.connected -> openCall()  // one call at a time: back to it
                else -> call()
            }
        }
        val pickForCall = View.OnClickListener { if (onCall()) switchCall() }
        who.setOnClickListener(pickForCall)
        avatar.setOnClickListener(pickForCall)
        findViewById<View>(R.id.serverRow).setOnClickListener { askServer { } }
        showClear(prefs().getBoolean("clear", true))
        setUpTalkButton()
        send.setOnClickListener { sendTyped() }
        setUpVoiceNotes()
        attach.setOnClickListener {
            if (locked) endVoiceNote(keep = false, why = "Dropped")
            else panel = AddSheet(this) { what ->
                when (what) { "Camera" -> takePhoto(); "Photos" -> pickPhotos(); else -> pickFiles() }
            }.also { it.show() }
        }
        findViewById<View>(R.id.stop).setOnClickListener { stopAgent() }
        text.setOnEditorActionListener { _, action, _ -> (action == EditorInfo.IME_ACTION_SEND).also { if (it) sendTyped() } }
        requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS,
            Manifest.permission.BLUETOOTH_CONNECT), 1)
        showHistory()
        if (CallService.Bus.connected) openCall() else show(listScreen)  // home: the chat list
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

    /** Photos: the phone's photo picker (no permission needed), several at once. */
    private fun pickPhotos() {
        val intent = if (android.os.Build.VERSION.SDK_INT >= 33)
            Intent(android.provider.MediaStore.ACTION_PICK_IMAGES).putExtra(android.provider.MediaStore.EXTRA_PICK_IMAGES_MAX, 20)
        else Intent(Intent.ACTION_GET_CONTENT).setType("image/*").putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        startActivityForResult(intent, PICK_FILES)
    }

    private var photo: android.net.Uri? = null  // where the camera puts the picture it takes

    /** Camera: a picture taken now, into the phone's Pictures/Henchline, then shared like any picked file. */
    private fun takePhoto() {
        val values = android.content.ContentValues().apply {
            put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, "henchline-${System.currentTimeMillis()}.jpg")
            put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/Henchline")
        }
        photo = contentResolver.insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return
        try {
            startActivityForResult(Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE)
                .putExtra(android.provider.MediaStore.EXTRA_OUTPUT, photo), TAKE_PHOTO)
        } catch (_: android.content.ActivityNotFoundException) {
            contentResolver.delete(photo!!, null, null); photo = null
            android.widget.Toast.makeText(this, "No camera app", android.widget.Toast.LENGTH_SHORT).show()
        }
    }

    @Deprecated("the platform Activity has no result API besides this")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        super.onActivityResult(code, result, data)
        if (code == TAKE_PHOTO) {
            val taken = photo.also { photo = null } ?: return
            if (result == RESULT_OK) attachUris(listOf(taken)) else contentResolver.delete(taken, null, null)
            return
        }
        if (code != PICK_FILES || result != RESULT_OK || data == null) return
        val uris = data.clipData?.let { clip -> (0 until clip.itemCount).map { clip.getItemAt(it).uri } }
            ?: listOfNotNull(data.data)
        attachUris(uris)
    }

    private fun attachUris(uris: List<android.net.Uri>) {
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
            }, onClosed = { showHistory() }).also { it.show() }  // closed: what came meanwhile is read now
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
            panel?.isOpen == true -> panel?.close()
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
        showConnected(onCall())
        if (CallService.Bus.connected) {  // what was said while this screen wasn't looking
            CallService.Bus.lastAgents?.let { fromServer(it) }
            CallService.Bus.lastMode?.let { fromServer(it) }
        }
        if (chatScreen.visibility == View.VISIBLE) {
            Messages.viewing = current
            showModel(current)  // the model may have been changed on the laptop meanwhile (in Cursor itself)
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
            .setNegativeButton("Decline") { _, _ -> Reply.send(reply, "declined"); Calls.outcome(this, reply, "declined"); Ringing.end(this) }
            .show()
    }

    private var ringingShown: AlertDialog? = null

    private fun showServer() {
        findViewById<TextView>(R.id.server).text =
            prefs().getString("server", "")?.removePrefix("https://")?.trimEnd('/')?.ifEmpty { null }
                ?: "Set the laptop's address"
    }

    override fun onPause() {
        Messages.viewing = ""  // not on screen: texts get their banner again
        CallService.Bus.listener = null  // the call goes on; this screen just stops drawing it
        // Leaving mid-recording: the mic is never left open with nothing on screen. Held, it is dropped (the
        // finger left with the app); locked, it was meant to be sent
        if (voiceNote != null) endVoiceNote(keep = locked, why = "Dropped: you left the app")
        super.onPause()
    }

    override fun onDestroy() {
        main.removeCallbacks(recordClock)
        lockHint?.dismiss(); lockHint = null
        super.onDestroy()
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
        callFrom = current  // the call goes to the agent of the chat it was started from
        callSession = current
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
                // Started from a chat: the call is with that chat's agent
                if (callFrom.isNotEmpty()) CallService.Bus.service?.send(JSONObject().put("switch", callFrom).toString())
                callSession = callFrom
                callFrom = ""
                showConnected(onCall())
                showCallBar()
                if (buttonOnly) CallService.Bus.service?.send(JSONObject().put("button_only", true).toString())
            }
            status.has("closed") -> {
                callSession = ""
                showConnected(false)
                showCallBar()
                say("", status.getString("closed"), note = true)
            }
            status.has("chat") -> {
                val line = status.getJSONObject("chat")
                // On the chat list, an arriving line shows there at once: bold, with its unread count (a text
                // reached the banner but not the list, 2026-11-16)
                if (listScreen.visibility == View.VISIBLE) main.post { showCrew() }
                val from = line.getString("from")
                val file = line.optJSONObject("file")
                if (from != "you" && line.optString("session") == current && file == null) {
                    // Added at the end, your ticks before it turned blue in place: no redraw, so the chat
                    // doesn't jump (a full redraw shifted it, 2026-11-16)
                    say(from, line.getString("text"), session = current, keep = false)
                    for ((bubble, words) in myBubbles) bubble.text = stamp(bubble, words, "read")
                    myBubbles.clear()
                    main.post { refreshStop() }  // it answered: nothing to stop
                    if (chatScreen.visibility == View.VISIBLE) {
                        if (line.has("tid")) Messages.seen(this, current)
                        markRead(current)
                    }
                    return
                }
                // The call has kept it already (CallService): drawn here only
                if (file == null || line.optString("text").isNotEmpty())
                    say(from, line.getString("text"), mine = from == "you", session = line.optString("session"), keep = false,
                        status = if (from == "you") "delivered" else "")
                if (file != null) fileCard(from, file, line.optString("session"), keep = false)
                // An agent's text arriving in the chat you're looking at: read at once
                if (line.optString("session") == current && chatScreen.visibility == View.VISIBLE) {
                    if (line.has("tid")) Messages.seen(this, current)
                    markRead(current)
                }
            }
            status.has("agents") -> showAgents(status)
            status.has("delivered") -> tick(status.getString("delivered"), "delivered")  // in place: no jump
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
                if (onCall()) state.text = subtitle(status.optString("state"))
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
        val following = current.isEmpty() || current == callSession  // looking at the call: it follows the switch
        if (now.isNotEmpty() && now != callSession) {
            callSession = now
            if (following && now != current) {
                current = now
                prefs().edit().putString("current", current).putString("current_name", names[current] ?: "").apply()
                showHistory()
            }
        }
        showConnected(onCall())
        showCallBar()
        showCrew()
    }

    /** The chat list (the home screen). Each chat is named by its project, its henchman's face and name saying
     *  which agent (the user's choice, 2026-11-16). Ordered newest first, or grouped by project or by agent; the
     *  search box matches project and agent names first, then what was said, showing the matching line. */
    private fun showCrew() {
        crew.removeAllViews()
        main.removeCallbacks(searchSoon)
        if (showingCalls) return showCalls()
        val query = if (::search.isInitialized) search.text.toString().trim() else ""
        if (names.isEmpty()) {
            crew.addView(TextView(this).apply {
                text = "No crew yet. Pull down to look for agents running on your computer, or call once."
                setTextColor(getColor(R.color.muted))
                setPadding(dp(20), dp(24), dp(20), dp(24))
            })
        }
        class Chat(val session: String, val agent: String, val project: String, val last: JSONObject?, val found: String)
        fun project(name: String) = name.substringAfter(", ", "").removePrefix("in ").ifEmpty { "home" }
        var chats = names.map { (session, name) -> Chat(session, name.substringBefore(","), project(name), Chats.last(this, session), "") }
        // Chats an agent texted lately that the laptop's crew list doesn't name: named from the session ("cursor-_tmp");
        // older ones stay out of the list (long-press clears one that's in it). Not hands-free's own: it is the
        // switchboard, not an agent (its "Now talking to …" notices from calls, 2026-11-16)
        chats = chats + Chats.sessions(this).filter { s -> s !in names && !s.startsWith("handsfree-") && (Chats.last(this, s)?.optLong("at") ?: 0) > System.currentTimeMillis() - 3 * 86_400_000L }.map { session ->
            val agent = session.substringBefore("-").replaceFirstChar { it.uppercase() }
            Chat(session, agent, "not on the crew list", Chats.last(this, session), "")  // its key can't say its folder
        }
        if (query.isNotEmpty()) {
            val q = query.lowercase()
            val byName = chats.filter { q in it.project.lowercase() || q in it.agent.lowercase() }
            if (byName.isEmpty()) {  // nothing by name: a deeper look, in what was said, off the main thread
                val done = searched?.takeIf { it.first == q }?.second
                if (done == null) {
                    val sessions = chats.map { it.session }
                    Thread {
                        val hits = sessions.mapNotNull { session ->
                            Chats.load(this, session).lastOrNull { q in it.optString("text").lowercase() }
                                ?.let { session to it.optString("text") }
                        }.toMap()
                        main.post {
                            searched = q to hits
                            if (search.text.toString().trim().lowercase() == q) showCrew()
                        }
                    }.start()
                    crew.addView(TextView(this).apply {
                        text = "Searching the chats…"; setTextColor(getColor(R.color.muted)); setPadding(dp(20), dp(24), dp(20), dp(24))
                    })
                    return
                }
                chats = chats.mapNotNull { c -> done[c.session]?.let { Chat(c.session, c.agent, c.project, c.last, it) } }
            } else chats = byName
            if (chats.isEmpty()) crew.addView(TextView(this).apply {
                text = "Nothing matches \"$query\"."; setTextColor(getColor(R.color.muted)); setPadding(dp(20), dp(24), dp(20), dp(24))
            })
        }
        val recent = chats.sortedByDescending { it.last?.optLong("at") ?: 0 }
        val groups: List<Pair<String, List<Chat>>> = when (sortMode()) {
            "project" -> recent.groupBy { it.project }.toList()
            "agent" -> recent.groupBy { it.agent }.toList()
            else -> listOf("" to recent)
        }
        for ((heading, members) in groups) {
            if (heading.isNotEmpty()) crew.addView(TextView(this).apply {
                text = heading.replace("_", " ").uppercase()
                setTextColor(getColor(R.color.muted)); textSize = 12f; typeface = android.graphics.Typeface.DEFAULT_BOLD
                setPadding(dp(20), dp(16), dp(20), dp(4))
            })
            for (chat in members) crew.addView(chatRow(chat.session, chat.agent, chat.project, chat.last, chat.found))
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

    private var voiceNote: VoiceNote? = null  // recording while the mic is held, or locked
    private var locked = false  // slid up: recording on without holding, sent with the button, dropped with ✕
    private var lockHint: android.widget.PopupWindow? = null  // the lock above the mic, while it is held
    private var tapped = false  // a new touch on the locked button
    private var downX = 0f
    private var downY = 0f

    /** Off a call, with the box empty, the send button is a mic, as in WhatsApp (the user's wish, 2026-11-16): hold
     *  to record and let go to send; slide left to drop it; slide up to lock it on, then the button sends and ✕
     *  drops. The button follows the finger, so it feels like something is happening. The laptop transcribes the
     *  message and types it to this chat's agent. */
    private fun setUpVoiceNotes() {
        val swap = { if (voiceNote == null) send.setImageResource(if (micMode()) R.drawable.ic_mic else R.drawable.ic_send) }
        text.addTextChangedListener(object : android.text.TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: android.text.Editable?) = swap()
        })
        swap()
        send.setOnTouchListener { _, event ->
            if (!micMode() && voiceNote == null) return@setOnTouchListener false  // a send: the click does it
            if (locked) {  // the button is "send" now, for a new tap: the finger that locked it lifting is not one
                if (event.actionMasked == MotionEvent.ACTION_DOWN) tapped = true
                if (event.actionMasked == MotionEvent.ACTION_UP && tapped) endVoiceNote(keep = true)
                return@setOnTouchListener true
            }
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = event.rawX; downY = event.rawY; startVoiceNote() }
                MotionEvent.ACTION_MOVE -> if (voiceNote != null) {
                    // Raw coordinates: the button moves under the finger, so its own would chase themselves
                    val dx = (event.rawX - downX).coerceAtMost(0f)
                    val dy = (event.rawY - downY).coerceAtMost(0f)
                    val sideways = -dx > -dy  // one direction at a time, as WhatsApp does
                    send.translationX = if (sideways) dx else 0f
                    send.translationY = if (sideways) 0f else dy
                    lockHint?.contentView?.alpha = if (sideways) 0.4f else 1f
                    when {
                        dx < -dp(110) -> endVoiceNote(keep = false, why = "Dropped")
                        dy < -dp(90) -> lockVoiceNote()
                    }
                }
                MotionEvent.ACTION_UP -> endVoiceNote(keep = true)
                MotionEvent.ACTION_CANCEL -> endVoiceNote(keep = false, why = "Dropped")
            }
            true
        }
    }

    private fun micMode() = !onCall() && text.text.isNullOrBlank()

    private fun startVoiceNote() {
        if (current.isEmpty() || voiceNote != null) return
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
            return
        }
        val note = VoiceNote()
        if (!note.start()) { say("", "The microphone is busy.", note = true); return }
        voiceNote = note
        send.backgroundTintList = android.content.res.ColorStateList.valueOf(0xFFE24A4A.toInt())  // red while recording
        send.animate().scaleX(1.5f).scaleY(1.5f).setDuration(160).setInterpolator(android.view.animation.OvershootInterpolator()).start()
        send.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS)
        lockHint = android.widget.PopupWindow(TextView(this).apply {  // above the mic: slide up here to lock
            text = "🔒\n▲"
            gravity = Gravity.CENTER
            setTextColor(getColor(R.color.text)); textSize = 16f
            setPadding(dp(10), dp(10), dp(10), dp(10))
            background = GradientDrawable().apply { setColor(getColor(R.color.surface_high)); cornerRadius = dp(22).toFloat() }
        }, dp(48), dp(76)).apply {
            showAsDropDown(send, 0, -send.height - dp(130))
            contentView.translationY = dp(20).toFloat()
            contentView.animate().translationY(0f).setDuration(220).start()
            bounce(contentView)
        }
        main.post(recordClock)
    }

    /** The lock's arrow nudges up now and then, as a hint. */
    private fun bounce(view: View) {
        view.animate().translationY(-dp(6).toFloat()).setDuration(450).withEndAction {
            view.animate().translationY(0f).setDuration(450).withEndAction { if (lockHint?.isShowing == true) bounce(view) }.start()
        }.start()
    }

    /** The time recorded so far, in the message box, with what to do next. */
    private val recordClock = object : Runnable {
        override fun run() {
            val note = voiceNote ?: return
            val seconds = (System.currentTimeMillis() - note.started) / 1000
            if (seconds >= VoiceNote.LONGEST) return endVoiceNote(keep = true)  // at the cap: sent, and the mic let go
            val clock = "%d:%02d".format(seconds / 60, seconds % 60)
            text.hint = if (locked) "🔴 $clock   ✕ drops it, the button sends" else "🔴 $clock   ◀ slide to drop · ▲ to lock"
            main.postDelayed(this, 500)
        }
    }

    /** Slid up: recording stays on hands-off; the mic springs back as a send button, ✕ (where the paperclip is)
     *  drops it. */
    private fun lockVoiceNote() {
        if (locked || voiceNote == null) return
        locked = true
        tapped = false
        lockHint?.dismiss(); lockHint = null
        send.animate().translationX(0f).translationY(0f).scaleX(1f).scaleY(1f).setDuration(200)
            .setInterpolator(android.view.animation.OvershootInterpolator()).start()
        send.setImageResource(R.drawable.ic_send)
        attach.setImageResource(R.drawable.ic_close)
        attach.contentDescription = "Drop the voice message"
        send.performHapticFeedback(if (android.os.Build.VERSION.SDK_INT >= 30) android.view.HapticFeedbackConstants.CONFIRM else android.view.HapticFeedbackConstants.VIRTUAL_KEY)
        recordClock.run()
    }

    private fun endVoiceNote(keep: Boolean, why: String = "") {
        val note = voiceNote ?: return
        voiceNote = null
        locked = false
        main.removeCallbacks(recordClock)
        lockHint?.dismiss(); lockHint = null
        send.backgroundTintList = android.content.res.ColorStateList.valueOf(getColor(R.color.cyan))
        // Back in place with a little spring; a dropped one gives a shake, so it is clearly gone
        send.animate().translationX(0f).translationY(0f).scaleX(1f).scaleY(1f).setDuration(220)
            .setInterpolator(android.view.animation.OvershootInterpolator()).start()
        if (!keep) send.postDelayed({
            android.animation.ObjectAnimator.ofFloat(send, "rotation", 0f, -14f, 14f, -8f, 8f, 0f).setDuration(320).start()
        }, 220)
        attach.setImageResource(R.drawable.ic_add)
        attach.contentDescription = "Add a photo, a picture or files"
        showConnected(onCall())  // the hint and the mic back
        val short = System.currentTimeMillis() - note.started < 700
        val wav = note.stop(keep && !short)
        if (wav == null) {
            android.widget.Toast.makeText(this, if (short && keep) "Hold to record" else why, android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        val id = java.util.UUID.randomUUID().toString()
        val to = current
        val waiting = "🎙 Voice message…"
        Chats.add(this, to, JSONObject().put("from", "you").put("text", waiting).put("mine", true).put("id", id).put("status", "sent"))
        val bubble = say("you", waiting, mine = true, session = to, keep = false, status = "sent")
        sentBubbles[id] = bubble to waiting
        Thread {
            val (failed, heard) = VoiceNote.send(this, to, wav)
            if (failed.isEmpty()) {
                val words = "🎙 $heard"
                Chats.setText(this, to, id, words)
                Chats.setStatus(this, to, id, "delivered")
                main.post {
                    if (sentBubbles[id]?.first === bubble) sentBubbles[id] = bubble to words
                    // And the bubble waiting to turn blue: the agent's answer must not put "Voice message…" back
                    // (it did, 2026-11-16)
                    myBubbles.replaceAll { if (it.first === bubble) bubble to words else it }
                    bubble.text = stamp(bubble, words, "delivered")  // in place: no redraw under you
                }
            } else {
                Chats.setText(this, to, id, "🎙 Voice message (not sent)")
                main.post {
                    bubble.alpha = 0.5f
                    bubble.paintFlags = bubble.paintFlags or Paint.STRIKE_THRU_TEXT_FLAG
                    say("", failed, note = true)
                }
            }
        }.start()
    }

    private fun sendTyped() {
        val entered = text.text.toString().trim()
        if (entered.isEmpty()) return
        // Replying to a message: it goes along, on the same line (a newline would send early in a terminal)
        val words = if (quote.isEmpty()) entered else "(replying to: \"${quote.replace("\n", " ").take(160)}\") $entered"
        setQuote("")
        val id = java.util.UUID.randomUUID().toString()
        if (!onCall()) {  // not the call's chat: a text, to the agent whose chat this is
            if (current.isEmpty()) return
            Chats.add(this, current, JSONObject().put("from", "you").put("text", words).put("mine", true).put("id", id).put("status", "sent"))
            val bubble = say("you", words, mine = true, session = current, keep = false, status = "sent")
            sentBubbles[id] = bubble to words
            text.setText("")
            val to = current
            Thread {
                val note = Messages.text(this, to, words)
                if (note.isEmpty()) {
                    Chats.setStatus(this, to, id, "delivered")
                    main.post { tick(id, "delivered") }  // in place: no redraw under you
                }
                if (note.isNotEmpty()) main.post {
                    bubble.alpha = 0.5f
                    bubble.paintFlags = bubble.paintFlags or Paint.STRIKE_THRU_TEXT_FLAG
                    say("", note, note = true)
                }
            }.start()
            return
        }
        CallService.Bus.service?.lastSession = current
        CallService.Bus.service?.send(JSONObject().put("chat", words).put("session", current).put("id", id).toString())
        Chats.add(this, current, JSONObject().put("from", "you").put("text", words).put("mine", true).put("id", id).put("status", "sent"))
        val bubble = say("you", words, mine = true, session = current, keep = false, status = "sent")
        bubble.tag = "typed-$id"
        sentBubbles[id] = bubble to words
        text.setText("")
    }

    /** Back to the call's chat, from anywhere (the call bar, or Call while texting another agent). */
    private fun openCall() {
        if (callSession.isNotEmpty() && callSession != current) open(callSession) else { showConnected(onCall()); show(chatScreen) }
    }

    /** The bar over the list and other chats while a call is up: who it's with; a tap goes back to it. */
    private fun showCallBar() {
        val up = CallService.Bus.connected && !(chatScreen.visibility == View.VISIBLE && onCall())
        val words = "📞  On a call with ${names[callSession]?.substringBefore(",") ?: "your crew"} · tap to return"
        for (id in listOf(R.id.callBarList, R.id.callBarChat)) findViewById<TextView>(id).apply {
            visibility = if (up) View.VISIBLE else View.GONE
            text = words
        }
    }

    /** On the call, a tap on the agent's name or face: who the call is with, switched (as "switch me to …"). */
    private fun switchCall() {
        val crew = names.map { (session, name) -> JSONObject().put("id", session).put("name", name) }
        if (crew.size < 2) return
        panel = ModelPicker(this, "Switch the call to", crew, withDefault = false) { session, _ ->
            CallService.Bus.service?.send(JSONObject().put("switch", session).toString())
        }.also { it.show() }
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
        send.setImageResource(if (micMode()) R.drawable.ic_mic else R.drawable.ic_send)  // off a call, empty: voice
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
            state.text = subtitle(if (CallService.Bus.connected) "Texting · on a call with ${names[callSession]?.substringBefore(",") ?: "your crew"}"
                         else "Not on a call")  // texts and voice messages still reach the agent (not "not connected", 2026-11-16)
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
                    keep: Boolean = true, status: String = "", at: Long = System.currentTimeMillis()): TextView {
        if (keep && !note) Chats.add(this, session, JSONObject().put("from", from).put("text", words).put("mine", mine))
        if (!note && at > 0) dayLine(at)
        val bubble = TextView(this).apply {
            if (!note) bubbleAt[this] = at
            text = if (note) words else stamp(this, words, if (mine) status else "")
            setTextColor(getColor(if (note) R.color.muted else R.color.text))
            textSize = if (note) 13f else 15f
            if (!note) {
                setBackgroundResource(if (mine) R.drawable.bubble_mine else R.drawable.bubble_agent)
                setPadding(dp(14), dp(10), dp(14), dp(10))
            }
        }
        if (mine && (status == "sent" || status == "delivered")) myBubbles.add(bubble to words)  // to turn blue
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
        // A new chat opens empty, showing the henchman instead of the lines: its first line brings the lines back
        // (a first text was sent but never shown, 2026-11-16)
        if (chatScroll.visibility != View.VISIBLE && row.visibility == View.VISIBLE) {
            chatScroll.visibility = View.VISIBLE
            findViewById<View>(R.id.idle).visibility = View.GONE
        }
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        return bubble
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private val myBubbles = mutableListOf<Pair<TextView, String>>()  // your lines on screen not yet read
    private val sentBubbles = HashMap<String, Pair<TextView, String>>()  // your lines on screen by id, for their ticks

    /** A line of yours moved on (delivered): its ticks change where it is, nothing else redrawn. */
    private fun tick(id: String, status: String) {
        refreshStop()
        val (bubble, words) = sentBubbles[id] ?: return
        bubble.text = stamp(bubble, words, status)
    }

    /** The stop square: shown while the agent works on what you sent, i.e. your last line there was typed into it
     *  (delivered) and it hasn't answered since (the Claude app's stop button, the user's wish, 2026-11-16). */
    private var thoughtsSince = ""  // the newest thought shown, for the next ask
    private var thoughtsOf = ""  // the chat they belong to
    private val thoughtsPoll = object : Runnable {
        override fun run() {
            pollThoughts()
            if (chatScreen.visibility == View.VISIBLE) main.postDelayed(this, 5000)
        }
    }

    /** A Cursor IDE chat's thinking, as thought bubbles, while its chat is on screen (the user's wish, 2026-11-16:
     *  a thought bubble beside the speech ones). Only thoughts after its last kept line, then each new one. */
    private fun pollThoughts() {
        val session = current
        if (!session.matches(Regex("(cursor|claude)-[0-9a-f-]{36}")) || !prefs().getBoolean("thoughts", true)) return
        if (thoughtsOf != session) {
            thoughtsOf = session
            val at = Chats.last(this, session)?.optLong("at") ?: 0L
            thoughtsSince = if (at > 0) java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US)
                .apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date(at)) else ""
        }
        ask("/chat/thoughts?session=" + android.net.Uri.encode(session) + "&since=" + android.net.Uri.encode(thoughtsSince), null) { answer ->
            val list = answer.optJSONArray("thoughts") ?: return@ask
            if (session != current || chatScreen.visibility != View.VISIBLE) return@ask
            for (i in 0 until list.length()) {
                val thought = list.getJSONObject(i)
                thoughtsSince = maxOf(thoughtsSince, thought.optString("at"))
                thoughtBubble(thought.optString("text"))
            }
        }
    }

    /** A thought: a cloud, not a speech bubble (as a comic's: soft and round, with the small circles trailing
     *  from the thinker), italic and quieter than speech; two lines and "tap to read all", the rest a tap away. */
    private fun thoughtBubble(words: String) {
        val cloud = getColor(R.color.surface) and 0x00FFFFFF or 0x99000000.toInt()
        val bubble = TextView(this).apply {
            text = words
            setTextColor(getColor(R.color.muted)); textSize = 13.5f
            setTypeface(typeface, android.graphics.Typeface.ITALIC)
            maxLines = 3; ellipsize = android.text.TextUtils.TruncateAt.END
            setPadding(dp(18), dp(12), dp(18), dp(12))
            background = GradientDrawable().apply { setColor(cloud); cornerRadius = dp(28).toFloat() }
            setOnClickListener {  // open in full; again: folded to three lines
                if (maxLines != Int.MAX_VALUE) { maxLines = Int.MAX_VALUE; text = words } else { maxLines = 3; text = words }
            }
            maxWidth = (resources.displayMetrics.widthPixels * 0.72).toInt()
        }
        bubble.post {  // folded: say there is more
            val layout = bubble.layout ?: return@post
            if (layout.lineCount >= 3 && layout.getEllipsisCount(2) > 0) {  // the hint as the 3rd line's end
                bubble.maxLines = 4
                bubble.text = layout.text.subSequence(0, layout.getLineEnd(1)).toString().trimEnd() + "…\n— tap to read all"
            }
        }
        fun dot(size: Int) = View(this).apply {
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(cloud) }
        }.also { it.layoutParams = LinearLayout.LayoutParams(dp(size), dp(size)).apply { marginEnd = dp(3) } }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.BOTTOM
            setPadding(dp(16), dp(4), 0, dp(4))
            addView(dot(6)); addView(dot(10).apply { (layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(6) })
            addView(bubble)
        }
        chat.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
    }

    private fun refreshStop() {
        val last = if (current.isEmpty()) null else Chats.last(this, current)
        val working = last != null && last.optBoolean("mine") && last.optString("status") == "delivered"
        findViewById<View>(R.id.stop).visibility = if (working) View.VISIBLE else View.GONE
    }

    private fun stopAgent() {
        val session = current.ifEmpty { return }
        findViewById<View>(R.id.stop).visibility = View.GONE
        ask("/crew/stop", JSONObject().put("session", session)) { answer ->
            android.widget.Toast.makeText(this, if (answer.optBoolean("ok")) "Stopped" else answer.optString("note").ifEmpty { "Not stopped" },
                android.widget.Toast.LENGTH_SHORT).show()
            if (!answer.optBoolean("ok")) refreshStop()
        }
    }

    private val bubbleAt = java.util.WeakHashMap<TextView, Long>()  // when each bubble's line was said
    private var lastDay = ""  // the day of the newest line drawn, for the day line between days

    /** The words with the time and, for yours, the ticks small at the end, as WhatsApp shows them: ✓ sent,
     *  ✓✓ delivered, ✓✓ in cyan once read. */
    private fun stamp(bubble: TextView, words: String, status: String): CharSequence {
        val at = bubbleAt[bubble] ?: 0L
        val time = if (at > 0) android.text.format.DateFormat.getTimeFormat(this).format(java.util.Date(at)) else ""
        val ticks = when (status) { "" -> ""; "sent" -> "✓"; else -> "✓✓" }
        val tail = listOf(time, ticks).filter { it.isNotEmpty() }.joinToString(" ")
        if (tail.isEmpty()) return styled(words)
        val all = android.text.SpannableStringBuilder(styled(words)).append("  ").append(tail)
        val from = all.length - tail.length
        all.setSpan(android.text.style.ForegroundColorSpan(getColor(R.color.muted)), from, all.length, android.text.Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
        if (ticks.isNotEmpty() && status == "read")
            all.setSpan(android.text.style.ForegroundColorSpan(getColor(R.color.cyan)), all.length - ticks.length, all.length,
                android.text.Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
        all.setSpan(android.text.style.RelativeSizeSpan(0.75f), from, all.length, android.text.Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
        return all
    }

    /** An agent's light markdown, shown as it means: **bold** bold and `code` in a fixed-width face, the marks
     *  themselves gone (they showed as asterisks, 2026-11-16). */
    private fun styled(words: String): CharSequence {
        val out = android.text.SpannableStringBuilder()
        val marks = Regex("\\*\\*(.+?)\\*\\*|`([^`]+)`")
        var at = 0
        for (m in marks.findAll(words)) {
            out.append(words, at, m.range.first)
            val start = out.length
            if (m.groupValues[1].isNotEmpty()) {
                out.append(m.groupValues[1])
                out.setSpan(android.text.style.StyleSpan(android.graphics.Typeface.BOLD), start, out.length, android.text.Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
            } else {
                out.append(m.groupValues[2])
                out.setSpan(android.text.style.TypefaceSpan("monospace"), start, out.length, android.text.Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            at = m.range.last + 1
        }
        out.append(words, at, words.length)
        return out
    }

    /** The line under the agent's name: its project, then what is happening ("pc diagnostics · Not on a call"). */
    private fun subtitle(status: String): String {
        val name = names[current] ?: prefs().getString("current_name", "") ?: ""
        val project = name.substringAfter(", in ", "").substringBefore(" · ").replace("_", " ")
        return listOf(project, status).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    /** A call in the chat as WhatsApp shows one (the user's example, 2026-11-16): a message bubble on the side of
     *  whoever started it, a round phone mark (red when missed), "Voice call" and how long, and its time. */
    private fun callBubble(line: JSONObject) {
        val call = line.optJSONObject("call") ?: JSONObject().put("dir", "out")  // from before: one of yours
        val mine = call.optString("dir") == "out"
        val outcome = call.optString("outcome")
        val missed = outcome == "missed" || outcome == "declined"
        val title = when (outcome) { "missed" -> "Missed voice call"; "declined" -> "Declined voice call"; else -> "Voice call" }
        val detail = when {
            call.has("seconds") -> Calls.length(call.optLong("seconds"))
            missed -> call.optString("why").take(60)
            else -> line.optString("text").substringAfter("· ", "")
        }
        val at = line.optLong("at")
        val time = if (at > 0) android.text.format.DateFormat.getTimeFormat(this).format(java.util.Date(at)) else ""
        val mark = TextView(this).apply {
            text = if (mine) "↗" else "↙"
            setTextColor(getColor(if (missed) R.color.hang else R.color.text)); textSize = 18f; gravity = Gravity.CENTER
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(getColor(R.color.navy)) }
        }
        val words = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(dp(12), 0, dp(12), 0)
            addView(TextView(this@MainActivity).apply {
                text = "📞 $title"; setTextColor(getColor(if (missed) R.color.hang else R.color.text)); textSize = 15f
                typeface = android.graphics.Typeface.DEFAULT_BOLD
            })
            if (detail.isNotEmpty()) addView(TextView(this@MainActivity).apply {
                text = detail; setTextColor(getColor(R.color.muted)); textSize = 13f; maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
        }
        val bubble = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(10), dp(10), dp(12), dp(8))
            setBackgroundResource(if (mine) R.drawable.bubble_mine else R.drawable.bubble_agent)
            addView(mark, LinearLayout.LayoutParams(dp(40), dp(40)))
            addView(words, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(TextView(this@MainActivity).apply {
                text = time; setTextColor(getColor(R.color.muted)); textSize = 11f
            }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                gravity = Gravity.BOTTOM
            })
        }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = if (mine) Gravity.END else Gravity.START or Gravity.BOTTOM
            setPadding(0, dp(4), 0, dp(4))
        }
        if (!mine) row.addView(ImageView(this).apply { setImageResource(avatarOf(current)) },
            LinearLayout.LayoutParams(dp(28), dp(28)).apply { marginEnd = dp(8) })
        row.addView(bubble, LinearLayout.LayoutParams((resources.displayMetrics.widthPixels * 0.68).toInt(),
            LinearLayout.LayoutParams.WRAP_CONTENT))
        if (mine) row.addView(ImageView(this).apply { setImageResource(R.drawable.avatar_you) },
            LinearLayout.LayoutParams(dp(28), dp(28)).apply { marginStart = dp(8) })
        chat.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
    }

    /** "Today", "Yesterday" or the date, centred between the lines of different days. */
    private fun dayLine(at: Long) {
        val day = android.text.format.DateFormat.getMediumDateFormat(this).format(java.util.Date(at))
        if (day == lastDay) return
        lastDay = day
        val label = when {
            android.text.format.DateUtils.isToday(at) -> "Today"
            android.text.format.DateUtils.isToday(at + 86_400_000L) -> "Yesterday"
            else -> day
        }
        chat.addView(TextView(this).apply {
            text = label
            setTextColor(getColor(R.color.muted)); textSize = 12f; gravity = Gravity.CENTER
            setPadding(0, dp(10), 0, dp(6))
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
    }

    private val searchSoon = Runnable { showCrew() }
    private var searched: Pair<String, Map<String, String>>? = null  // a content search's result, for its query

    /** When you last looked at a chat: what its agent said after that is unread (bold in the list). */
    private fun markRead(session: String) = prefs().edit().putLong("seen_$session", System.currentTimeMillis()).apply()

    /** How many of the agent's lines you haven't seen: read from the file only when the newest one is unseen. */
    private fun unread(session: String, last: JSONObject?): Int {
        val seen = prefs().getLong("seen_$session", 0)
        if (last == null || last.optBoolean("mine") || last.optLong("at") <= seen) return 0
        return Chats.load(this, session).takeLastWhile { !it.optBoolean("mine") }
            .count { !it.optBoolean("note") && it.optLong("at") > seen }  // a call's line isn't a message
    }

    private var showingCalls = false  // the list screen shows the call history instead of the chats

    /** The call history, newest first: who rang and why, and how it went; calls with their length. Tap one for
     *  that agent's chat. */
    private fun showCalls() {
        val calls = Calls.load(this).asReversed()
        if (calls.isEmpty()) crew.addView(TextView(this).apply {
            text = "No calls yet. Rings and calls show here."
            setTextColor(getColor(R.color.muted)); setPadding(dp(20), dp(24), dp(20), dp(24))
        })
        val ringSeconds = (RingService.RING_SECONDS + 30) * 1000
        for (call in calls) {
            val ring = call.optString("kind") == "ring"
            val at = call.optLong("at")
            // A banner ring nobody answered or declined is still "ringing" in the log: missed, once its time is up
            val outcome = call.optString("outcome").let { if (it == "ringing" && System.currentTimeMillis() - at > ringSeconds) "missed" else it }
            val agent = call.optString("agent")
            val session = call.optString("session")
            val name = if (ring) IncomingActivity.nameOf(agent) else names[session]?.substringBefore(",") ?: "Call"
            val (mark, colour) = when {
                !ring -> "📞" to R.color.text
                outcome == "answered" -> "↙ answered" to R.color.cyan
                outcome == "declined" -> "↙ declined" to R.color.muted
                outcome == "ringing" || outcome == "rang" -> "↙ rang" to R.color.muted
                else -> "↙ $outcome" to R.color.hang
            }
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                setPadding(dp(20), dp(12), dp(20), dp(12))
                if (session.isNotEmpty()) setOnClickListener { showCalls(false); open(session) }
            }
            row.addView(ImageView(this).apply {
                setImageResource(if (ring) avatarOf(agent) else if (session.isNotEmpty()) avatarOf(session) else R.drawable.avatar_handsfree)
            }, LinearLayout.LayoutParams(dp(44), dp(44)))
            val words = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, 0, 0) }
            val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            top.addView(TextView(this).apply {
                text = name
                setTextColor(getColor(if (outcome == "missed") R.color.hang else R.color.text)); textSize = 16f
                typeface = android.graphics.Typeface.DEFAULT_BOLD; maxLines = 1
            }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            top.addView(TextView(this).apply {
                text = android.text.format.DateUtils.formatDateTime(this@MainActivity, at,
                    android.text.format.DateUtils.FORMAT_SHOW_TIME or android.text.format.DateUtils.FORMAT_SHOW_DATE or
                        android.text.format.DateUtils.FORMAT_ABBREV_MONTH)
                setTextColor(getColor(R.color.muted)); textSize = 12f
            })
            words.addView(top)
            val detail = if (ring) listOf(mark, call.optString("why")) else
                listOf("$mark Call" + if (call.has("seconds")) " · ${Calls.length(call.optLong("seconds"))}" else " · going on")
            words.addView(TextView(this).apply {
                text = detail.filter { it.isNotEmpty() }.joinToString(" · ")
                setTextColor(getColor(colour)); textSize = 14f; maxLines = 2
                ellipsize = android.text.TextUtils.TruncateAt.END
            })
            row.addView(words, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            crew.addView(row)
        }
    }

    /** The bottom bar: Chats or Calls, the one showing lit. */
    private fun showCalls(on: Boolean) {
        showingCalls = on
        findViewById<TextView>(R.id.tabChats).setTextColor(getColor(if (on) R.color.muted else R.color.cyan))
        findViewById<TextView>(R.id.tabCalls).setTextColor(getColor(if (on) R.color.cyan else R.color.muted))
        for (id in listOf(R.id.sort, R.id.search, R.id.compose)) findViewById<View>(id).visibility = if (on) View.GONE else View.VISIBLE
        showCrew()
    }

    private fun sortMode() = prefs().getString("sort", "recent") ?: "recent"

    /** A chat in the list: henchman, project, the agent's name and the last line (or the line a search found). */
    private fun chatRow(session: String, agent: String, project: String, last: JSONObject?, found: String): View {
        val unread = unread(session, last)
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(20), dp(12), dp(20), dp(12))
            background = getDrawable(if (session == current) R.drawable.row_current else android.R.color.transparent)
            setOnClickListener { open(session) }
            setOnLongClickListener { chatMenu(session, project, agent); true }
        }
        row.addView(ImageView(this).apply { setImageResource(avatarOf(session)) }, LinearLayout.LayoutParams(dp(52), dp(52)))
        val words = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), 0, 0, 0) }
        val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        top.addView(TextView(this).apply {
            text = project.replace("_", " ")
            setTextColor(getColor(R.color.text)); textSize = 17f; typeface = android.graphics.Typeface.DEFAULT_BOLD
            maxLines = 1; ellipsize = android.text.TextUtils.TruncateAt.END
        }, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        top.addView(TextView(this).apply {
            text = last?.optLong("at")?.takeIf { it > 0 }?.let { android.text.format.DateUtils.getRelativeTimeSpanString(it).toString() } ?: ""
            setTextColor(getColor(if (unread > 0) R.color.cyan else R.color.muted)); textSize = 12f
        })
        words.addView(top)
        val bottom = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        TextView(this).apply {
            val line = found.ifEmpty {
                last?.let { (if (it.optBoolean("mine")) "You: " else "") + it.optString("text").ifEmpty { "📎 file" } } ?: ""
            }
            text = listOf(agent, line.replace("\n", " ")).filter { it.isNotEmpty() }.joinToString(" · ")
            setTextColor(getColor(when { found.isNotEmpty() -> R.color.cyan; unread > 0 -> R.color.text; else -> R.color.muted }))
            textSize = 14f; maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.END
            if (unread > 0) typeface = android.graphics.Typeface.DEFAULT_BOLD  // unread: something to look at
        }.let { line -> bottom.addView(line, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)) }
        if (unread > 0) bottom.addView(TextView(this).apply {  // the count, as a messaging app shows it
            text = if (unread > 99) "99+" else unread.toString()
            setTextColor(getColor(R.color.navy)); textSize = 12f; typeface = android.graphics.Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER; minWidth = dp(22); setPadding(dp(6), dp(1), dp(6), dp(1))
            background = GradientDrawable().apply { setColor(getColor(R.color.cyan)); cornerRadius = dp(11).toFloat() }
        }, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { marginStart = dp(8) })
        words.addView(bottom)
        row.addView(words, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        return row
    }

    /** One screen at a time: the chat list, a chat, or settings. */
    private fun show(screen: View) {
        for (s in listOf(listScreen, chatScreen, settingsScreen)) s.visibility = if (s === screen) View.VISIBLE else View.GONE
        if (screen === listScreen) { showCrew(); refreshCrewQuietly() }
        showCallBar()
        // A chat opens at its newest message, as a messaging app's does (it opened at the oldest, 2026-11-16)
        if (screen === chatScreen) chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
        main.removeCallbacks(thoughtsPoll)
        if (screen === chatScreen) { thoughtsOf = ""; main.post(thoughtsPoll) }
        Messages.viewing = if (screen === chatScreen) current else ""
        if (screen !== chatScreen) { sentBubbles.clear(); myBubbles.clear() }  // only bubbles on screen are kept
    }

    /** Into an agent's chat from the list: on a call the laptop switches to it (and says so); off one, its chat
     *  is shown and texts go to it. */
    private fun open(session: String) {
        if (session != current) {  // a chat, not the call: on a call this doesn't switch it (the call bar leads back)
            current = session
            prefs().edit().putString("current", current).putString("current_name", names[current] ?: "").apply()
            showHistory()
        }
        showConnected(onCall())
        showCrew()
        show(chatScreen)
        if (current.isNotEmpty()) { Messages.seen(this, current); markRead(current) }  // opened: read
        showModel(session)
    }

    /** A model's name short enough for the top bar: "Claude Opus 5.5 300K Medium" is "Opus 5.5", "gpt-5.6-sol xhigh"
     *  is "gpt-5.6-sol" (the chip pushed the call button off the bar, 2026-11-16). */
    private fun shortModel(name: String): String {
        val words = name.split(" ").filter { it.isNotEmpty() }
            .takeWhile { !Regex("(?i)\\d+[KM]|low|medium|high|xhigh|extra|max|fast|thinking|\\(.*").matches(it) }
        val short = words.dropWhile { it.equals("Claude", ignoreCase = true) && words.size > 1 }.joinToString(" ").ifEmpty { name }
        // The effort, short, when the name has one: "Grok 4.7 Extra High" is "Grok 4.7 · xhigh" (showing only the
        // family hid that a chat ran on extra high, 2026-11-16)
        val rest = name.lowercase()
        val effort = when {
            "extra high" in rest || "xhigh" in rest -> "xhigh"
            Regex("\\bhigh\\b").containsMatchIn(rest) -> "high"
            Regex("\\bmedium\\b").containsMatchIn(rest) -> "med"
            Regex("\\blow\\b").containsMatchIn(rest) -> "low"
            else -> ""
        }
        return if (effort.isEmpty()) short else "$short · $effort"
    }

    /** The chat's model chip: the model the agent is using now, as the laptop sees it (the user's wish,
     *  2026-11-16); until it answers, the one last switched to from here. */
    private fun showModel(session: String) {
        val chip = findViewById<TextView>(R.id.modelChip)
        chip.text = prefs().getString("model_$session", null)?.let { "✦ " + shortModel(it) } ?: "✦ Model"
        if (session.isEmpty() || Files.origin(this) == null) return
        ask("/crew/models?only=now&session=" + android.net.Uri.encode(session), null) { answer ->
            val now = answer.optString("label").ifEmpty { answer.optString("now") }  // model · effort · context
            if (now.isNotEmpty() && session == current) {
                chip.text = "✦ " + (answer.optString("label").ifEmpty { shortModel(now) })
                prefs().edit().putString("model_$session", now).apply()
            }
        }
    }

    private var crewAsked = 0L

    /** The list coming into view: the laptop's crew asked for again, quietly, at most every half minute, so agents
     *  ended on the laptop leave the list without a pull (one stayed listed after it was killed, 2026-11-16). */
    private fun refreshCrewQuietly() {
        if (System.currentTimeMillis() - crewAsked < 30_000 || Files.origin(this) == null) return
        crewAsked = System.currentTimeMillis()
        ask("/crew", JSONObject()) { answer ->
            if (answer.has("agents")) { takeCrew(answer); if (listScreen.visibility == View.VISIBLE) showCrew() }
        }
    }

    /** Pull down on the list: the laptop looks for every agent running (as "switch me to" does) and lists them. */
    private fun findCrew() {
        val origin = Files.origin(this) ?: run { refresh.isRefreshing = false; return say("", "Set the laptop's address in Settings first.", note = true).let {} }
        Thread {
            val found = try {
                Files.client.newCall(okhttp3.Request.Builder().url("$origin/crew").header("Origin", origin)
                    .post(ByteArray(0).toRequestBody(null)).build())
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

    /** A request to the laptop off the main thread: the JSON answer (with "note" saying why not), on the main thread. */
    private fun ask(path: String, body: JSONObject?, then: (JSONObject) -> Unit) {
        val origin = Files.origin(this) ?: return say("", "Set the laptop's address in Settings first.", note = true).let {}
        Thread {
            val answer = try {
                val request = okhttp3.Request.Builder().url("$origin$path").header("Origin", origin)
                if (body != null) request.post(body.toString().toRequestBody("application/json".toMediaType()))
                Files.client.newCall(request.build()).execute().use { JSONObject(it.body?.string()?.ifEmpty { "{}" } ?: "{}") }
            } catch (_: Exception) { JSONObject().put("note", "Couldn't reach the laptop (is Tailscale on?)") }
            main.post { then(answer) }
        }.start()
    }

    /** The crew as the laptop listed it: names, and which agents were started from here (those can be ended). */
    private fun takeCrew(answer: JSONObject) {
        val found = answer.optJSONArray("agents") ?: return
        names = (0 until found.length()).associate { found.getJSONObject(it).getString("session") to found.getJSONObject(it).getString("name") }
        prefs().edit().putString("crew", found.toString()).apply()
    }

    private fun startedHere(session: String): Boolean = try {
        val crew = org.json.JSONArray(prefs().getString("crew", "[]"))
        (0 until crew.length()).any { crew.getJSONObject(it).optString("session") == session && crew.getJSONObject(it).optBoolean("spawned") }
    } catch (_: Exception) { false }

    private var panel: BottomPanel? = null  // the new-chat or long-press sheet, while it is up

    /** New chat (the user's wish, 2026-11-16): the laptop's folders (those its Claude trusts, newest first, or a
     *  typed path) and agents, in a bottom sheet; the agent starts in its own tmux window there and its chat opens. */
    private fun newChat() {
        ask("/crew/folders", null) { answer ->
            val folders = answer.optJSONArray("folders")
            val agents = answer.optJSONArray("agents")
            if (folders == null || agents == null || agents.length() == 0) {
                android.widget.Toast.makeText(this, answer.optString("note").ifEmpty { "No agent to start on the laptop (it needs tmux)." },
                    android.widget.Toast.LENGTH_LONG).show()
                return@ask
            }
            panel = NewChatSheet(this, (0 until folders.length()).map { folders.getJSONObject(it) },
                (0 until agents.length()).map { agents.getString(it) },
                loadModels = { agent, then -> ask("/crew/models?agent=$agent", null) { answer -> then(answer) } }
            ) { folder, agent, model, params, typed, done ->
                ask("/crew/new", JSONObject().put("folder", folder).put("agent", agent).put("model", model)
                    .put("params", JSONObject(params as Map<*, *>)).put("typed", typed)) { started ->
                    val session = started.optString("session")
                    if (session.isEmpty()) return@ask done(started.optString("note").ifEmpty { "It didn't start." })
                    done(null)
                    takeCrew(started)
                    open(session)
                }
            }.also { it.show() }
        }
    }

    private fun modelsOf(answer: JSONObject): List<JSONObject> =
        answer.optJSONArray("models")?.let { list -> (0 until list.length()).map { list.getJSONObject(it) } } ?: emptyList()

    /** The model chip in a chat's top bar: the agent's models, and the pick typed into it as its own /model. */
    private fun switchModel() {
        val session = current.ifEmpty { return }
        ask("/crew/models?session=" + android.net.Uri.encode(session), null) { answer ->
            val toast = { words: String -> android.widget.Toast.makeText(this, words, android.widget.Toast.LENGTH_LONG).show() }
            if (answer.has("note")) return@ask toast(answer.optString("note"))
            if (!answer.optBoolean("switch")) return@ask toast(when {
                answer.optString("agent") == "codex" -> "Codex switches models only in its own picker: start a new chat with the model."
                session.matches(Regex("cursor-[0-9a-f-]{36}")) -> "On ${answer.optString("now").ifEmpty { "its own model" }}. Cursor only lets you change an IDE chat's model in Cursor itself."
                else -> "This agent can't switch models from the phone."
            })
            // A Cursor IDE chat: Cursor's own menu (model, effort, context, fast)
            answer.optJSONArray("menu")?.let { menu ->
                val current = answer.optJSONObject("current") ?: JSONObject()
                val settings = mutableMapOf<String, String>()
                current.optJSONObject("params")?.let { p -> p.keys().forEach { k -> settings[k] = p.optString(k) } }
                panel = CursorModelMenu(this, (0 until menu.length()).map { menu.getJSONObject(it) },
                    current.optString("model"), settings) { model, params, done ->
                    val list = org.json.JSONArray(params.map { (k, v) -> JSONObject().put("id", k).put("value", v) })
                    ask("/crew/model", JSONObject().put("session", session).put("model", model).put("params", list)) { result ->
                        done(if (result.optBoolean("ok")) result.optString("note").ifEmpty { null }
                             else result.optString("note").ifEmpty { "Didn't switch." })
                        showModel(session)
                    }
                }.also { it.show() }
                return@ask
            }
            val now = answer.optString("now")
            panel = ModelPicker(this, if (now.isNotEmpty()) "Now on $now" else "Switch model", modelsOf(answer), withDefault = false) { id, name ->
                ask("/crew/model", JSONObject().put("session", session).put("model", id)) { done ->
                    if (done.optBoolean("ok")) {
                        say("", "✦ Switched to $name", note = true)
                        prefs().edit().putString("model_$session", name).apply()
                        showModel(session)  // what it really uses now (Cursor may pick a near match)
                        done.optString("note").takeIf { it.isNotEmpty() }?.let(toast)  // e.g. press Enter in Konsole
                    } else toast(done.optString("note").ifEmpty { "Didn't switch." })
                }
            }.also { it.show() }
        }
    }

    /** Long-press on a chat: clear it on this phone, or (for an agent started from here) end that agent, asked again
     *  first. Agents the user started on the laptop are never ended from the phone. */
    private fun chatMenu(session: String, project: String, agent: String) {
        panel = ChatActionsSheet(this, agent, project.replace("_", " "), startedHere(session), gone = session !in names,
            onClear = {
                Chats.clear(this, session)
                if (session == current) showHistory()
                showCrew()
            },
            onRemove = {
                Chats.clear(this, session)
                names = names - session
                val kept = org.json.JSONArray(prefs().getString("crew", "[]"))
                val crew = org.json.JSONArray((0 until kept.length()).map { kept.getJSONObject(it) }.filter { it.optString("session") != session })
                prefs().edit().putString("crew", crew.toString()).apply()
                if (session == current) { current = ""; prefs().edit().putString("current", "").apply() }
                showCrew()
            },
            onEnd = { done ->
                ask("/crew/end", JSONObject().put("session", session)) { answer ->
                    if (!answer.optBoolean("ok")) return@ask done(answer.optString("note").ifEmpty { "Not ended." })
                    done(null)
                    Chats.clear(this, session)
                    names = names - session
                    val kept = org.json.JSONArray(prefs().getString("crew", "[]"))
                    val crew = org.json.JSONArray((0 until kept.length()).map { kept.getJSONObject(it) }.filter { it.optString("session") != session })
                    prefs().edit().putString("crew", crew.toString()).apply()
                    if (session == current) { current = ""; prefs().edit().putString("current", "").apply() }
                    showCrew()
                }
            }).also { it.show() }
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
        var startY = 0f
        var dragging = false
        row.setOnTouchListener { view, event ->
            when (event.actionMasked) {
                // Kept from the start (a row that doesn't take the DOWN never sees the drag); the list still
                // scrolls, as the ScrollView takes over a vertical move
                MotionEvent.ACTION_DOWN -> { startX = event.rawX; startY = event.rawY; dragging = false; true }
                MotionEvent.ACTION_MOVE -> {
                    val dx = event.rawX - startX
                    val dy = event.rawY - startY
                    if (!dragging && dx > dp(12) && dx > 2 * kotlin.math.abs(dy)) {  // sideways, not a slow scroll
                        dragging = true
                        view.parent.requestDisallowInterceptTouchEvent(true)
                    }
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
        lastDay = ""
        myBubbles.clear()
        sentBubbles.clear()  // their views are gone with the old rows
        val history = Chats.load(this, current)
        val lastAgent = history.indexOfLast { !it.optBoolean("mine") && it.optString("from").isNotEmpty() }
        if (current.isNotEmpty() && chatScreen.visibility == View.VISIBLE && sheet?.isOpen != true) {  // you're looking: read
            Messages.seen(this, current)
            markRead(current)
        }
        for ((i, line) in history.withIndex()) {
            val file = line.optJSONObject("file")
            if (file != null) fileCard(line.optString("from"), file, current, keep = false)
            else if (line.has("thumb")) picture(line.getString("thumb"), line.optString("text"), current)
            else if (line.optBoolean("note")) {  // a kept note: a call and its length
                dayLine(line.optLong("at"))
                callBubble(line)
            } else {
                val bubble = say(line.optString("from"), line.optString("text"), mine = line.optBoolean("mine"),
                    session = current, keep = false, at = line.optLong("at"),
                    status = line.optString("status").ifEmpty {  // from before ticks: answered since means read
                        if (!line.optBoolean("mine")) "" else if (i < lastAgent) "read" else "delivered" })
                // Yours, by id: a delivered reply arriving later changes this bubble, the one on screen
                if (line.optBoolean("mine") && line.has("id")) sentBubbles[line.getString("id")] = bubble to line.optString("text")
            }
        }
        if (!onCall()) showConnected(false)  // not the call's chat: the chat if there is one, else the henchman
        refreshStop()
        chatScroll.post { chatScroll.fullScroll(View.FOCUS_DOWN) }
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
        const val TAKE_PHOTO = 5  // the + sheet's camera
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
