package io.github.ninjatuna007.henchline

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Person
import android.app.RemoteInput
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

/**
 * Agents texting you (`handsfree text` on the laptop) while no call is up: a messaging notification per agent
 * session, with a reply box right in it (the user's wish, 2026-11-15: "it's a messaging system now, not
 * calling"). A reply goes to that agent over Tailscale, tagged [hands-free · texted from the phone]. Both
 * sides are kept in that agent's chat in the app.
 */
object Messages {
    const val CHANNEL = "texts"
    const val REPLY = "io.github.ninjatuna007.henchline.REPLY_TEXT"
    private const val KEY = "reply"

    private fun me(context: Context) = Person.Builder().setName("You")
        .setIcon(Icon.createWithResource(context, R.drawable.avatar_you)).build()

    private fun them(context: Context, agent: String) = Person.Builder().setName(IncomingActivity.nameOf(agent))
        .setIcon(Icon.createWithResource(context, MainActivity.avatarOf(agent))).setImportant(true).build()

    /** An agent's message: shown with the last few of the conversation, and a reply box. */
    fun show(context: Context, agent: String, session: String, text: String) {
        Chats.add(context, session, JSONObject().put("from", IncomingActivity.nameOf(agent)).put("text", text).put("mine", false))
        Chats.readAll(context, session)
        post(context, agent, session)
        // The app open on that chat shows it at once, as a line from a call would (2026-11-15: a text reached the
        // notification and the kept chat, but the open screen didn't redraw)
        CallService.Bus.listener?.invoke(JSONObject().put("chat", JSONObject().put("from", IncomingActivity.nameOf(agent))
            .put("text", text).put("session", session)).toString())
    }

    /** The notification for that session, from its kept chat: the latest lines, yours and the agent's. */
    fun post(context: Context, agent: String, session: String, sending: Boolean = false) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "Messages from your crew", NotificationManager.IMPORTANCE_HIGH))
        val style = Notification.MessagingStyle(me(context))
        for (line in Chats.load(context, session).takeLast(6)) {
            val file = line.optJSONObject("file")
            val text = line.optString("text").ifEmpty { if (file != null) "📎 " + file.optString("name") else "" }
            if (text.isEmpty()) continue
            style.addMessage(text, line.optLong("at"), if (line.optBoolean("mine")) null else them(context, agent))
        }
        val id = session.hashCode()
        val reply = PendingIntent.getBroadcast(context, id, Intent(context, ReplyReceiver::class.java).setAction(REPLY)
            .putExtra("agent", agent).putExtra("session", session),
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)  // mutable: the reply text is added to it
        val open = PendingIntent.getActivity(context, id, Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE)
        manager.notify(id, Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setStyle(style)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setContentIntent(open)
            .setOnlyAlertOnce(sending)  // your own reply landing in it doesn't buzz
            .setAutoCancel(true)
            .addAction(Notification.Action.Builder(null, "Reply", reply)
                .addRemoteInput(RemoteInput.Builder(KEY).setLabel("Reply to ${IncomingActivity.nameOf(agent)}").build())
                .setAllowGeneratedReplies(false).build())
            .build())
    }

    /** Words to an agent session over Tailscale, within 8 s: "" when an agent took them, else why not. */
    fun text(context: Context, session: String, words: String): String = try {
        val origin = Files.origin(context) ?: throw IllegalStateException("no laptop address set")
        QUICK.newCall(Request.Builder().url("$origin/text").header("Origin", origin)
            .post(JSONObject().put("text", words).put("session", session).toString()
                .toRequestBody("application/json".toMediaType())).build()).execute().use { answer ->
            val body = try { JSONObject(answer.body?.string() ?: "") } catch (_: Exception) { JSONObject() }
            if (body.has("to")) "" else body.optString("note", "the laptop said ${answer.code}")
        }
    } catch (e: Exception) {
        "Not sent: ${e.message ?: "the laptop couldn't be reached"} (is Tailscale on?)"
    }

    private val QUICK = okhttp3.OkHttpClient.Builder().callTimeout(8, java.util.concurrent.TimeUnit.SECONDS).build()

    /** A reply typed into the notification: to the laptop, then into the chat and the notification. */
    class ReplyReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val words = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(KEY)?.toString()?.trim() ?: return
            val agent = intent.getStringExtra("agent") ?: "handsfree"
            val session = intent.getStringExtra("session") ?: ""
            val pending = goAsync()
            Thread {
                try {
                    send(context, agent, session, words)
                } finally {
                    pending.finish()
                }
            }.start()
        }

        /** Within the receiver's ten seconds: an unreachable laptop is "not sent", not a hang. */
        private fun send(context: Context, agent: String, session: String, words: String) {
            val note = text(context, session, words)
            Chats.add(context, session, JSONObject().put("from", "you").put("mine", true)
                .put("text", if (note.isEmpty()) words else "$words  ($note)"))
            post(context, agent, session, sending = true)
        }
    }
}
