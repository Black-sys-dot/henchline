package io.github.ninjatuna007.henchline

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import org.unifiedpush.android.connector.FailedReason
import org.unifiedpush.android.connector.PushService
import org.unifiedpush.android.connector.UnifiedPush
import org.unifiedpush.android.connector.data.PushEndpoint
import org.unifiedpush.android.connector.data.PushMessage

/**
 * An agent rang (`handsfree ring` on the laptop), woken through UnifiedPush: the phone's ntfy app is the
 * distributor, so no Google services. The laptop learns this app's push address at the start of every call
 * (CallService sends it), keeps it, and posts {"agent", "why"} to it; here that becomes an incoming call.
 */
class RingService : PushService() {

    override fun onNewEndpoint(endpoint: PushEndpoint, instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().putString(ENDPOINT, endpoint.url).apply()
        CallService.Bus.service?.send(JSONObject().put("ring_endpoint", endpoint.url).toString())
    }

    override fun onMessage(message: PushMessage, instance: String) {
        val ring = try {
            JSONObject(String(message.content))
        } catch (_: Exception) {
            JSONObject()
        }
        if (ring.has("text")) {  // `handsfree text`: a message, not a call
            Messages.show(this, ring.optString("agent", "handsfree"), ring.optString("session"), ring.optString("text"))
            return
        }
        ring.optJSONObject("file")?.let { file ->  // `handsfree send` while no call was up: kept in that agent's chat too
            val from = IncomingActivity.nameOf(ring.optString("agent"))
            val session = ring.optString("session")
            if (ring.optString("why").isNotEmpty())
                Chats.add(this, session, JSONObject().put("from", from).put("text", ring.optString("why")).put("mine", false))
            Chats.add(this, session, JSONObject().put("from", from).put("file", file))
            Files.notify(this, ring.optString("agent", "handsfree"), ring.optString("why"), file.optString("url"),
                file.optString("name", "file"), file.optString("type", "application/octet-stream"), file.optLong("size"))
            return
        }
        if (CallService.Bus.service != null) return  // on a call already: the agent is heard there
        val reply = Reply.valid(ring.optString("reply"))
        Reply.send(reply, "rang")  // it reached the phone
        ringNow(this, ring.optString("agent", "handsfree"), ring.optString("why"), reply)
    }

    override fun onRegistrationFailed(reason: FailedReason, instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().remove(ENDPOINT).apply()
    }

    override fun onUnregistered(instance: String) {
        getSharedPreferences("call", MODE_PRIVATE).edit().remove(ENDPOINT).apply()
    }

    companion object {
        const val ENDPOINT = "ring_endpoint"
        // "ring", then "calls" until 2026-11-15: a channel made before the app had Do Not Disturb access never gets through
        const val CHANNEL = "calls_v2"
        const val NOTIFICATION = 2
        const val RING_SECONDS = 30L  // then it is a missed call

        /** Ask the phone's ntfy app (or another distributor) for a push address; a no-op once registered. */
        fun register(context: Context) {
            UnifiedPush.tryUseCurrentOrDefaultDistributor(context) { ok ->
                if (ok) UnifiedPush.register(context, "default", "Henchline: your agents ring you", null)
            }
        }

        /** The incoming call. On a locked phone, or with the screen off, Android opens the full-screen ring
         *  (IncomingActivity, which plays the agent's tone and phrase). While the phone is in use it only
         *  shows a banner, which rang silently (2026-11-15): then the banner itself rings, with the agent's
         *  tone over and over, and has Answer and Decline, as a phone app's call does. */
        fun ringNow(context: Context, agent: String, why: String, reply: String? = null, banner: Boolean = false,
                    seconds: Long = RING_SECONDS, quiet: Boolean = false) {
            val manager = context.getSystemService(NotificationManager::class.java)
            val flavour = agent.takeIf { it in IncomingActivity.FLAVOURS } ?: "handsfree"
            val inUse = banner || (context.getSystemService(android.os.PowerManager::class.java).isInteractive &&
                !context.getSystemService(android.app.KeyguardManager::class.java).isKeyguardLocked)
            if (seconds == RING_SECONDS) Ringing.started(context, agent, why, reply)
            retire(manager)
            // quiet: the ring screen is up and rings itself; the notification stays for its buttons, silent
            val channel = if (inUse && !quiet) bannerChannel(context, manager, flavour) else CHANNEL.also {
                manager.createNotificationChannel(NotificationChannel(CHANNEL, "Incoming calls", NotificationManager.IMPORTANCE_HIGH)
                    .apply { setSound(null, null); setBypassDnd(true) })  // IncomingActivity rings, with the agent's own tone
            }
            val screen = Intent(context, IncomingActivity::class.java)
                .putExtra(IncomingActivity.AGENT, agent).putExtra(IncomingActivity.WHY, why)
                .putExtra(IncomingActivity.REPLY, reply)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_NO_USER_ACTION)
            val open = PendingIntent.getActivity(context, 3, screen, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val answer = PendingIntent.getActivity(context, 4, Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ANSWER).putExtra(IncomingActivity.REPLY, reply)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val decline = PendingIntent.getBroadcast(context, 5, Intent(context, RingActions::class.java)
                .setAction(RingActions.DECLINE).putExtra(IncomingActivity.REPLY, reply),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val name = IncomingActivity.nameOf(agent)
            val ring = Notification.Builder(context, channel)
                .setSmallIcon(R.drawable.ic_notify)
                .setContentTitle("$name is calling")
                .setContentText(why.ifEmpty { "Your crew wants to talk." })
                .setCategory(Notification.CATEGORY_CALL)
                .apply { if (!banner && !quiet) setFullScreenIntent(open, true) }  // the screen is, or was, up already
                .setContentIntent(open)
                .setOngoing(true)
                .setTimeoutAfter(seconds * 1000)
                .apply {
                    // A call's own banner, Android 12 and later; Android allows it only with the full-screen ring
                    // (posting it quietly or as a banner without one crashed the ring screen, 2026-11-15)
                    if (android.os.Build.VERSION.SDK_INT >= 31 && !banner && !quiet) {
                        val caller = android.app.Person.Builder().setName(name).setImportant(true)
                            .setIcon(android.graphics.drawable.Icon.createWithResource(context, MainActivity.avatarOf(flavour))).build()
                        setStyle(Notification.CallStyle.forIncomingCall(caller, decline, answer))
                    } else {
                        addAction(Notification.Action.Builder(null, "Decline", decline).build())
                        addAction(Notification.Action.Builder(null, "Answer", answer).build())
                    }
                }
                .build()
            if (inUse && !quiet) ring.flags = ring.flags or Notification.FLAG_INSISTENT  // the tone repeats until answered
            try {
                manager.notify(NOTIFICATION, ring)
            } catch (e: IllegalArgumentException) {  // refused (its style?): a plain one instead, never no ring at all
                android.util.Log.w("Henchline", "ring notification refused, posting a plain one: ${e.message}")
                try {
                    manager.notify(NOTIFICATION, Notification.Builder(context, channel)
                        .setSmallIcon(R.drawable.ic_notify)
                        .setContentTitle("$name is calling")
                        .setContentText(why.ifEmpty { "Your crew wants to talk." })
                        .setCategory(Notification.CATEGORY_CALL)
                        .setContentIntent(open)
                        .setOngoing(true)
                        .setTimeoutAfter(seconds * 1000)
                        .addAction(Notification.Action.Builder(null, "Decline", decline).build())
                        .addAction(Notification.Action.Builder(null, "Answer", answer).build())
                        .build().apply { if (inUse && !quiet) flags = flags or Notification.FLAG_INSISTENT })
                } catch (again: IllegalArgumentException) {
                    android.util.Log.w("Henchline", "plain ring notification refused too: ${again.message}")
                }
            }
        }

        /** A channel per agent whose sound is its ringtone: a banner's sound is its channel's. In Do Not
         *  Disturb the tone plays as an alarm (a ringtone is silenced there), from a channel of its own: a
         *  channel's sound can't change once made. */
        private fun bannerChannel(context: Context, manager: NotificationManager, flavour: String): String {
            val quiet = DoNotDisturb.on(context)
            // "_v2": the tone with its phrases (banner_mix.py) since 2026-11-15; a channel's sound is fixed when made
            val id = if (quiet) "calls_banner_dnd_v2_$flavour" else "calls_banner_v2_$flavour"
            val tone = android.net.Uri.parse("android.resource://${context.packageName}/raw/banner_$flavour")
            val name = "${IncomingActivity.nameOf(flavour)} calling while you use the phone" + if (quiet) " (Do Not Disturb)" else ""
            manager.createNotificationChannel(NotificationChannel(id, name, NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(tone, android.media.AudioAttributes.Builder()
                    // In Do Not Disturb at the alarm volume, unscaled (a channel can't scale its sound): the user
                    // found the banner the gentler of the two there (2026-11-15); the ring screen is scaled to match
                    .setUsage(if (quiet) android.media.AudioAttributes.USAGE_ALARM else android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                setBypassDnd(true)  // kept only when the app has Do Not Disturb access (DoNotDisturb.ask)
            })
            return id
        }

        /** The channels from before rings could get through Do Not Disturb. */
        private fun retire(manager: NotificationManager) {
            val old = IncomingActivity.FLAVOURS.flatMap { listOf("ring_banner_$it", "calls_banner_$it", "calls_banner_dnd_$it") }
            for (id in listOf("ring", "calls") + old) manager.deleteNotificationChannel(id)
        }
    }
}
